import json
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
from time import perf_counter
from uuid import uuid4


def _text(value: str, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{label} must contain 1–{limit} characters")
    return value.strip()


def _json_snapshot(value, limit: int):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > limit:
            raise ValueError("Payload exceeds size limit")
        return json.loads(encoded)
    except (ValueError, TypeError, RecursionError):
        raise ValueError(f"Payload must be finite JSON of at most {limit} bytes") from None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Trace:
    def __init__(
        self, client, name: str, *, external_id: str | None = None, attributes: dict | None = None
    ):
        self._client = client
        self._name = _text(name, "name", 120)
        self._external_id = _text(
            external_id if external_id is not None else str(uuid4()), "external_id", 255
        )
        if attributes is not None and not isinstance(attributes, dict):
            raise ValueError("attributes must be an object")
        self._attributes = _json_snapshot(attributes if attributes is not None else {}, 16 * 1024)
        self._current_span: ContextVar = ContextVar(f"tracegrade-span-{uuid4()}", default=None)
        self._id: str | None = None
        self._started_at: datetime | None = None
        self._ended_at: datetime | None = None
        self._status = "running"
        self._entered = False
        self._active = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def external_id(self) -> str:
        return self._external_id

    @property
    def id(self) -> str | None:
        return self._id

    @property
    def status(self) -> str:
        return self._status

    def _require_active(self):
        if not self._active:
            raise RuntimeError("Instrumentation context is not active")

    def set_attribute(self, key: str, value) -> None:
        self._require_active()
        if not isinstance(key, str):
            raise ValueError("Attribute keys must be strings")
        self._attributes = _json_snapshot({**self._attributes, key: value}, 16 * 1024)

    def span(
        self,
        name: str,
        *,
        kind: str = "operation",
        input=None,
        model: str | None = None,
        external_id: str | None = None,
        attributes: dict | None = None,
    ):
        self._require_active()
        return Span(
            self,
            name,
            kind=kind,
            input=input,
            model=model,
            external_id=external_id,
            attributes=attributes,
        )

    def _payload(self) -> dict:
        return {
            "external_id": self.external_id,
            "name": self.name,
            "status": self._status,
            "started_at": self._started_at.isoformat(),
            "ended_at": self._ended_at.isoformat() if self._ended_at else None,
            "attributes": self._attributes,
        }

    def __enter__(self):
        if self._entered:
            raise RuntimeError("A trace/span context can only be entered once")
        self._entered = True
        self._started_at = _now()
        self._id = self._client._send("traces", self._payload())
        self._active = True
        return self

    def __exit__(self, _exc_type, exc, _traceback):
        self._require_active()
        self._ended_at = max(_now(), self._started_at)
        self._status = "error" if exc is not None else "ok"
        self._active = False
        if self._id is not None:
            self._client._send("traces", self._payload(), preserve_exception=exc is not None)
        return False


class Span(Trace):
    def __init__(
        self,
        trace: Trace,
        name: str,
        *,
        kind: str = "operation",
        input=None,
        model: str | None = None,
        external_id: str | None = None,
        attributes: dict | None = None,
    ):
        super().__init__(trace._client, name, external_id=external_id, attributes=attributes)
        if kind not in {"operation", "llm"}:
            raise ValueError("kind must be operation or llm")
        self._trace = trace
        self._kind = kind
        self._input = _json_snapshot(input, 64 * 1024)
        self._output = None
        self._model = _text(model, "model", 120) if model is not None else None
        self._parent: Span | None = None
        self._token = None
        self._started_clock = None
        self._latency_ms = None
        self._error_message = None
        self._usage = dict.fromkeys(
            ["prompt_tokens", "completion_tokens", "total_tokens", "estimated_cost_usd"]
        )

    def span(self, name: str, **kwargs):
        self._require_active()
        if self._trace._current_span.get() is not self:
            raise RuntimeError("Only the current span can create a child")
        return self._trace.span(name, **kwargs)

    def set_input(self, value) -> None:
        self._require_active()
        self._input = _json_snapshot(value, 64 * 1024)

    def set_output(self, value) -> None:
        self._require_active()
        self._output = _json_snapshot(value, 64 * 1024)

    def set_usage(
        self,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        estimated_cost_usd=None,
    ) -> None:
        self._require_active()
        if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
            # Validate inputs before summing so bools/floats cannot become integers.
            for value in [prompt_tokens, completion_tokens]:
                if type(value) is not int or not 0 <= value <= 2**31 - 1:
                    raise ValueError("Token counts must be non-negative 32-bit integers")
            total_tokens = prompt_tokens + completion_tokens
        values = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        if any(
            value is not None and (type(value) is not int or not 0 <= value <= 2**31 - 1)
            for value in values.values()
        ):
            raise ValueError("Token counts must be non-negative 32-bit integers")
        cost = None
        if estimated_cost_usd is not None:
            try:
                cost = Decimal(str(estimated_cost_usd))
                digits = cost.as_tuple().digits
                exponent = cost.as_tuple().exponent
                if cost.is_finite() and cost != 0:
                    while digits and digits[-1] == 0:
                        digits, exponent = digits[:-1], exponent + 1
                if (
                    not cost.is_finite()
                    or not 0 <= cost < Decimal("10000000000")
                    or (cost != 0 and exponent < -10)
                ):
                    raise ValueError("Invalid cost")
                with localcontext(Context(prec=28)):
                    cost = cost.quantize(Decimal("0.0000000001"))
            except (InvalidOperation, ValueError):
                raise ValueError(
                    "Cost must be non-negative with at most 10 decimal places and 10 integer digits"
                ) from None
        self._usage = {**values, "estimated_cost_usd": str(cost) if cost is not None else None}

    def _payload(self) -> dict:
        return {
            **super()._payload(),
            "parent_span_id": self._parent.id if self._parent else None,
            "kind": self._kind,
            "input": self._input,
            "output": self._output,
            "model": self._model,
            **self._usage,
            "latency_ms": self._latency_ms,
            "error_message": self._error_message,
        }

    def __enter__(self):
        self._trace._require_active()
        if self._entered:
            raise RuntimeError("A trace/span context can only be entered once")
        self._entered = True
        self._parent = self._trace._current_span.get()
        if self._parent is not None:
            self._parent._require_active()
        self._started_at = _now()
        if self._trace.id is not None and (self._parent is None or self._parent.id is not None):
            self._id = self._client._send(f"traces/{self._trace.id}/spans", self._payload())
        self._started_clock = perf_counter()
        self._token = self._trace._current_span.set(self)
        self._active = True
        return self

    def __exit__(self, _exc_type, exc, _traceback):
        self._require_active()
        self._trace._current_span.reset(self._token)
        self._active = False
        self._ended_at = max(_now(), self._started_at)
        self._latency_ms = min(
            2**31 - 1, max(0, int((perf_counter() - self._started_clock) * 1000))
        )
        self._status = "error" if exc is not None else "ok"
        if exc is not None:
            try:
                self._error_message = str(exc)[:4096]
            except Exception:
                self._error_message = type(exc).__name__
        if self._id is not None:
            self._client._send(
                f"traces/{self._trace.id}/spans",
                self._payload(),
                preserve_exception=exc is not None,
            )
        return False
