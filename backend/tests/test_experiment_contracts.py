from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tracegrade_experiments.contracts import (
    ApplicationRequest,
    ApplicationResponse,
    CaseSnapshot,
    DatasetSnapshot,
    EvaluatorSnapshot,
    RunConfig,
)


@pytest.mark.parametrize(
    "values",
    [
        {"target": "https://example.com/run"},
        {"target": "../runner"},
        {"target": "package:run"},
        {"target": ""},
        {"target": "reviewer", "url": "http://localhost"},
        {"target": "reviewer", "headers": {"Authorization": "secret"}},
        {"target": "reviewer", "max_concurrency": 0},
        {"target": "reviewer", "max_concurrency": 21},
        {"target": "reviewer", "max_concurrency": "5"},
        {"target": "reviewer", "max_concurrency": True},
        {"target": "reviewer", "application_timeout_seconds": 0},
        {"target": "reviewer", "application_timeout_seconds": 301},
        {"target": "reviewer", "schema_version": 2},
        {"target": "reviewer", "schema_version": True},
        {"target": "reviewer", "parameters": {"temperature": float("nan")}},
        {"target": "reviewer", "parameters": {"prompt": "é" * 8192}},
    ],
)
def test_reject_invalid_execution_configuration(values):
    with pytest.raises(ValidationError):
        RunConfig.model_validate(values)


def test_application_request_round_trip_and_stable_delivery_key():
    request = ApplicationRequest(
        experiment_id=uuid4(),
        test_case_id=uuid4(),
        version_label="v1",
        input={"diff": "λ", "context": None},
        parameters={"temperature": 0},
    )
    restored = ApplicationRequest.model_validate_json(request.model_dump_json())
    assert restored == request
    assert restored.idempotency_key == request.idempotency_key
    changed_case = request.model_copy(update={"test_case_id": uuid4()})
    changed_run = request.model_copy(update={"experiment_id": uuid4()})
    assert (
        len({request.idempotency_key, changed_case.idempotency_key, changed_run.idempotency_key})
        == 3
    )
    # Header data is derived, never a caller-supplied body field.
    assert "idempotency_key" not in request.model_dump()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"output": "x", "total_tokens": True},
        {"output": "x", "prompt_tokens": -1},
        {"output": "x", "completion_tokens": "5"},
        {"output": "x", "total_tokens": 2**31},
        {"output": "x", "estimated_cost_usd": "NaN"},
        {"output": "x", "estimated_cost_usd": "Infinity"},
        {"output": "x", "estimated_cost_usd": "-0.1"},
        {"output": "x", "estimated_cost_usd": "0.00000000001"},
        {"output": "é" * 32768},
        {"output": {"x": float("inf")}},
        {"output": "x", "schema_version": "1"},
    ],
)
def test_reject_invalid_application_response(payload):
    with pytest.raises(ValidationError):
        ApplicationResponse.model_validate(payload)


def test_output_null_and_unknown_metrics_are_valid():
    response = ApplicationResponse.model_validate({"output": None})
    assert response.output is None and response.total_tokens is None
    with pytest.raises(ValidationError):
        ApplicationResponse.model_validate({})
    response = ApplicationResponse.model_validate(
        {
            "output": {"safe": False},
            "model": "example-model",
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
            "estimated_cost_usd": "0.0000012345",
        }
    )
    assert response.estimated_cost_usd == Decimal("0.0000012345")
    assert ApplicationResponse.model_validate_json(response.model_dump_json()) == response


def test_snapshot_contracts_retain_inputs_and_normalize_evaluator_configuration():
    case = CaseSnapshot(name="Security", input=None, metadata={"category": "security"})
    assert CaseSnapshot.model_validate_json(case.model_dump_json()) == case
    assert case.expected_output is None
    snapshot = EvaluatorSnapshot(name="Exact", type="exact_match", config={"case_sensitive": False})
    assert snapshot.config == {"case_sensitive": False, "strip_whitespace": False}
    # Reference-free Gemini evaluations remain supported by nullable case references.
    judge = EvaluatorSnapshot(
        name="Correctness",
        type="gemini_judge",
        config={
            "model": "gemini-test",
            "rubric": "Assess correctness based on input and output",
        },
    )
    assert judge.config["pass_threshold"] == 0.5


@pytest.mark.parametrize(
    "model,payload",
    [
        (DatasetSnapshot, {"name": " "}),
        (CaseSnapshot, {"name": "Case"}),
        (CaseSnapshot, {"name": "Case", "input": "x", "metadata": {"x": "a" * 16384}}),
        (CaseSnapshot, {"name": "Case", "input": "x", "expected_output": "é" * 32768}),
        (EvaluatorSnapshot, {"name": "Exact", "type": "exact_match", "config": {"rubric": "x"}}),
        (EvaluatorSnapshot, {"name": "Judge", "type": "gemini_judge", "config": {}}),
    ],
)
def test_snapshot_validation(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_depth_limit_applies_to_application_payloads():
    payload = []
    for _ in range(65):
        payload = [payload]
    with pytest.raises(ValidationError):
        ApplicationResponse(output=payload)
