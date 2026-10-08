import json
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import RLock
from time import sleep
from uuid import UUID

import httpx

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class TraceGradeError(Exception):
    """A telemetry delivery failure, without request secrets or model payloads."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass(eq=False)
class _Completion:
    trace_id: str
    span_id: str
    payload: dict
    exhausted: bool = False


class TraceGrade:
    """Synchronous instrumentation with bounded completion batching and retries."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "http://localhost:8000/api/v1",
        timeout: float = 5.0,
        raise_on_error: bool = False,
        batch_size: int = 100,
        max_pending_spans: int = 1000,
        max_retries: int = 2,
        retry_backoff: float = 0.1,
        max_retry_delay: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("A project API key is required")
        if any(ord(character) < 32 or ord(character) == 127 for character in api_key):
            raise ValueError("API key must not contain control characters")
        url = httpx.URL(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "base_url must be an HTTP(S) API URL without credentials, query or fragment"
            )
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        for label, value, minimum, maximum in [
            ("batch_size", batch_size, 1, 100),
            ("max_pending_spans", max_pending_spans, 1, 10000),
            ("max_retries", max_retries, 0, 10),
        ]:
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{label} must be an integer between {minimum} and {maximum}")
        if not all(
            math.isfinite(value) and value >= 0 for value in [retry_backoff, max_retry_delay]
        ):
            raise ValueError("Retry delays must be finite and non-negative")
        self._raise_on_error = raise_on_error
        self._batch_size = batch_size
        self._max_pending = max_pending_spans
        self._max_retries = max_retries
        self._backoff = retry_backoff
        self._max_delay = max_retry_delay
        self._lock = RLock()
        self._pending: deque[_Completion] = deque()
        self._errors: deque[TraceGradeError] = deque(maxlen=100)
        self._http = httpx.Client(
            base_url=str(url).rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    @property
    def delivery_errors(self) -> tuple[TraceGradeError, ...]:
        with self._lock:
            return tuple(self._errors)

    @property
    def pending_spans(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def closed(self) -> bool:
        return self._http.is_closed

    def trace(self, name: str, *, external_id: str | None = None, attributes: dict | None = None):
        from tracegrade.tracing import Trace

        if self.closed:
            raise RuntimeError("TraceGrade client is closed")
        return Trace(self, name, external_id=external_id, attributes=attributes)

    def _remember(self, error: TraceGradeError):
        with self._lock:
            self._errors.append(error)

    def _retry_after(self, raw: str | None) -> float | None:
        if raw is None:
            return None
        try:
            seconds = float(raw)
        except ValueError:
            try:
                date = parsedate_to_datetime(raw)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                seconds = (date - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return None
        return max(0, min(seconds, self._max_delay)) if math.isfinite(seconds) else None

    def _pause(self, attempt: int, error: TraceGradeError):
        delay = (
            error.retry_after
            if error.retry_after is not None
            else min(self._max_delay, self._backoff * 2**attempt)
        )
        if delay:
            sleep(delay)

    def _request_once(self, path: str, payload: dict):
        if self.closed:
            raise TraceGradeError("TraceGrade client is closed", code="closed_client")
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError):
            raise TraceGradeError(
                "Telemetry must contain finite JSON values", code="invalid_payload"
            ) from None
        try:
            response = self._http.post(
                path, content=body, headers={"Content-Type": "application/json"}
            )
        except httpx.RequestError as error:
            retryable = isinstance(
                error, httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError
            )
            raise TraceGradeError(
                "TraceGrade request failed", code="transport_error", retryable=retryable
            ) from None
        if response.status_code not in (200, 201):
            raise TraceGradeError(
                f"TraceGrade API returned HTTP {response.status_code}",
                code="http_error",
                status_code=response.status_code,
                retryable=response.status_code in RETRYABLE_STATUS,
                retry_after=self._retry_after(response.headers.get("Retry-After")),
            )
        try:
            return response.json()
        except ValueError:
            raise TraceGradeError(
                "Invalid TraceGrade API response", code="invalid_response"
            ) from None

    def _resource_id(self, data, payload: dict) -> str:
        try:
            if not isinstance(data, dict) or not isinstance(data.get("id"), str):
                raise ValueError("Missing resource ID")
            resource_id = str(UUID(data["id"]))
            if data["external_id"] != payload["external_id"]:
                raise ValueError("Mismatched operation")
            return resource_id
        except (ValueError, TypeError, KeyError):
            raise TraceGradeError(
                "Invalid TraceGrade API response", code="invalid_response"
            ) from None

    def _post(self, path: str, payload: dict) -> str:
        for attempt in range(self._max_retries + 1):
            try:
                return self._resource_id(self._request_once(path, payload), payload)
            except TraceGradeError as error:
                if not error.retryable or attempt == self._max_retries:
                    raise
                self._pause(attempt, error)
        raise AssertionError("Retry loop exited unexpectedly")

    def _send(self, path: str, payload: dict, *, preserve_exception: bool = False) -> str | None:
        try:
            return self._post(path, payload)
        except TraceGradeError as error:
            self._remember(error)
            if self._raise_on_error and not preserve_exception:
                raise
            return None

    def _batch_results(self, data, entries: list[_Completion]):
        # Validate the complete partition before acknowledging anything. A malformed
        # response must never be mistaken for a successful acknowledgement.
        try:
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("accepted"), list)
                or not isinstance(data.get("errors"), list)
            ):
                raise ValueError("Invalid result arrays")
            outcomes = {}
            for accepted, items in [(True, data["accepted"]), (False, data["errors"])]:
                for item in items:
                    index = item["index"]
                    if type(index) is not int or not 0 <= index < len(entries) or index in outcomes:
                        raise ValueError("Invalid item index")
                    if accepted:
                        resource_id = self._resource_id(item, entries[index].payload)
                        if (
                            resource_id != entries[index].span_id
                            or type(item.get("created")) is not bool
                        ):
                            raise ValueError("Invalid acknowledgement")
                        outcomes[index] = None
                    else:
                        status = item["status_code"]
                        if type(status) is not int or not 400 <= status < 600:
                            raise ValueError("Invalid error status")
                        outcomes[index] = TraceGradeError(
                            f"TraceGrade batch item returned HTTP {status}",
                            code="batch_item_error",
                            status_code=status,
                            retryable=status in RETRYABLE_STATUS,
                        )
            if len(outcomes) != len(entries):
                raise ValueError("Missing item results")
            return [outcomes[index] for index in range(len(entries))]
        except (ValueError, TypeError, KeyError, TraceGradeError):
            raise TraceGradeError(
                "Invalid TraceGrade batch response", code="invalid_response"
            ) from None

    def _deliver_entries(self, entries: list[_Completion]) -> list[TraceGradeError]:
        failures = []
        remaining = entries
        for attempt in range(self._max_retries + 1):
            try:
                if self._batch_size == 1:
                    data = self._request_once(
                        f"traces/{remaining[0].trace_id}/spans", remaining[0].payload
                    )
                    if self._resource_id(data, remaining[0].payload) != remaining[0].span_id:
                        raise TraceGradeError(
                            "Invalid TraceGrade API response", code="invalid_response"
                        )
                    outcomes = [None]
                else:
                    data = self._request_once(
                        f"traces/{remaining[0].trace_id}/spans/batch",
                        {"spans": [item.payload for item in remaining]},
                    )
                    outcomes = self._batch_results(data, remaining)
            except TraceGradeError as error:
                if error.retryable and attempt < self._max_retries:
                    self._pause(attempt, error)
                    continue
                self._remember(error)
                for entry in remaining:
                    if error.retryable:
                        entry.exhausted = True
                    else:
                        self._pending.remove(entry)
                return [*failures, error]
            retry = []
            for entry, error in zip(remaining, outcomes, strict=True):
                if error is None:
                    self._pending.remove(entry)
                elif error.retryable and attempt < self._max_retries:
                    retry.append(entry)
                else:
                    self._remember(error)
                    failures.append(error)
                    if error.retryable:
                        entry.exhausted = True
                    else:
                        self._pending.remove(entry)
            if not retry:
                return failures
            remaining = retry
            self._pause(
                attempt,
                TraceGradeError("Retry batch items", code="batch_item_error", retryable=True),
            )
        raise AssertionError("Batch retry loop exited unexpectedly")

    def _flush(
        self,
        trace_id: str | None = None,
        *,
        preserve_exception: bool = False,
        retry_exhausted: bool = False,
    ) -> bool:
        with self._lock:
            groups = {}
            deferred = False
            for entry in self._pending:
                if trace_id is None or entry.trace_id == trace_id:
                    if entry.exhausted and not retry_exhausted:
                        deferred = True
                    else:
                        entry.exhausted = False
                        groups.setdefault(entry.trace_id, []).append(entry)
            failures = []
            for entries in groups.values():
                for start in range(0, len(entries), self._batch_size):
                    failures.extend(
                        self._deliver_entries(entries[start : start + self._batch_size])
                    )
            if failures and self._raise_on_error and not preserve_exception:
                raise failures[0]
            return not failures and not deferred

    def flush(self) -> bool:
        """Deliver queued completions; return false if any delivery failed."""
        if self.closed:
            raise RuntimeError("TraceGrade client is closed")
        return self._flush(retry_exhausted=True)

    def _complete_span(
        self, trace_id: str, span_id: str, payload: dict, *, preserve_exception: bool = False
    ):
        # Freeze final metadata so retries cannot change an immutable operation.
        snapshot = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        with self._lock:
            if self.closed:
                error = TraceGradeError("TraceGrade client is closed", code="closed_client")
                self._remember(error)
                if self._raise_on_error and not preserve_exception:
                    raise error
                return
            pressure_error = None
            if len(self._pending) >= self._max_pending:
                previous_error = self._errors[-1] if self._errors else None
                self._flush(trace_id, preserve_exception=True)
                if self._errors and self._errors[-1] is not previous_error:
                    pressure_error = self._errors[-1]
            if len(self._pending) >= self._max_pending:
                error = TraceGradeError("Completion queue is full", code="queue_full")
                self._remember(error)
                if self._raise_on_error and not preserve_exception:
                    raise error
                return
            self._pending.append(_Completion(trace_id, span_id, snapshot))
            if len(self._pending) >= min(self._batch_size, self._max_pending):
                self._flush(
                    trace_id, preserve_exception=preserve_exception or pressure_error is not None
                )
            if pressure_error is not None and self._raise_on_error and not preserve_exception:
                raise pressure_error

    def _finish_trace(self, trace_id: str, payload: dict, *, preserve_exception: bool = False):
        try:
            self._flush(trace_id, preserve_exception=preserve_exception)
        except TraceGradeError:
            self._send("traces", payload, preserve_exception=True)
            raise
        self._send("traces", payload, preserve_exception=preserve_exception)

    def _close(self, preserve_exception: bool):
        with self._lock:
            if self.closed:
                return
            try:
                self._flush(preserve_exception=preserve_exception)
            finally:
                self._http.close()

    def close(self) -> None:
        self._close(False)

    def __enter__(self):
        if self.closed:
            raise RuntimeError("TraceGrade client is closed")
        return self

    def __exit__(self, _exc_type, exc, _traceback):
        self._close(exc is not None)
        return False
