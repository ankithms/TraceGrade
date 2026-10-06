# Rejected Ideas Backlog

This file prevents rejected ideas from silently re-entering v1. Items here are not scheduled and must not be implemented during the frozen build.

| Idea | v1 decision | Reason |
| --- | --- | --- |
| MCP | Rejected | Not required by the frozen evaluation workflow. |
| RAG | Rejected | Adds a separate retrieval product surface. |
| Kubernetes | Rejected | Docker and one public deployment are sufficient for the portfolio release. |
| Kafka | Rejected | PostgreSQL, HTTP ingestion, and Temporal cover the required workload. |
| Multi-agent orchestration | Rejected | TraceGrade evaluates applications; it does not orchestrate agent teams. |
| Multi-tenancy | Rejected | v1 is a single-owner installation with project-level data boundaries. |
| RBAC | Rejected | A role system is unnecessary for the single-owner v1. |
| GitHub Actions | Rejected | CI/CD automation is not part of the frozen product. |
| Model routing | Rejected | TraceGrade records the selected model; it does not choose models. |
| Multiple judge providers | Rejected | Gemini is the only frozen LLM-as-judge provider. |
| Complex prompt management | Rejected | Evaluator rubrics/configuration are sufficient; no prompt CMS or versioning product. |
| Alerts and notifications | Rejected | Regressions are displayed in results and the dashboard only. |
| Billing | Rejected | Cost is measured for experiments, not charged to users. |
| Trace replay | Rejected | Experiments run from datasets; production traces are not replayed. |

## Rule

New ideas are appended to this table with a reason. They may be reconsidered only after the v1 completion contract in `SCOPE.md` is met.
