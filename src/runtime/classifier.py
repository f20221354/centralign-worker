"""Observation -> recovery route. Deterministic; business-rule failures are never treated as transient."""
from __future__ import annotations

from src.contracts import Observation, ObsStatus, Outcome

MAX_ATTEMPTS = 3       # per action, for transient failures
MAX_ADAPTATIONS = 2    # per run, for UI changes


def classify(obs: Observation, attempt: int, adaptations: int) -> tuple[Outcome, str]:
    s = obs.status
    if obs.data.get("fatal"):
        return Outcome.FAIL, "configuration error; retrying cannot help"
    if s == ObsStatus.SUCCESS:
        return Outcome.CONTINUE, ""
    if s == ObsStatus.HTTP_ERROR:
        if obs.http_status is not None and 400 <= obs.http_status < 500:
            return Outcome.REPLAN, f"HTTP {obs.http_status} is not transient"
        if attempt < MAX_ATTEMPTS:
            return Outcome.RETRY, f"transient failure (attempt {attempt}/{MAX_ATTEMPTS})"
        return Outcome.FAIL, f"still failing after {MAX_ATTEMPTS} attempts"
    if s == ObsStatus.DOM_ERROR:
        if adaptations < MAX_ADAPTATIONS:
            return Outcome.ADAPT, "the page differs from what the executor expected"
        return Outcome.FAIL, f"UI still not usable after {MAX_ADAPTATIONS} adaptations"
    if s in (ObsStatus.DATA_AMBIGUITY, ObsStatus.CONFLICT):
        return Outcome.HUMAN, "business data needs a human decision"
    # VALIDATION_ERROR, NOT_FOUND, POLICY_BLOCKED: new information; the model must choose differently
    return Outcome.REPLAN, s.value.lower().replace("_", " ")


def backoff_s(attempt: int) -> float:
    return min(0.8 * 2 ** (attempt - 1), 5.0)
