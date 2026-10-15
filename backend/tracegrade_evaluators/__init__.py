"""Scoring from fixed outputs. No API, database, or workflow dependencies."""

from tracegrade_evaluators.config import EvaluatorType
from tracegrade_evaluators.engine import EvaluationError, EvaluationResult, evaluate

__all__ = ["EvaluationError", "EvaluationResult", "EvaluatorType", "evaluate"]
