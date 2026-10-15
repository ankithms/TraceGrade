import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from tracegrade_evaluators.config import (
    EvaluatorType,
    GeminiJudgeConfig,
    TextConfig,
    validate_config,
)


class EvaluationError(Exception):
    """Safe, stable failure details for a future workflow retry policy."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class EvaluationResult:
    score: float
    passed: bool
    rationale: str | None = None


def reject_constant(value):
    raise ValueError("Non-finite JSON number")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_json(value):
    if isinstance(value, str):
        return json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_object)
    return value


def json_equal(left, right):
    # JSON booleans must not compare equal to numbers (Python True == 1).
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def validate_fixture(value):
    # Bound depth and enforce JSON types before recursive serialization/comparison.
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 64:
            raise ValueError("Fixture exceeds 64 levels of nesting")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and not isinstance(item, str | int | float | bool):
            raise ValueError("Fixture must be JSON")
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > 64 * 1024:
        raise ValueError("Fixture too large")


async def evaluate(
    kind: EvaluatorType | str,
    config: dict,
    *,
    output: Any,
    expected_output: Any = None,
    input: Any = None,
    gemini_api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> EvaluationResult:
    """Score one output; raise EvaluationError for invalid inputs/provider failures.

    Gemini makes one bounded request. Retry orchestration belongs to Temporal.
    An injected client remains owned by the caller (also supports offline tests).
    """
    try:
        kind = EvaluatorType(kind)
        settings = validate_config(kind, config)
    except (ValueError, TypeError, ValidationError) as error:
        raise EvaluationError("invalid_configuration", "Invalid evaluator configuration") from error
    try:
        for value in (input, output, expected_output):
            validate_fixture(value)
    except (ValueError, TypeError, RecursionError) as error:
        raise EvaluationError("invalid_input", "Inputs must be bounded, finite JSON") from error

    if isinstance(settings, GeminiJudgeConfig):
        from tracegrade_evaluators.gemini import judge

        return await judge(settings, input, output, expected_output, gemini_api_key, client)
    if isinstance(settings, TextConfig):
        if not isinstance(output, str) or not isinstance(expected_output, str):
            raise EvaluationError(
                "invalid_input", "Text evaluators require string outputs and references"
            )
        if settings.strip_whitespace:
            output, expected_output = output.strip(), expected_output.strip()
        if not settings.case_sensitive:
            output, expected_output = output.casefold(), expected_output.casefold()
        if kind == EvaluatorType.CONTAINS:
            if not expected_output:
                raise EvaluationError("invalid_input", "Contains requires a non-empty reference")
            passed = expected_output in output
        else:
            passed = output == expected_output
    else:
        try:
            expected = parse_json(expected_output)
            validate_fixture(expected)
        except (ValueError, TypeError, RecursionError) as error:
            raise EvaluationError("invalid_input", "Reference is not valid JSON") from error
        try:
            actual = parse_json(output)
            validate_fixture(actual)
        except (ValueError, TypeError, RecursionError):
            return EvaluationResult(0.0, False, "Output is not valid JSON")
        passed = json_equal(actual, expected)
    return EvaluationResult(float(passed), passed)
