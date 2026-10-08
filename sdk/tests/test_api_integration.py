"""Opt-in test against a running TraceGrade API backed by an isolated database."""

import os
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from tracegrade import TraceGrade


def test_sdk_round_trip_against_api():
    api_url = os.getenv("TRACEGRADE_TEST_API_URL")
    admin_key = os.getenv("TRACEGRADE_TEST_ADMIN_KEY")
    if not api_url or not admin_key:
        pytest.skip("Set TRACEGRADE_TEST_API_URL and TRACEGRADE_TEST_ADMIN_KEY")
    with httpx.Client(base_url=api_url.rstrip("/") + "/", trust_env=False, timeout=5) as api:
        admin_headers = {"X-TraceGrade-Admin-Key": admin_key}
        created = api.post("projects", json={"name": f"SDK test {uuid4()}"}, headers=admin_headers)
        assert created.status_code == 201
        project_id = created.json()["id"]
        try:
            key = api.post(
                f"projects/{project_id}/api-keys", json={"name": "SDK test"}, headers=admin_headers
            )
            assert key.status_code == 201
            project_key = key.json()["key"]
            with TraceGrade(project_key, base_url=api_url, raise_on_error=True) as client:
                with client.trace("review", attributes={"version": "v1"}) as trace:
                    with trace.span("fetch") as parent:
                        with parent.span(
                            "model", kind="llm", model="example", input={"prompt": "review"}
                        ) as child:
                            child.set_output({"summary": "ok"})
                            child.set_usage(
                                prompt_tokens=20,
                                completion_tokens=10,
                                estimated_cost_usd=Decimal("0.0000012345"),
                            )
                with pytest.raises(ValueError, match="provider failure"):
                    with client.trace("failed") as failed:
                        with failed.span("model", kind="llm"):
                            raise ValueError("provider failure")
                assert client.delivery_errors == ()
            headers = {"Authorization": f"Bearer {project_key}"}
            listing = api.get("traces", headers=headers)
            assert listing.status_code == 200
            assert listing.json()["total"] == 2
            detail = api.get(f"traces/{trace.id}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["status"] == "ok"
            spans = {span["name"]: span for span in detail.json()["spans"]}
            assert spans["model"]["parent_span_id"] == parent.id
            assert spans["model"]["output"] == {"summary": "ok"}
            assert spans["model"]["total_tokens"] == 30
            assert spans["model"]["estimated_cost_usd"] == "0.0000012345"
            failed_detail = api.get(f"traces/{failed.id}", headers=headers)
            assert failed_detail.status_code == 200
            assert failed_detail.json()["status"] == "error"
            assert failed_detail.json()["spans"][0]["error_message"] == "provider failure"
        finally:
            deleted = api.delete(f"projects/{project_id}", headers=admin_headers)
            assert deleted.status_code == 204
