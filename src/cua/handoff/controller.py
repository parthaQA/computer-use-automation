from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from cua.contracts.actions import ControlOwner
from cua.session.control import SessionState


@dataclass
class Intervention:
    session_id: str
    capability_id: str | None
    goal: str | None
    step_id: str | None
    reason: str
    screenshot_note: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class HandoffController:
    def pause(self, session: SessionState, intervention: Intervention) -> Intervention:
        session.cede_to_human()
        return intervention

    def resume(self, session: SessionState) -> None:
        if session.control_owner != ControlOwner.human:
            raise RuntimeError("resume requires HUMAN control_owner")
        session.resume_automation()
