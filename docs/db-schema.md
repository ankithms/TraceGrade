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
| `dataset_id` | UUID | FK to `datasets`. |
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

Junction table with (`experiment_id`, `evaluator_id`) as the primary key and an immutable `config_snapshot` JSONB column.

### `experiment_results`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key. |
| `experiment_id` | UUID | FK to `experiments`; indexed. |
| `test_case_id` | UUID | FK to `test_cases`. |
| `status` | ENUM | `pending`, `running`, `completed`, `failed`. |
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
| `evaluator_id` | UUID | FK to `evaluators`. |
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
3. Datasets and test cases.
4. Evaluators and experiments.
5. Results and scores.
6. Comparisons and regressions.
