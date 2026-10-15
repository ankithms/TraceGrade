"""Creation input for the snapshot service; HTTP lifecycle routes arrive in PR 5."""

from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tracegrade_experiments.contracts import Name, RunConfig


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID
    name: Name
    version_label: Name
    idempotency_key: Annotated[
        str, StringConstraints(min_length=1, max_length=255, pattern=r"^\S+$")
    ]
    evaluator_ids: list[UUID] = Field(min_length=1, max_length=100)
    run_config: RunConfig

    @model_validator(mode="after")
    def unique_evaluators(self) -> Self:
        if len(set(self.evaluator_ids)) != len(self.evaluator_ids):
            raise ValueError("Evaluator IDs must be unique")
        return self
