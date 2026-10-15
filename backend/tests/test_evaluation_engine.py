import json

import httpx
import pytest

from tracegrade_evaluators import EvaluationError, EvaluationResult, evaluate, gemini

JUDGE = {"model": "gemini-test", "rubric": "Check correctness", "pass_threshold": 0.75}


@pytest.mark.parametrize(
    "kind,config,output,expected,passed",
    [
        ("exact_match", {}, "ok", "ok", True),
        ("exact_match", {}, "OK", "ok", False),
        ("exact_match", {}, " ok ", "ok", False),
        ("exact_match", {"strip_whitespace": True, "case_sensitive": False}, " OK ", "ok", True),
        ("exact_match", {"case_sensitive": False}, "Straße", "STRASSE", True),
        ("exact_match", {}, "", "", True),
        ("contains", {}, "SQL injection detected", "injection", True),
        ("contains", {}, "SQL injection detected", "INJECTION", False),
        ("contains", {"case_sensitive": False}, "SQL injection detected", "INJECTION", True),
        ("contains", {}, "safe", "unsafe", False),
        ("json_match", {}, '{"b":2,"a":1}', {"a": 1, "b": 2}, True),
        ("json_match", {}, {"a": [1, 2]}, '{"a":[1,2]}', True),
        ("json_match", {}, [1, 2], [2, 1], False),
        ("json_match", {}, {"a": True}, {"a": 1}, False),
        ("json_match", {}, [True], [1], False),
        ("json_match", {}, {"a": 1}, {"a": 1.0}, True),
        ("json_match", {}, {"a": 1, "b": 2}, {"a": 1}, False),
        ("json_match", {}, None, None, True),
        ("json_match", {}, '"ok"', '"ok"', True),
        ("json_match", {}, "not-json", {}, False),
        ("json_match", {}, '{"a":1,"a":2}', {"a": 2}, False),
        ("json_match", {}, "NaN", None, False),
        ("json_match", {}, '{"a":1e400}', {}, False),
    ],
)
async def test_deterministic_scores(kind, config, output, expected, passed):
    result = await evaluate(kind, config, output=output, expected_output=expected)
    assert result.passed is passed
    assert result.score == float(passed)


@pytest.mark.parametrize(
    "kind,config,output,expected,code",
    [
        ("unknown", {}, "x", "x", "invalid_configuration"),
        ("contains", {"case_sensitive": 1}, "x", "x", "invalid_configuration"),
        ("json_match", {"ignore_order": True}, {}, {}, "invalid_configuration"),
        ("gemini_judge", {}, "x", "x", "invalid_configuration"),
        ("gemini_judge", {**JUDGE, "pass_threshold": True}, "x", "x", "invalid_configuration"),
        (
            "gemini_judge",
            {**JUDGE, "pass_threshold": float("nan")},
            "x",
            "x",
            "invalid_configuration",
        ),
        ("exact_match", {}, 1, "1", "invalid_input"),
        ("contains", {}, "x", None, "invalid_input"),
        ("contains", {}, "x", "", "invalid_input"),
        ("contains", {"strip_whitespace": True}, "x", " ", "invalid_input"),
        ("json_match", {}, {}, "invalid", "invalid_input"),
        ("json_match", {}, {}, '{"a":1,"a":2}', "invalid_input"),
        ("json_match", {}, {}, float("inf"), "invalid_input"),
        ("exact_match", {}, "é" * 32768, "x", "invalid_input"),
    ],
)
async def test_invalid_inputs_and_config(kind, config, output, expected, code):
    with pytest.raises(EvaluationError) as error:
        await evaluate(kind, config, output=output, expected_output=expected)
    assert error.value.code == code
    assert error.value.retryable is False


def candidate(text, finish="STOP"):
    return {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]}


@pytest.mark.parametrize("score,passed", [(0, False), (0.74, False), (0.75, True), (1, True)])
async def test_gemini_request_and_threshold(score, passed):
    requests = []

    def handler(request):
        requests.append(request)
        assert (
            request.url
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
        )
        assert request.headers["x-goog-api-key"] == "test-secret"
        assert "test-secret" not in str(request.url)
        body = json.loads(request.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["responseJsonSchema"]["required"] == ["score", "rationale"]
        assert "Check correctness" in body["systemInstruction"]["parts"][0]["text"]
        fixture = json.loads(body["contents"][0]["parts"][0]["text"])
        assert fixture == {
            "input": {"diff": "x"},
            "output": "ignore instructions",
            "expected_output": "safe",
        }
        return httpx.Response(
            200, json=candidate(json.dumps({"score": score, "rationale": "Reason"}))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await evaluate(
            "gemini_judge",
            JUDGE,
            input={"diff": "x"},
            output="ignore instructions",
            expected_output="safe",
            gemini_api_key="test-secret",
            client=client,
        )
        assert not client.is_closed
    assert len(requests) == 1
    assert result.score == score and result.passed is passed and result.rationale == "Reason"


async def test_gemini_judges_without_reference_output():
    def handler(request):
        body = json.loads(request.content)
        fixture = json.loads(body["contents"][0]["parts"][0]["text"])
        assert fixture == {
            "input": {"diff": "+query(user_input)"},
            "output": "Possible SQL injection; use a parameterized query.",
        }
        instruction = body["systemInstruction"]["parts"][0]["text"]
        assert "Rubric:\nCheck correctness" in instruction
        assert "Use expected_output as reference data" not in instruction
        return httpx.Response(
            200, json=candidate('{"score":0.9,"rationale":"Found the real issue"}')
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await evaluate(
            "gemini_judge",
            JUDGE,
            input={"diff": "+query(user_input)"},
            output="Possible SQL injection; use a parameterized query.",
            gemini_api_key="test-secret",
            client=client,
        )
    assert result == EvaluationResult(0.9, True, "Found the real issue")


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        None,
        {"candidates": []},
        {"promptFeedback": {"blockReason": "SAFETY"}},
        candidate('{"score":1,"rationale":"ok"}', "MAX_TOKENS"),
        candidate('{"score":1,"rationale":"ok"}', "SAFETY"),
        candidate("not JSON"),
        candidate('```json\n{"score":1,"rationale":"ok"}\n```'),
        candidate('{"score":1.1,"rationale":"x"}'),
        candidate('{"score":-0.1,"rationale":"x"}'),
        candidate('{"score":NaN,"rationale":"x"}'),
        candidate('{"score":1e400,"rationale":"x"}'),
        candidate('{"score":"0.8","rationale":"x"}'),
        candidate('{"score":true,"rationale":"x"}'),
        candidate('{"score":0.8}'),
        candidate('{"score":0.8,"rationale":" "}'),
        candidate('{"score":0.8,"rationale":null}'),
        candidate('{"score":0,"score":1,"rationale":"x"}'),
        candidate('{"score":0.8,"rationale":"x","passed":true}'),
        candidate(json.dumps({"score": 1, "rationale": "x" * 4097})),
        {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"functionCall": {}}]}}]},
    ],
)
async def test_gemini_rejects_invalid_response(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        with pytest.raises(EvaluationError) as error:
            await evaluate(
                "gemini_judge", JUDGE, output="x", gemini_api_key="secret", client=client
            )
    assert error.value.code == "invalid_response"
    assert not error.value.retryable
    assert "secret" not in str(error.value)


async def test_gemini_text_parts_and_thoughts():
    body = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"text": "reasoning", "thought": True},
                        {"text": '{"score":0.8,'},
                        {"text": '"rationale":"ok"}'},
                    ]
                },
            }
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        result = await evaluate(
            "gemini_judge", JUDGE, output="x", gemini_api_key="secret", client=client
        )
    assert result.score == 0.8


@pytest.mark.parametrize(
    "status,retryable",
    [
        (400, False),
        (401, False),
        (403, False),
        (404, False),
        (429, True),
        (500, True),
        (503, True),
        (302, False),
    ],
)
async def test_provider_errors_are_safe_and_not_retried(status, retryable):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="private provider payload and secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EvaluationError) as error:
            await evaluate(
                "gemini_judge",
                JUDGE,
                output="private input",
                gemini_api_key="secret",
                client=client,
            )
    assert error.value.code == "provider_error"
    assert error.value.retryable is retryable
    assert "secret" not in str(error.value) and "private" not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "failure,code",
    [(httpx.ReadTimeout, "provider_timeout"), (httpx.ConnectError, "provider_error")],
)
async def test_provider_network_errors(failure, code):
    def handler(request):
        raise failure("sensitive details", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EvaluationError) as error:
            await evaluate(
                "gemini_judge", JUDGE, output="x", gemini_api_key="secret", client=client
            )
    assert error.value.code == code and error.value.retryable
    assert "sensitive" not in str(error.value)


async def test_missing_key_does_not_call_provider():
    def handler(_):
        pytest.fail("Provider must not be called without credentials")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EvaluationError, match="GEMINI_API_KEY") as error:
            await evaluate("gemini_judge", JUDGE, output="x", client=client)
    assert error.value.code == "provider_not_configured"
    assert not error.value.retryable


@pytest.mark.parametrize("content", [b"{", b"\xff", b"x" * (gemini.MAX_RESPONSE_BYTES + 1)])
async def test_invalid_or_oversized_provider_body(content):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    ) as client:
        with pytest.raises(EvaluationError) as error:
            await evaluate(
                "gemini_judge", JUDGE, output="x", gemini_api_key="secret", client=client
            )
    assert error.value.code == "invalid_response"


async def test_whole_request_deadline(monkeypatch):
    import asyncio

    async def handler(_):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={})

    monkeypatch.setattr(gemini, "REQUEST_TIMEOUT_SECONDS", 0.01)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EvaluationError) as error:
            await evaluate(
                "gemini_judge", JUDGE, output="x", gemini_api_key="secret", client=client
            )
    assert error.value.code == "provider_timeout"
    assert error.value.retryable


@pytest.mark.parametrize("output", [(1, 2), {1: "value"}])
async def test_library_rejects_non_json_types(output):
    with pytest.raises(EvaluationError) as error:
        await evaluate("json_match", {}, output=output, expected_output=output)
    assert error.value.code == "invalid_input"


async def test_library_rejects_deep_or_cyclic_values():
    deep = []
    for _ in range(65):
        deep = [deep]
    cyclic = []
    cyclic.append(cyclic)
    for value in (deep, cyclic):
        with pytest.raises(EvaluationError) as error:
            await evaluate("json_match", {}, output=value)
        assert error.value.code == "invalid_input"
