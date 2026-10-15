# Phase 5 experiment execution contract

Status: schema and data contracts implemented in PR 1 of Phase 5. Snapshot
creation, application execution, Temporal workflows, lifecycle APIs, and
cancellation handling are subsequent PRs. No experiment execution endpoint is
introduced by this schema slice.

Classification: **IN SCOPE**, implementing frozen items 6 (experiments),
7 (Temporal execution), 10 (results), and 14 (failure handling).

## Delivery sequence

Each PR branches from updated `main` after its prerequisite is merged.

| PR | Branch | Deliverable |
| --- | --- | --- |
| 1 | `feat/experiment-schema` | Experiment/result/score schema, ownership constraints, versioned execution and snapshot contracts. |
| 2 | `feat/experiment-snapshots` | Atomic immutable snapshots, canonical request fingerprints, project-scoped idempotency, active-source deletion guards. |
| 3 | `feat/experiment-activities` | Application execution and independently retryable evaluation activities, checkpointed output, idempotent persistence, shared worker packaging. |
| 4 | `feat/experiment-workflow` | Registered Temporal workflow/worker, bounded concurrency, timeouts, retries, progress, finalization. |
| 5 | `feat/experiment-api` | Create/start/list/detail/results APIs and recovery across database commit/workflow-start failures. |
| 6 | `feat/experiment-cancellation` | Durable cancellation intent, pending-start races, active activity cancellation and terminal write guards. |
| 7 | `test/experiment-durability` | Restart/crash acceptance tests, failure fixtures, Docker checks and runnable walkthrough. |

## Ownership and frozen inputs

An experiment belongs to one project. Each case result and selected evaluator
snapshot carries that same project through a composite foreign key. Each score
must reference both a result and a selected evaluator from the same experiment;
even another run in the same project cannot supply its evaluator selection.
Deleting a project cascades to its experiments, snapshots, results and scores.

`dataset_id`, `test_case_id`, and `evaluator_id` retain the original resource
identities. They deliberately do not have foreign keys to mutable source rows.
Instead, the experiment stores dataset name/description in `dataset_snapshot`,
each result stores a `case_snapshot`, and each selected evaluator stores a
`config_snapshot`. This preserves history when source resources are deleted.
These UUIDs identify historical sources; they do not grant access to live data.

PR 2 must validate all live-source ownership and case membership before copying
anything, using a consistent database snapshot and a single transaction. It must
also enforce the existing active-experiment deletion restrictions, including
case deletion within an active dataset. Direct SQL inserts are not a substitute
for this service validation. Composite foreign keys enforce ownership within
experiment-owned records, not the ownership of historical source UUID labels.

Snapshot contents have `schema_version: 1`:

- Dataset: `name`, nullable `description`.
- Case: `name`, `input`, nullable `expected_output`, `metadata`.
- Evaluator: `name`, `type`, validated and normalized `config`.
- Run configuration: `target`, `parameters`, `max_concurrency`,
  `application_timeout_seconds`.

The typed contracts are in `backend/tracegrade_experiments/contracts.py` and
reuse Phase 4 evaluator validation. They reject unknown fields, non-finite JSON,
unsupported schema versions, and oversized/deep payloads. These checks run when
constructing snapshots; JSONB columns do not enforce their entire JSON shape.
PR 2 must prevent snapshot changes through the application service after creation.
The schema alone does not make snapshots immutable against direct SQL updates.

## Application execution

The first runner implementation in PR 3 will use an HTTP adapter. `target` is a
configured application name such as `code-reviewer`, not a URL, Python import
path, shell command, or model-provider selector. The worker resolves its endpoint
and authentication from installation configuration. Credentials are not fields
in the run configuration. `parameters` contains nonsecret application settings
that are safe to retain with the experiment.

```json
{
  "schema_version": 1,
  "target": "code-reviewer",
  "parameters": {"review_style": "security"},
  "max_concurrency": 5,
  "application_timeout_seconds": 60
}
```

Concurrency is an integer from 1 to 20; application timeout is an integer from
1 to 300 seconds. Defaults are 5 and 60. Server-side worker capacity may further
limit concurrent calls. These fields are validated data contracts in this PR;
actual enforcement is introduced with the activity/workflow implementation.

The worker will POST one versioned request per case to the configured endpoint:

```json
{
  "schema_version": 1,
  "experiment_id": "00000000-0000-0000-0000-000000000001",
  "test_case_id": "00000000-0000-0000-0000-000000000002",
  "version_label": "baseline-v1",
  "input": {"diff": "+query(user_input)"},
  "parameters": {"review_style": "security"}
}
```

`ApplicationRequest.idempotency_key` derives
`tracegrade:<experiment UUID>:<test-case UUID>`. PR 3 will send it in the
`Idempotency-Key` header on every attempt. It is not a caller-supplied request-body
field. The application is responsible for deduplicating external side effects;
TraceGrade can guarantee one stored result, but cannot guarantee an external
call executes exactly once after a crash between the call and its checkpoint.
`version_label` is supplied to the application for reproducibility; TraceGrade
does not deploy or route application versions.

A successful application response contains `output`, even when its value is
JSON null. Optional metadata fields are:

```json
{
  "schema_version": 1,
  "output": {"safe": false, "finding": "SQL injection"},
  "model": "example-model",
  "prompt_tokens": 20,
  "completion_tokens": 5,
  "total_tokens": 25,
  "estimated_cost_usd": "0.0000012345"
}
```

Tokens must be nonnegative 32-bit integers, not booleans or numeric strings.
Cost is nonnegative decimal USD with at most 10 integer and 10 fractional digits.
Missing metrics stay unknown; they are not converted to zero or inferred from a
price catalog. The worker will measure application latency in milliseconds,
excluding judge calls. Input/output/reference JSON is limited to 64 KiB each;
parameters/metadata to 16 KiB, with at most 64 levels of nesting.

Application execution and evaluator execution are separate activity steps. Once
`output_recorded_at` is set, judge retries use the stored output, including a
valid JSON-null output. `attempt_count` on a case tracks application attempts;
score `attempt_count` tracks attempts for its evaluator. Persistence activities
must not increment either counter merely because a database write is replayed.

## Idempotency and progress

- `(project_id, idempotency_key)` identifies a creation request. `request_hash`
  will contain its canonical SHA-256 fingerprint. Reusing the same key for a
  different request is a conflict. A matching retry returns the original run
  without rebuilding its snapshot from possibly edited sources.
- `workflow_id` is globally unique. PR 5 must use a deterministic workflow ID
  based on the experiment UUID and recover both before-start and after-start
  failures without starting a second execution.
- `(experiment_id, test_case_id)` identifies one case result.
- `(experiment_id, evaluator_id)` identifies one selected evaluator snapshot.
- `(experiment_result_id, evaluator_id)` identifies one score. A failed score
  may be replaced by a successful retry through guarded persistence in PR 3.
- `completed_cases`, `failed_cases`, and `cancelled_cases` count disjoint case
  outcomes. All are nonnegative and their sum cannot exceed `total_cases`.
  The service rejects empty datasets, reflected by `total_cases > 0`.

Counters and row statuses must change atomically; terminal updates and retries
must not count a case twice. Guarding state transitions, stale attempt writes,
and late cancellation results belongs to the subsequent persistence/workflow PRs.

## Outcome semantics

An experiment or case can be `pending`, `running`, `completed`, `failed`, or
`cancelled`. A score can be `completed` or `failed`:

- A completed score has a numeric value from 0 to 1 and a boolean verdict, with
  no error message. Zero/false means valid evaluation and low quality.
- A failed score has no numeric score/verdict and a required error message.
- A case completes when application execution and all selected evaluators
  succeed. Exhausted application or evaluator errors fail that case; other cases
  continue. Completed score rows remain available when another evaluator fails.
- An experiment completes after all cases finish if at least one case completes;
  partial failures remain visible in `failed_cases`. It fails if every case fails
  or an unrecoverable orchestration error prevents completion.
- A cancelled experiment preserves completed work and counts unfinished cases as
  cancelled. Fatal orchestration failure must finalize remaining cases with an
  explicit failure reason. Terminal runs must not leave cases pending forever.

`error_message`, timestamps and `cancel_requested_at` reserve durable fields for
those later transitions. Database constraints enforce score shape, numeric
bounds, uniqueness, ownership, and counter bounds now; lifecycle rules above are
the contract for the upcoming services, not already implemented orchestration.

Aggregated comparisons/regressions remain Phase 6; dashboard resource management
and the real AI Code Reviewer adapter/demo remain Phase 7.

## Verification

The schema tests run on SQLite by default and repeat against a migrated,
isolated PostgreSQL database when `TRACEGRADE_TEST_DATABASE_URL` is set. They
cover ownership constraints, uniqueness, invalid progress/metrics/status values,
source-history retention, and project deletion cascades. Contract tests validate
application payloads, snapshot settings, limits and stable per-case identifiers.
