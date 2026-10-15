"""Validated, serializable evaluator settings; independent of API and database code."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints


class EvaluatorType(StrEnum):
    EXACT_MATCH = "exact_match"
    CONTAINS = "contains"
    JSON_MATCH = "json_match"
    GEMINI_JUDGE = "gemini_judge"


class TextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_sensitive: StrictBool = True
    strip_whitespace: StrictBool = False


class JsonMatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeminiJudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Annotated[str, StringConstraints(pattern=r"^gemini-[a-zA-Z0-9._-]+$", max_length=120)]
    rubric: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)]
    pass_threshold: float = Field(default=0.5, strict=True, ge=0, le=1, allow_inf_nan=False)


EvaluatorConfig = TextConfig | JsonMatchConfig | GeminiJudgeConfig


def validate_config(kind: EvaluatorType | str, config: dict) -> EvaluatorConfig:
    kind = EvaluatorType(kind)
    schema = {
        EvaluatorType.EXACT_MATCH: TextConfig,
        EvaluatorType.CONTAINS: TextConfig,
        EvaluatorType.JSON_MATCH: JsonMatchConfig,
        EvaluatorType.GEMINI_JUDGE: GeminiJudgeConfig,
    }[kind]
    return schema.model_validate(config)
