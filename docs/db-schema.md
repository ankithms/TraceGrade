# Database Schema Plan

PostgreSQL is the only product database. UUID primary keys, UTC timestamps, foreign keys, and explicit unique constraints are used throughout. JSONB is reserved for user/model payloads and small frozen configuration snapshots, not as a substitute for core relational fields.

## Tables

### `projects`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `name` | TEXT | Human-readable name. |
| `slug` | TEXT | Unique stable identifier. |
| `created_at` | TIMESTAMPTZ | Creation time. |
| `updated_at` | TIMESTAMPTZ | Last update time. |

### `api_keys`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `project_id` | UUID | FK to `projects`; indexed. |
| `name` | TEXT | Key label. |
| `prefix` | TEXT | Safe display/lookup prefix; unique. |
| `secret_hash` | TEXT | One-way hash; plaintext is never stored. |
| `last_used_at` | TIMESTAMPTZ | Nullable. |
| `revoked_at` | TIMESTAMPTZ | Nullable; set instead of deleting usage history. |
| `created_at` | TIMESTAMPTZ | Creation time. |

### `traces`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `project_id` | UUID | FK to `projects`; indexed. |
| `external_id` | TEXT | SDK-generated idempotency ID. |
| `name` | TEXT | Operation name. |
| `status` | ENUM | `running`, `ok`, `error`. |
| `started_at` | TIMESTAMPTZ | Client-reported start. |
| `ended_at` | TIMESTAMPTZ | Nullable. |
| `attributes` | JSONB | Bounded application metadata. |
| `created_at` | TIMESTAMPTZ | Ingestion time. |

Unique: (`project_id`, `external_id`).

### `spans`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `project_id` | UUID | FK to `projects`; indexed. |
| `trace_id` | UUID | FK to `traces`; indexed. |
| `parent_span_id` | UUID | Nullable self-reference. |
| `external_id` | TEXT | SDK-generated idempotency ID. |
| `name` | TEXT | Operation name. |
| `kind` | ENUM | `llm` or `operation`. |
| `input` | JSONB | Nullable, bounded payload. |
| `output` | JSONB | Nullable, bounded payload. |
| `model` | TEXT | Nullable for non-LLM spans. |
| `prompt_tokens` | INTEGER | Nullable, non-negative. |
| `completion_tokens` | INTEGER | Nullable, non-negative. |
| `total_tokens` | INTEGER | Nullable, non-negative. |
| `latency_ms` | INTEGER | Nullable, non-negative. |
| `estimated_cost_usd` | NUMERIC | Nullable, non-negative. |
| `status` | ENUM | `running`, `ok`, `error`. |
| `error_message` | TEXT | Nullable, size-limited. |
| `started_at` | TIMESTAMPTZ | Client-reported start. |
| `ended_at` | TIMESTAMPTZ | Nullable. |
| `attributes` | JSONB | Bounded application metadata. |
| `created_at` | TIMESTAMPTZ | Ingestion time. |

Unique: (`project_id`, `external_id`). Index: (`trace_id`, `started_at`).

Trace/span schema implementation:

- Composite foreign keys require a span's project to match its trace, and a parent
  span to belong to the same trace and project. A span cannot reference itself.
- Project, trace, and parent-span deletion cascades to dependent spans.
- Non-negative metric checks are enforced in the database; unknown metrics may be null.
- Estimated cost uses `NUMERIC(20, 10)` to preserve small fractional USD amounts.
- `execution_status` is shared by traces and spans; `span_kind` is a separate
  PostgreSQL enum. Migration rollback removes both types after dropping the tables.
- Payload size limits and full ancestry-cycle validation belong to ingestion APIs.


### `datasets`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `project_id` | UUID | FK to `projects`; indexed. |
| `name` | TEXT | Unique within project. |
| `description` | TEXT | Nullable. |
| `created_at` | TIMESTAMPTZ | Creation time. |
| `updated_at` | TIMESTAMPTZ | Last update time. |

### `test_cases`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `dataset_id` | UUID | FK to `datasets`; indexed. |
| `name` | TEXT | Case label. |
| `input` | JSONB | Application input. |
| `expected_output` | JSONB | Nullable reference output. |
| `metadata` | JSONB | Bounded tags/context. |
| `created_at` | TIMESTAMPTZ | Creation time. |
| `updated_at` | TIMESTAMPTZ | Last update time. |

### `evaluators`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `project_id` | UUID | FK to `projects`; indexed. |
| `name` | TEXT | Unique within project. |
| `type` | ENUM | `exact_match`, `contains`, `json_match`, or `gemini_judge`. |
| `config` | JSONB | Validated settings or one judge rubric; no prompt-management subsystem. |
| `created_at` | TIMESTAMPTZ | Creation time. |
| `updated_at` | TIMESTAMPTZ | Last update time. |

### `experiments`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `project_id` | UUID | FK to `projects`; indexed. |
| `dataset_id` | UUID | Historical source dataset identity; no live-source FK. |
| `name` | TEXT | Experiment label. |
| `version_label` | TEXT | Application/prompt version supplied by the caller. |
| `status` | ENUM | `pending`, `running`, `completed`, `failed`, `cancelled`. |
| `workflow_id` | TEXT | Unique Temporal workflow ID. |
| `run_config` | JSONB | Immutable execution snapshot. |
| `total_cases` | INTEGER | Snapshot count. |
| `completed_cases` | INTEGER | Progress counter. |
| `failed_cases` | INTEGER | Progress counter. |
| `started_at` | TIMESTAMPTZ | Nullable. |
| `completed_at` | TIMESTAMPTZ | Nullable. |
| `created_at` | TIMESTAMPTZ | Creation time. |

### `experiment_evaluators`

Snapshot table with (`experiment_id`, `evaluator_id`) as the primary key. It carries `project_id` for a composite ownership FK to the experiment and `config_snapshot` JSONB containing schema version, name, type, and normalized settings. `evaluator_id` retains the historical source identity without a live-source FK.

### `experiment_results`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `experiment_id` | UUID | FK to `experiments`; indexed. |
| `test_case_id` | UUID | Historical source case identity; no live-source FK. |
| `status` | ENUM | `pending`, `running`, `completed`, `failed`, `cancelled`. |
| `output` | JSONB | Nullable application output. |
| `model` | TEXT | Model used by the evaluated app. |
| `prompt_tokens` | INTEGER | Nullable. |
| `completion_tokens` | INTEGER | Nullable. |
| `total_tokens` | INTEGER | Nullable. |
| `latency_ms` | INTEGER | Nullable. |
| `estimated_cost_usd` | NUMERIC | Nullable. |
| `attempt_count` | INTEGER | Activity attempts observed. |
| `error_message` | TEXT | Nullable, size-limited. |
| `started_at` | TIMESTAMPTZ | Nullable. |
| `completed_at` | TIMESTAMPTZ | Nullable. |

Unique: (`experiment_id`, `test_case_id`).

### `evaluation_scores`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `experiment_result_id` | UUID | FK to `experiment_results`; indexed. |
| `evaluator_id` | UUID | References selected evaluator snapshot within the experiment. |
| `status` | ENUM | `completed` or `failed`. |
| `score` | NUMERIC | Nullable normalized score from 0 to 1. |
| `passed` | BOOLEAN | Nullable evaluator verdict. |
| `rationale` | TEXT | Nullable; primarily for Gemini judge. |
| `error_message` | TEXT | Nullable. |

Unique: (`experiment_result_id`, `evaluator_id`).

### `experiment_comparisons`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `project_id` | UUID | FK to `projects`; indexed. |
| `baseline_experiment_id` | UUID | FK to completed `experiments`. |
| `candidate_experiment_id` | UUID | FK to completed `experiments`. |
| `metrics` | JSONB | Frozen aggregate values and deltas. |
| `thresholds` | JSONB | Quality, latency, and cost thresholds used. |
| `created_at` | TIMESTAMPTZ | Creation time. |

Unique: (`baseline_experiment_id`, `candidate_experiment_id`).

### `regressions`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `comparison_id` | UUID | FK to `experiment_comparisons`; indexed. |
| `metric` | ENUM | `quality`, `latency`, or `cost`. |
| `baseline_value` | NUMERIC | Baseline aggregate. |
| `candidate_value` | NUMERIC | Candidate aggregate. |
| `absolute_delta` | NUMERIC | Candidate minus baseline. |
| `relative_delta` | NUMERIC | Nullable when baseline is zero. |
| `threshold` | NUMERIC | Threshold applied. |
| `detected` | BOOLEAN | Whether the threshold was crossed. |

Unique: (`comparison_id`, `metric`).

## Deletion Policy

Project deletion is an explicit destructive operation and cascades through project-owned data. API keys are normally revoked, not deleted. Experiment inputs/results are immutable after execution begins so comparisons remain reproducible.

## Migration Order

1. Projects and API keys.
2. Traces and spans.
3. Datasets, test cases, and evaluators (`20261015_0003`).
4. Experiments, selected-evaluator snapshots, case results, and scores (`20261015_0004`).
5. Comparisons and regressions.

## Phase 4 implementation

Migration `20261015_0003` creates datasets, test cases, and evaluators, including
project-local name constraints and the PostgreSQL `evaluator_type` enum. Deleting
a project cascades to datasets and evaluators; deleting a dataset cascades to
cases. Cases inherit project ownership through their dataset. The API always
checks dataset ownership before accessing a nested case. `input` may contain
JSON null, while the database column itself remains NOT NULL. The Python ORM
maps the `metadata` column to `case_metadata` to avoid the declarative metadata
attribute; the public API still calls it `metadata`.

Experiment references, snapshots, and deletion restrictions are implemented
with Phase 5; Phase 4 resources are editable and deletable before experiments
exist. Evaluator type is immutable in the API, while config updates apply to
future evaluations.


## Phase 5 schema implementation

Migration `20261015_0004` implements the experiment tables above with these
additional fields and constraints:

- `experiments`: `idempotency_key`, `request_hash`, `dataset_snapshot`,
  `cancelled_cases`, `error_message`, and `cancel_requested_at`. Creation keys
  are unique within a project and workflow IDs are globally unique. Total cases
  must be positive; disjoint terminal counters are nonnegative and cannot sum
  above the total. Index: (`project_id`, `created_at`).
- `experiment_results`: `project_id`, `case_snapshot`, and `output_recorded_at`.
  Composite foreign keys keep results in their experiment's project. The output
  checkpoint distinguishes an unrecorded application call from saved JSON null.
  Metrics and application-attempt counts must be nonnegative. Index:
  (`experiment_id`, `status`).
- `evaluation_scores`: `experiment_id` and evaluator `attempt_count` (at least
  one). Composite foreign keys require a result and selected evaluator from the
  same experiment. Completed scores require a 0–1 score and verdict without an
  error; failed scores require an error and null score/verdict.

Source dataset/case/evaluator UUIDs are historical identities. Required JSONB
snapshots preserve inputs after live source deletion; the PR 2 snapshot service
validates live ownership before copying and the source API protects active resources.
Deleting a source never cascades into experiment history. Project deletion
cascades through experiment-owned records. Snapshot JSON shape and service-level
immutability are enforced by PR 2. Lifecycle transition rules arrive with the
activity/workflow services. See the [execution contract](experiment-execution-contract.md).
