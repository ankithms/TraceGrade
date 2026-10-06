# TraceGrade Scope Contract

Status: **Frozen for v1**

TraceGrade is an AI Evaluation & Reliability Platform for tracing LLM applications, creating evaluation datasets, running durable experiments, comparing versions, and detecting regressions in quality, latency, and cost.

This document is the implementation contract. Work may clarify how an item is built, but it must not expand what the product does.

## In Scope

1. Projects and project-scoped API keys.
2. A Python SDK.
3. Trace and span ingestion.
4. LLM metadata: input, output, model, token counts, latency, and status.
5. Datasets and test cases.
6. Experiments.
7. Temporal-based durable experiment execution.
8. Deterministic evaluators.
9. Gemini as the single LLM-as-judge provider.
10. Experiment results.
11. A/B experiment comparison.
12. Regression detection across quality, latency, and cost.
13. A web dashboard.
14. Failure handling and retries.
15. AI Code Reviewer integration as the real end-to-end demo.
16. Docker setup, public deployment, documentation, and a recorded or scripted demo.

## Explicitly Out of Scope

- MCP.
- RAG.
- Kubernetes.
- Kafka.
- Multi-agent orchestration.
- Multi-tenancy.
- RBAC.
- GitHub Actions.
- Model routing.
- Multiple LLM providers.
- Complex prompt management.
- Alerts or notifications.
- Billing.
- Trace replay.
- Any feature not listed in the frozen scope.

## Product Boundary

- One TraceGrade installation is operated by one owner or team.
- Projects isolate application data and API keys; they are not tenants.
- Gemini is the only judge integration. Deterministic evaluators do not call a model.
- The dashboard manages and reads only the frozen resources.
- Comparison and regression detection operate on completed experiment results.
- The AI Code Reviewer is a consumer and demo of TraceGrade, not a second platform.
- Public deployment means the frozen system is reachable and demonstrable; it does not imply enterprise identity, scaling, or operations features.

## Scope Decision Rule

Every proposed change must be classified before implementation:

- **BLOCKER**: required to make a frozen item function correctly or safely.
- **IN SCOPE**: directly implements one of the 16 frozen items.
- **OUT OF SCOPE**: anything else. Record it in `BACKLOG.md` and do not implement it in v1.

## v1 Completion Contract

TraceGrade v1 is complete only when a reviewer can:

1. Create a project and API key.
2. Instrument the AI Code Reviewer with the Python SDK and inspect its traces and spans.
3. Build a dataset of code-review cases.
4. Run deterministic and Gemini-judge evaluations through Temporal.
5. Inspect durable execution status, retries, case-level results, and aggregate metrics.
6. Compare two completed experiments and see quality, latency, and cost regressions.
7. Use the dashboard for the same core workflow.
8. Run the stack with Docker and access a public demo with setup and architecture documentation.

Passing the completion contract does not require any out-of-scope capability.
