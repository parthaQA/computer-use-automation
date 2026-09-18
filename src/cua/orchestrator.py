"""Orchestrator owns mode selection. It never talks to Playwright or the LLM directly."""

from __future__ import annotations

from enum import Enum

from cua.contracts.artifact import Capability
from cua.contracts.result import RunResult
from cua.handoff.controller import HandoffController
from cua.policy.gate import PolicyGate
from cua.replay.engine import ReplayEngine
from cua.session.control import SessionState
from cua.store.base import CapabilityStore
from cua.surface.base import SurfaceDriver


class Mode(str, Enum):
    discover = "discover"
    replay = "replay"
    escalate = "escalate"


class Orchestrator:
    def __init__(
        self,
        store: CapabilityStore,
        policy: PolicyGate,
        driver: SurfaceDriver,
        handoff: HandoffController | None = None,
    ):
        self.store = store
        self.policy = policy
        self.driver = driver
        self.handoff = handoff or HandoffController()
        self.replay = ReplayEngine(driver, policy)

    def replay_capability(
        self,
        capability_id: str,
        params: dict,
        *,
        tenant_id: str | None = None,
        human_approved: bool = False,
    ) -> RunResult:
        cap: Capability = self.store.get(capability_id)
        return self.replay.run(
            cap, params, tenant_id=tenant_id, human_approved=human_approved
        )

    def new_session(self, start_url: str, tenant_id: str | None = None) -> SessionState:
        return SessionState(start_url=start_url, tenant_id=tenant_id)
