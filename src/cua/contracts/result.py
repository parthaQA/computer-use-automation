from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    succeeded = "succeeded"
    business_outcome = "business_outcome"
    failed = "failed"
    escalated = "escalated"


class OutcomeCode(str, Enum):
    """App-level answers the caller must handle — not crashes."""

    member_not_found = "MEMBER_NOT_FOUND"
    permission_denied = "PERMISSION_DENIED"
    membership_locked = "MEMBERSHIP_LOCKED"
    validation_error = "VALIDATION_ERROR"
    insufficient_funds = "INSUFFICIENT_FUNDS"
    signon_rejected = "SIGNON_REJECTED"
    session_timeout = "SESSION_TIMEOUT"
    unexpected_dialog = "UNEXPECTED_DIALOG"


class StepError(BaseModel):
    step_id: str
    expected: str
    observed: str
    screenshot_path: str | None = None


class RunResult(BaseModel):
    status: RunStatus
    capability_id: str | None = None
    run_id: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    outcome_code: OutcomeCode | None = None
    error: StepError | None = None
    evidence_ref: str | None = None
    control_owner: str = "AUTOMATION"

    @property
    def ok(self) -> bool:
        return self.status in (RunStatus.succeeded, RunStatus.business_outcome)
