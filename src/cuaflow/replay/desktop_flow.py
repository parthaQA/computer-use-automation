"""Replay a UserFlow on the desktop surface. No Playwright, no LLM."""

from __future__ import annotations

import asyncio

from cua.enums import AppId, ConditionKind, ResultStatus, ScreenId, SurfaceKind
from ..discover.lookup import LookupOutput
from ..discover.userflow import UserFlow, UserFlowStep
from ..replay.playwright_flow import apply_step_value, extract_outputs, fill_placeholders, with_env_credentials
from ..replay.conditions import classify_runtime
from ..surface.base import run_step
from ..surface.desktop import DesktopSurface
from ..surface.locators import locators_for
from ..surface.tk_memsys import TkMemsys


def _step_label(step: UserFlowStep) -> str:
    """Short label for logs (click:Search, change:member_id, …)."""
    name = step.assertText or step.value or step.url or ""
    return f"{step.type}:{name}".strip(":")[:120]


class DesktopFlowRunner:
    """Replay a recorded UserFlow on the desktop surface. No LLM."""

    def __init__(
        self,
        *,
        ctx=None,
        headed: bool = True,
        backend=None,
        hold_s: float = 3.0,
        step_pause: float = 0.4,
        policy=None,
    ) -> None:
        """Configure the desktop replay runner (headed Tk, policy, HITL)."""
        self.ctx = ctx
        self.headed = headed
        self.backend = backend
        self.hold_s = hold_s
        self.step_pause = step_pause
        self.policy = policy
        self.wanted_outputs: list[str] = []

    async def run(self, flow: UserFlow, values: dict[str, str] | None = None) -> LookupOutput:
        """Execute UserFlow steps on the desktop surface."""
        values = with_env_credentials(values)
        values.setdefault("app", "desktop:memsys")
        self.wanted_outputs = [item.name for item in (flow.outputs or [])]
        owns_window = self.backend is None
        backend = self.backend if self.backend is not None else TkMemsys(visible=self.headed)
        surface = DesktopSurface(backend)
        app = values.get("app") or AppId.DESKTOP_MEMSYS
        if self.ctx is not None:
            self.ctx.run.surface = SurfaceKind.DESKTOP
            self.ctx.log(
                "surface_opened",
                driver="tk",
                app=(flow.app or "memsys-desktop") if app == AppId.DESKTOP_MEMSYS else app,
                title=getattr(backend, "title", "MEMSYS Desktop"),
            )
        print()
        if app == AppId.DESKTOP_MEMSYS:
            print("Opening desktop app: MEMSYS Desktop")
        else:
            print(f"Opening desktop app: {app}")
        print("(This is a desktop window, not Chrome.)")
        print(f"Starting workflow: {flow.title}")
        observed: dict[str, str] = {}
        last = LookupOutput(outcome=ResultStatus.SUCCESS, detail="steps completed", observed=observed)
        try:
            for step in flow.steps:
                denied = self._policy(step, observed)
                if denied is not None:
                    return denied
                label = _step_label(step)
                locs = locators_for(SurfaceKind.DESKTOP, step)
                if self.ctx is not None:
                    self.ctx.log("surface_step", type=step.type, label=label, locators=locs[:6])
                print(f"Executing desktop step: {label}")
                await run_step(
                    surface,
                    step,
                    url=fill_placeholders(step.desktop_url or step.url or "", values),
                    value=apply_step_value(step, values),
                )
                body, screen = await surface.snapshot()
                observed.update(extract_outputs(body, screen, self.wanted_outputs))
                cond = classify_runtime(body, screen)
                if self.ctx is not None:
                    self.ctx.checkpointer.record(
                        step=label,
                        screen_id=screen,
                        kind=cond.kind,
                        detail=cond.message or body[:120],
                        observed=observed,
                        ok=cond.kind in {ConditionKind.CLEAR, ConditionKind.BUSINESS},
                    )
                if cond.kind == ConditionKind.BUSINESS:
                    last = LookupOutput(
                        outcome=ResultStatus.BUSINESS_OUTCOME,
                        outcome_code=cond.code,
                        screen_id=screen,
                        detail=cond.message,
                        observed=observed,
                    )
                    break
                if cond.kind == ConditionKind.VALIDATION:
                    return LookupOutput(
                        outcome=ResultStatus.FAILED,
                        step=label,
                        expected="valid input accepted by the form",
                        detail=cond.message,
                        screen_id=screen,
                        observed=observed,
                    )
                last = LookupOutput(
                    outcome=ResultStatus.SUCCESS,
                    screen_id=screen,
                    detail=cond.message or "steps completed",
                    observed=observed,
                )
                await asyncio.sleep(self.step_pause)
            if last.outcome == ResultStatus.SUCCESS and last.screen_id == ScreenId.MEMBER and not last.detail:
                last.detail = "member inquiry shows the savings balance"
            print("Desktop workflow finished")
            last.observed = {**observed, "surface": SurfaceKind.DESKTOP}
            if owns_window and self.headed:
                await asyncio.sleep(self.hold_s)
            return last
        finally:
            closer = getattr(backend, "close", None)
            if owns_window and closer is not None:
                closer()

    def _policy(self, step: UserFlowStep, observed: dict[str, str]) -> LookupOutput | None:
        """Block or require a human for this desktop step."""
        if self.policy is None:
            return None
        reason = self.policy.check_step(step)
        if not reason:
            return None
        label = _step_label(step)
        if self.ctx is not None:
            self.ctx.log("policy_denied", step=label, reason=reason)
        return LookupOutput(
            outcome=ResultStatus.FAILED,
            step=label,
            expected="step allowed by policy",
            detail=reason,
            observed=observed,
        )
