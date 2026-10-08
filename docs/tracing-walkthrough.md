# Phase 3 tracing walkthrough

This walkthrough records a fixture AI code review through the Python SDK and
checks the persisted result through the API. It makes no AI-provider calls and
needs no Gemini key. The model name, response, token counts and cost are sample
values; latency is measured during the local fixture operation.

## Start the API and install the SDK

From the repository root, with your local `.env` configured:

```bash
docker compose up --build -d
python3 -m venv sdk/.venv
source sdk/.venv/bin/activate
pip install -e ./sdk
```

Use Python 3.10 or newer. Confirm `http://localhost:8000/api/v1/health/ready`
returns HTTP 200. Container startup applies the database migrations automatically.
For a fresh checkout, follow the root README's `.env` and port setup first.

## Get a project key

If you already have a project API key, use it. Otherwise open
<http://localhost:8000/docs>:

1. Expand `POST /api/v1/projects` and select **Try it out**. Enter your local
   `TRACEGRADE_ADMIN_KEY` from `.env` in the `X-TraceGrade-Admin-Key` header field.
   Submit `{"name": "Tracing walkthrough", "slug": "tracing-walkthrough"}`.
   Save the returned project `id`. If that slug already exists, use its existing
   project or choose another slug.
2. Expand `POST /api/v1/projects/{project_id}/api-keys`, supply that project ID
   and the admin header, and submit `{"name": "Tracing example"}`.
3. Save the returned `key` in your password manager; plaintext is revealed only
   once. The SDK needs this project key.

Enter the project key at the terminal without putting it in shell history
(the following commands work in Bash and Zsh; paste the key and press Enter):

```bash
read -rs TRACEGRADE_API_KEY
export TRACEGRADE_API_KEY
```

## Record and verify a review

```bash
python sdk/examples/trace_review.py
```

For another API address, set `TRACEGRADE_API_URL` or pass
`--base-url http://localhost:8000/api/v1`. Include `/api/v1` in the URL.

Successful output looks like:

```text
Trace ID: <generated UUID>
Verified: 3 spans, nested LLM metadata, 30 tokens, cost, timing and handled error.
Replay verified: same trace ID and span IDs; no duplicate records.
Inspect: GET http://localhost:8000/api/v1/traces/<generated UUID>
```

The trace contains this parent/child structure:

```text
AI Code Reviewer example                       ok (trace)
└── review pipeline                            ok (span)
    ├── model review                           ok (LLM span)
    └── publish review                         error (span)
```

The model span stores its diff input, review output, `fixture-model`, 20 prompt
tokens, 10 completion tokens, 30 total tokens, and estimated cost
`0.0000012345` USD. Every span has timing metadata. Publishing raises a fixture
exception which the application handles; its span stores the error while the
pipeline and trace complete successfully.

The script reads the completed snapshots and sends them again through trace and
span-batch ingestion, retaining their original external IDs and timestamps. It
checks HTTP 200 for trace replay, `created: false` for every span replay, and
unchanged IDs and span count on a second read. Each new script execution creates
a new trace; replay happens within that run.

## Inspect the stored records

In API docs, select **Authorize**, paste your project key into the HTTP Bearer
value field, and authorize it. Then try `GET /api/v1/traces` or
`GET /api/v1/traces/{trace_id}`. The list includes
the trace; detail includes its three spans, parent IDs and captured metadata.
The dashboard currently contains a shell; trace browsing is available through
the API.

The script exits nonzero if delivery or verification fails. If it fails, check
API readiness, the `/api/v1` URL, the active project key, and
`docker compose logs backend`. A failed attempt may leave partial records; a
new run creates new external IDs. Revoked keys return 401. For a lost key, create
a replacement in the same project and revoke the old key.

The example leaves its trace in your project for inspection. Deleting a dedicated
walkthrough project through the admin API removes its keys, traces and spans.
To instrument your real model call, replace the fixture response and usage in
`sdk/examples/trace_review.py` with your application's actual values.
