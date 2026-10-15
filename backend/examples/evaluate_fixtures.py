"""Run fixed evaluation fixtures locally; optionally run a real Gemini judge.

From backend/: python examples/evaluate_fixtures.py [--gemini-model MODEL]
Credentials for the optional judge come only from GEMINI_API_KEY.
"""

import argparse
import asyncio
import json
import os
from dataclasses import asdict

from tracegrade_evaluators import EvaluationError, evaluate

FIXTURES = [
    {
        "name": "exact verdict",
        "type": "exact_match",
        "config": {},
        "input": {"diff": "+query(user_input)"},
        "output": "unsafe",
        "expected_output": "unsafe",
    },
    {
        "name": "missing finding",
        "type": "contains",
        "config": {"case_sensitive": False},
        "input": {"diff": "+query(user_input)"},
        "output": "Looks good",
        "expected_output": "SQL injection",
    },
    {
        "name": "structured review",
        "type": "json_match",
        "config": {},
        "input": {"diff": "+query(user_input)"},
        "output": '{"safe":false,"issues":["SQL injection"]}',
        "expected_output": {"issues": ["SQL injection"], "safe": False},
    },
]


async def main(model: str | None) -> int:
    fixtures = list(FIXTURES)
    if model:
        fixtures.append(
            {
                "name": "judge review",
                "type": "gemini_judge",
                "config": {
                    "model": model,
                    "rubric": (
                        "Reward identifying SQL injection and recommending parameterized queries."
                    ),
                    "pass_threshold": 0.75,
                },
                "input": {"diff": "+db.execute('SELECT * FROM users WHERE name=' + user_input)"},
                "output": "SQL injection risk: use a parameterized query instead of concatenation.",
            }
        )
    failed = False
    for fixture in fixtures:
        try:
            result = await evaluate(
                fixture["type"],
                fixture["config"],
                input=fixture["input"],
                output=fixture["output"],
                expected_output=fixture.get("expected_output"),
                gemini_api_key=os.getenv("GEMINI_API_KEY"),
            )
            print(json.dumps({"name": fixture["name"], "status": "completed", **asdict(result)}))
        except EvaluationError as error:
            failed = True
            print(
                json.dumps(
                    {
                        "name": fixture["name"],
                        "status": "failed",
                        "code": error.code,
                        "message": str(error),
                        "retryable": error.retryable,
                    }
                )
            )
    return int(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gemini-model", help="Opt into a real Gemini call with this model ID")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.gemini_model)))
