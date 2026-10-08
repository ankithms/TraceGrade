import json
import math
from collections import deque
from uuid import UUID

import httpx


class TraceGradeError(Exception):
    """A telemetry delivery failure, without request secrets or model payloads."""

    def __init__(self, message: str, *, code: str, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class TraceGrade:
    """Synchronous instrumentation client with immediate, best-effort delivery."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "http://localhost:8000/api/v1",
        timeout: float = 5.0,
        raise_on_error: bool = False,
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
        self._raise_on_error = raise_on_error
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
        """The last 100 delivery errors; model content and keys are never included."""
        return tuple(self._errors)

    @property
    def closed(self) -> bool:
        return self._http.is_closed

    def trace(self, name: str, *, external_id: str | None = None, attributes: dict | None = None):
        from tracegrade.tracing import Trace

        if self.closed:
            raise RuntimeError("TraceGrade client is closed")
        return Trace(self, name, external_id=external_id, attributes=attributes)

    def _post(self, path: str, payload: dict) -> str:
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
        except httpx.RequestError:
            raise TraceGradeError("TraceGrade request failed", code="transport_error") from None
        if response.status_code not in (200, 201):
            raise TraceGradeError(
                f"TraceGrade API returned HTTP {response.status_code}",
                code="http_error",
                status_code=response.status_code,
            )
        try:
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("id"), str):
                raise ValueError("Missing resource ID")
            resource_id = str(UUID(data["id"]))
            if data["external_id"] != payload["external_id"]:
                raise ValueError("Mismatched operation")
        except (ValueError, TypeError, KeyError):
            raise TraceGradeError(
                "Invalid TraceGrade API response", code="invalid_response"
            ) from None
        return resource_id

    def _send(self, path: str, payload: dict, *, preserve_exception: bool = False) -> str | None:
        try:
            return self._post(path, payload)
        except TraceGradeError as error:
            self._errors.append(error)
            if self._raise_on_error and not preserve_exception:
                raise
            return None

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        if self.closed:
            raise RuntimeError("TraceGrade client is closed")
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()
        return False
