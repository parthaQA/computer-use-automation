"""Long-lived session with a single control owner.

HITL pauses this session — it does not open a fresh browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from cua.contracts.actions import ControlOwner


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: uuid4().hex[:12])
    control_owner: ControlOwner = ControlOwner.automation
    start_url: str | None = None
    tenant_id: str | None = None

    def cede_to_human(self) -> None:
        self.control_owner = ControlOwner.human

    def resume_automation(self) -> None:
        self.control_owner = ControlOwner.automation
