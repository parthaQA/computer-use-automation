from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cua.enums import InterventionStatus, ResultStatus, ResumeMode, SurfaceKind
from .states import RunState


def new_id() -> str:
    """Return a short unique id for a run or intervention."""
    return uuid.uuid4().hex[:8]


def now() -> str:
    """UTC timestamp string for events and interventions."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Model(BaseModel):
    """Base class for all our models, with two safety settings:
    - extra="forbid": a typo like Run(nmae="x") raises an error instead of being ignored
    - validate_assignment=True: run.state = 123 raises an error instead of silently breaking
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ErrorDetail(Model):
    """Enough detail to debug a failure: what step, what we expected, what we saw."""

    step: str | None
    expected: str
    observed: str


class RunResult(Model):
    status: ResultStatus
    outputs: dict[str, Any] = Field(default_factory=dict)  # e.g. {"balance_cents": 123456}
    outcome_code: str | None = None                        # e.g. "MEMBER_NOT_FOUND"
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "RunResult":
        """A result must make sense: the right fields for the right status."""
        if self.status == ResultStatus.SUCCESS and (self.error or self.outcome_code):
            raise ValueError("a success result must not have an error or an outcome_code")
        if self.status == ResultStatus.BUSINESS_OUTCOME and not self.outcome_code:
            raise ValueError("a business_outcome result needs an outcome_code")
        if self.status == ResultStatus.FAILED and self.error is None:
            raise ValueError("a failed result needs error details")
        return self

    @classmethod
    def success(cls, outputs: dict[str, Any] | None = None) -> "RunResult":
        """Build a successful RunResult with optional outputs."""
        return cls(status=ResultStatus.SUCCESS, outputs=outputs or {})

    @classmethod
    def business_outcome(cls, code: str, outputs: dict[str, Any] | None = None) -> "RunResult":
        """Build a business-outcome result (not-found, locked, …)."""
        return cls(status=ResultStatus.BUSINESS_OUTCOME, outcome_code=code, outputs=outputs or {})

    @classmethod
    def failed(
        cls,
        step: str | None,
        expected: str,
        observed: str,
        outputs: dict[str, Any] | None = None,
    ) -> "RunResult":
        """Build a failed result with step, expected, and observed."""
        return cls(
            status=ResultStatus.FAILED,
            outputs=outputs or {},
            error=ErrorDetail(step=step, expected=expected, observed=observed),
        )

    def as_extracted(self) -> dict[str, Any]:
        """Same shape as LookupOutput — what we persist on workflow_finished in events.jsonl."""
        skip = {
            "userflow_path",
            "workflow_path",
            "outcome_code",
            "detail",
            "screen_id",
            "member_id",
            "step",
            "expected",
            "product",
            "opening_deposit",
            "password",
            "checkpoint_trail",
        }
        observed = {
            key: value
            for key, value in self.outputs.items()
            if key not in skip and value not in (None, "")
        }
        detail = self.outputs.get("detail") or ""
        if self.error and not detail:
            detail = self.error.observed
        return {
            "outcome": self.status.value,
            "screen_id": self.outputs.get("screen_id"),
            "detail": detail,
            "observed": observed,
            "outcome_code": self.outcome_code,
            "step": (self.error.step if self.error else None) or self.outputs.get("step"),
            "expected": (self.error.expected if self.error else None) or self.outputs.get("expected"),
        }


class HumanAction(Model):
    """One thing the human did while in control."""

    what: str
    at: str = Field(default_factory=now)


class Intervention(Model):
    """A 'please help' request raised when the automation is stuck."""

    reason: str = Field(min_length=1)
    step: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    id: str = Field(default_factory=new_id)
    status: InterventionStatus = InterventionStatus.OPEN
    operator: str | None = None
    resume_mode: ResumeMode | None = None
    human_actions: list[HumanAction] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


# ----------------------------------------------------------------------
# The run itself
# ----------------------------------------------------------------------
class Run(Model):
    kind: str  # RunKind.DISCOVER or RunKind.REPLAY
    name: str  # the goal or the capability name
    surface: str = SurfaceKind.WEB  # SurfaceKind.WEB or SurfaceKind.DESKTOP — copied onto every events.jsonl line
    tenant_id: str = ""  # empty = env default; set from Tenant.id when a tenant is bound
    run_id: str = Field(default_factory=new_id)
    state: RunState = RunState.CREATED
    result: RunResult | None = None
    interventions: list[Intervention] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)  # the run's diary