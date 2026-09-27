"""Replay Chrome Recorder JSON with Playwright. No LLM."""

from __future__ import annotations

import asyncio
import re
import time

from pathlib import Path
from typing import Any

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout, async_playwright

from cua.enums import ConditionKind, OutcomeCode, ResultStatus, ScreenId, SurfaceKind

from ..discover.hitl import (
    PageSession,
    handoff,
    is_approval_overlay,
    poll_takeover_page,
    read_overlay,
    watch_operator_key,
)
from ..checkpoint import Checkpointer
from ..discover.lookup import LookupOutput
from ..discover.userflow import SuccessCriteria, UserFlow, UserFlowStep, VerifyCheck
from ..evidence import collect_webm, save_failure
from ..orchestrator.orchestrator import RunContext
from ..runtime import silence_windows_aio_pipes
from ..surface.base import run_step
from ..surface.web import (
    WebSurface,
    chrome_selector_to_playwright,
    page_text,
    select_option_attempts,
    _iter_frames,
    _wait_frames,
)
from .conditions import classify_runtime, page_business_line

_BALANCE = re.compile(r"Current savings balance[^:]*:\s*([0-9,]+\.\d{2})", re.I)
_SUBACCT = re.compile(r"Open Sub-Account\s+[—–-]\s+\S+\s+(.+)", re.I)
_CONFIRM = re.compile(r"(NSC-\d{8}-\d+)", re.I)


def with_env_credentials(values: dict[str, str] | None = None, settings=None) -> dict[str, str]:
    """Operator id and password always come from .env, never from the prompt or extra inputs."""
    from ..settings import load_settings

    settings = settings or load_settings()
    out = dict(values or {})
    out["operator"] = settings.memsys_operator
    out["password"] = settings.memsys_password
    return out


def fill_placeholders(text: str, values: dict[str, str]) -> str:
    """Replace {member_id}, {operator}, x_password in recorded values."""
    password = values.get("password", "")
    if password:
        text = text.replace("<secret>x_password</secret>", password)
        text = text.replace("x_password", password)
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    member_id = values.get("member_id")
    if member_id and text.isdigit():
        return member_id
    return text


def _selector_blob(step: UserFlowStep) -> str:
    """Join all locators uppercase so we can detect OPR/MEMID fields."""
    return " ".join(item for group in (step.selectors or []) for item in group).upper()


def apply_step_value(step: UserFlowStep, values: dict[str, str]) -> str:
    """Fill placeholders, then override baked product/deposit values on older recordings."""
    value = fill_placeholders(step.value or "", values)
    blob = _selector_blob(step)
    deposit = values.get("opening_deposit")
    if deposit and ("AMT" in blob or "DEPOSIT" in blob or "AMOUNT" in blob):
        return deposit
    product = values.get("product")
    if product and ("PROD" in blob or "PRODUCT" in blob):
        return product
    operator = values.get("operator")
    if operator and ("OPR" in blob or "OPERATOR" in blob):
        return operator
    member_id = values.get("member_id")
    if member_id and ("MEMID" in blob or "MEMBER" in blob) and (not value or value.isdigit()):
        return member_id
    return value


class PlaywrightFlowRunner:
    """Replay a recorded UserFlow in Chrome with Playwright. No LLM."""

    def __init__(
        self,
        *,
        headed: bool = True,
        viewport: dict[str, int] | None = None,
        cdp_url: str | None = None,
        step_pause: float = 0.5,
        ctx: RunContext | None = None,
        holder: dict | None = None,
        policy=None,
        evidence_dir: Path | None = None,
    ) -> None:
        """Configure headed/headless Playwright replay and evidence folder."""
        self.headed = headed
        self.viewport = viewport or {"width": 1280, "height": 800}
        self.cdp_url = cdp_url
        self.step_pause = step_pause
        self.ctx = ctx
        self.holder = holder if holder is not None else {"stop": False}
        self.wanted_outputs: list[str] = []
        self.ck = ctx.checkpointer if ctx is not None else Checkpointer()
        self.policy = policy
        self.surface: WebSurface | None = None
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self._pw_context: Any = None
        self._page_video: Any = None

    async def run(self, flow: UserFlow, values: dict[str, str] | None = None) -> LookupOutput:
        """Open Chrome, run each UserFlow step, save screenshot and video."""
        silence_windows_aio_pipes()
        values = with_env_credentials(values)
        async with async_playwright() as p:
            if self.cdp_url:
                browser = await p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else await context.new_page()
            else:
                browser = await p.chromium.launch(headless=not self.headed)
                context_opts: dict[str, Any] = {"viewport": self.viewport}
                if self.evidence_dir is not None:
                    video_dir = self.evidence_dir / "pw-video"
                    video_dir.mkdir(parents=True, exist_ok=True)
                    context_opts["record_video_dir"] = str(video_dir)
                    context_opts["record_video_size"] = dict(self.viewport)
                    if self.ctx is not None:
                        self.ctx.log("replay_video_armed", dir=str(video_dir))
                context = await browser.new_context(**context_opts)
                self._pw_context = context
                page = await context.new_page()
                self._page_video = getattr(page, "video", None)
            holder = self.holder
            holder["stop"] = False
            holder["agent"] = PageSession(page)
            if self.ctx is not None:
                holder["ctx"] = self.ctx
            self.surface = WebSurface(page, poll=self._poll_hitl)
            if self.ctx is not None:
                self.ctx.run.surface = flow.surface or SurfaceKind.WEB
                self.ctx.log(
                    "surface_opened",
                    driver="playwright",
                    app=flow.app or "memsys",
                    title="Chromium",
                )
            self.wanted_outputs = [item.name for item in (flow.outputs or [])]
            watcher = None
            if self.ctx is not None and self.headed:
                holder["defer_takeover"] = True
                print(
                    "Human in the loop is on. Press H in the browser window (not this terminal) to take control. "
                    "Finish the page, then press Enter there to resume. "
                    "If an approval popup appears, automation stops and you finish (Approve or Cancel)."
                )
                watcher = asyncio.create_task(watch_operator_key(self.ctx, holder))
            try:
                print(f"Starting workflow: {flow.title}")
                observed: dict[str, str] = {}
                last = LookupOutput(outcome=ResultStatus.SUCCESS, detail="steps completed", observed=observed)
                previous: UserFlowStep | None = None
                for step in flow.steps:
                    if duplicate_click(previous, step):
                        print(f"Skipping duplicate click: {step.assertText}")
                        previous = step
                        continue
                    if await self._stop_for_approval():
                        return await self._finish(page, await self._human_owns_rest(page, browser, observed))
                    await self._takeover_if_requested()
                    denied = await self._policy_gate(step, observed)
                    if denied is not None:
                        return await self._finish(page, denied)
                    try:
                        await self._step(page, step, values)
                        await self._snapshot_page(page)
                    except Exception as exc:
                        recovered = await self._recover_step(page, step, observed, exc)
                        if recovered is not None:
                            if recovered.detail == "human completed the step while in control":
                                last = recovered
                                previous = step
                                continue
                            return await self._finish(page, recovered)
                        self.ck.record(
                            step=_step_label(step),
                            kind="failed",
                            detail=f"{type(exc).__name__}: {exc}",
                            observed=observed,
                            ok=False,
                        )
                        return await self._finish(
                            page,
                            LookupOutput(
                                outcome=ResultStatus.FAILED,
                                step=_step_label(step),
                                expected="step to complete",
                                detail=f"{type(exc).__name__}: {exc}",
                                observed=observed,
                            ),
                        )
                    previous = step
                    if self.holder.get("human_owns"):
                        return await self._finish(page, await self._human_owns_rest(page, browser, observed))
                    await self._pause_sleep()
                    if await self._stop_for_approval(wait=step.type == "click"):
                        return await self._finish(page, await self._human_owns_rest(page, browser, observed))
                    await self._takeover_if_requested()
                    if self.holder.get("human_owns"):
                        return await self._finish(page, await self._human_owns_rest(page, browser, observed))
                    skip_business = any(check.capture for check in _checks_after(flow.success, step))
                    runtime = await self._after_step(
                        page, step, observed=observed, skip_business=skip_business
                    )
                    if runtime is not None:
                        return await self._finish(page, runtime)
                    for check in _checks_after(flow.success, step):
                        wanted = None if check.capture else self.wanted_outputs
                        criteria = None if check.capture else _criteria_from_check(check)
                        result = await extract_page(page, criteria, values, wanted=wanted)
                        observed.update(result.observed)
                        last = result.model_copy(
                            update={
                                "observed": observed,
                                "step": result.step or _step_label(step),
                                "detail": result.detail or check.description,
                            }
                        )
                        if check.capture:
                            self.ck.record(
                                step=_step_label(step),
                                screen_id=result.screen_id,
                                kind=ConditionKind.BUSINESS if result.outcome == ResultStatus.BUSINESS_OUTCOME else ConditionKind.CLEAR,
                                detail=result.detail,
                                observed=observed,
                                ok=result.outcome != "failed",
                            )
                            if result.outcome == ResultStatus.FAILED:
                                return await self._finish(
                                    page,
                                    last.model_copy(
                                        update={
                                            "expected": result.expected or check.description or "query success",
                                        }
                                    ),
                                )
                            if result.outcome == ResultStatus.BUSINESS_OUTCOME:
                                return await self._finish(page, last)
                            continue
                        if result.outcome != "success":
                            self.ck.record(
                                step=_step_label(step),
                                screen_id=result.screen_id,
                                kind="failed",
                                detail=result.detail,
                                observed=observed,
                                ok=False,
                            )
                            return await self._finish(
                                page,
                                last.model_copy(
                                    update={
                                        "expected": result.expected or check.description or "query success",
                                    }
                                ),
                            )
                if _legacy_final(flow.success):
                    result = await extract_page(page, flow.success, values, wanted=self.wanted_outputs)
                    observed.update(result.observed)
                    result.observed = observed
                    self.ck.record(
                        step="final",
                        screen_id=result.screen_id,
                        kind=ConditionKind.CLEAR if result.outcome == ResultStatus.SUCCESS else result.outcome,
                        detail=result.detail,
                        observed=observed,
                        ok=result.outcome == ResultStatus.SUCCESS,
                    )
                    return await self._finish(page, result)
                print("Workflow execution finished")
                last.observed = observed
                return await self._finish(page, last)
            finally:
                holder["stop"] = True
                if watcher is not None:
                    watcher.cancel()
                    try:
                        await watcher
                    except (asyncio.CancelledError, Exception):
                        pass
                if holder.get("keep_browser"):
                    await _wait_until_browser_gone(page, browser)
                try:
                    if self._pw_context is not None:
                        await self._pw_context.close()
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass
                await asyncio.sleep(0.15)
                await self._save_replay_video()

    async def _snapshot_page(self, page: Page) -> None:
        """Overwrite evidence/screenshot.png from the live Playwright page."""
        if self.evidence_dir is None:
            return
        try:
            await page.screenshot(path=str(self.evidence_dir / "screenshot.png"), full_page=True)
        except Exception:
            pass

    async def _save_replay_video(self) -> None:
        """Copy Playwright's webm into evidence/replay.webm."""
        if self.evidence_dir is None:
            return
        dest = self.evidence_dir / "replay.webm"
        if self._page_video is not None:
            try:
                await self._page_video.save_as(str(dest))
            except Exception:
                collect_webm(self.evidence_dir, "replay.webm")
        else:
            collect_webm(self.evidence_dir, "replay.webm")
        if dest.is_file() and dest.stat().st_size > 0 and self.ctx is not None:
            self.ctx.log("replay_video", path=str(dest))

    async def _finish(self, page: Page, result: LookupOutput) -> LookupOutput:
        """Take a screenshot, attach failure dumps, return the result."""
        if self.evidence_dir is not None:
            shot = self.evidence_dir / "screenshot.png"
            try:
                await page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            if shot.is_file() and shot.stat().st_size > 0 and self.ctx is not None:
                self.ctx.log("replay_screenshot", path=str(shot))
        if result.outcome != "failed":
            return result
        paths = await save_failure(page, self.ctx, step=result.step)
        if paths:
            result.observed = {**result.observed, **paths}
        return result

    async def _policy_gate(self, step: UserFlowStep, observed: dict[str, str]) -> LookupOutput | None:
        """Block off-allowlist steps. Irreversible clicks need a human, or we stop."""
        if self.policy is None:
            return None
        label = _step_label(step)
        reason = self.policy.check_step(step)
        if reason:
            self.ck.record(step=label, kind="failed", detail=reason, observed=observed, ok=False)
            if self.ctx is not None:
                self.ctx.log("policy_denied", step=label, reason=reason)
            return LookupOutput(
                outcome=ResultStatus.FAILED,
                step=label,
                expected="step allowed by policy",
                detail=reason,
                observed=observed,
            )
        if not self.policy.is_risky_step(step):
            return None
        if not self.headed or self.ctx is None:
            detail = f"irreversible action blocked (headless): {label}"
            self.ck.record(step=label, kind="failed", detail=detail, observed=observed, ok=False)
            if self.ctx is not None:
                self.ctx.log("policy_denied", step=label, reason=detail)
            return LookupOutput(
                outcome=ResultStatus.FAILED,
                step=label,
                expected="human confirmation for an irreversible action",
                detail=detail,
                observed=observed,
            )
        print(f"Risky action: {label}. Confirm in the browser (Enter) or close the window to stop.")
        self.ctx.log("policy_confirm", step=label)
        mode = await handoff(self.ctx, self.holder, f"irreversible action: {label}")
        if mode == "abort":
            detail = "human declined irreversible action"
            self.ck.record(step=label, kind="failed", detail=detail, observed=observed, ok=False)
            return LookupOutput(
                outcome=ResultStatus.FAILED,
                step=label,
                expected="human confirmation for an irreversible action",
                detail=detail,
                observed=observed,
            )
        return None

    async def _poll_hitl(self) -> None:
        """Check for H / approval overlay between locator retries."""
        await self._arm_takeover()
        await self._takeover_if_requested()

    async def _step(self, page: Page, step: UserFlowStep, values: dict[str, str]) -> None:
        """Run one navigate/click/change/keyDown on the web surface."""
        print(f"Executing step: {step.type}")
        if self.surface is None:
            self.surface = WebSurface(page, poll=self._poll_hitl)
        if self.ctx is not None:
            from ..surface.locators import locators_for

            self.ctx.log(
                "surface_step",
                type=step.type,
                label=_step_label(step),
                locators=locators_for(SurfaceKind.WEB, step)[:6],
            )
        try:
            await run_step(
                self.surface,
                step,
                url=fill_placeholders(step.url or "", values),
                value=apply_step_value(step, values),
            )
        except LookupError:
            if step.type == "click" and await self._stop_for_approval():
                self.holder["human_owns"] = True
                return
            raise

    async def _stop_for_approval(self, *, wait: bool = False) -> bool:
        """True if the human-approval popup is up."""
        if self.ctx is None or not self.headed:
            return False
        overlay = ""
        rounds = 15 if wait else 1
        for _ in range(rounds):
            overlay = await read_overlay(self.holder.get("agent"))
            if is_approval_overlay(overlay):
                self.holder["human_owns"] = True
                self.holder["keep_browser"] = True
                self.ctx.log("human_owns_rest", reason=overlay[:160])
                print()
                print("Approval popup: automation stopped. It will not take the browser back.")
                print("Approve or Cancel, then finish the rest yourself. Close the window when done.")
                return True
            if not wait:
                break
            await asyncio.sleep(0.15)
        return False

    async def _pause_sleep(self) -> None:
        """Short pause so the operator can see the headed replay."""
        remaining = self.step_pause
        while remaining > 0:
            await self._arm_takeover()
            await self._takeover_if_requested()
            if self.holder.get("human_owns"):
                return
            slice_s = min(0.15, remaining)
            await asyncio.sleep(slice_s)
            remaining -= slice_s

    async def _arm_takeover(self) -> None:
        """Show the on-page H overlay so the operator can take control."""
        until = float(self.holder.get("ignore_takeover_until") or 0)
        if until and time.monotonic() < until:
            return
        if self.holder.get("in_handoff") or self.holder.get("takeover"):
            return
        try:
            if await poll_takeover_page(self.holder.get("agent")):
                self.holder["takeover"] = True
                print("H pressed — handing the browser to you. Finish the page, then press Enter there.")
        except Exception:
            return

    async def _takeover_if_requested(self) -> None:
        """Hand the live page to the human if they pressed H."""
        if self.ctx is None or not self.headed:
            return
        await self._arm_takeover()
        if not self.holder.get("takeover") or self.holder.get("human_owns"):
            return
        self.holder["takeover"] = False
        await handoff(self.ctx, self.holder, "operator took control")

    async def _recover_step(
        self,
        page: Page,
        step: UserFlowStep,
        observed: dict[str, str],
        exc: Exception,
    ) -> LookupOutput | None:
        """Retry, recover overlay, or fail after a step exception."""
        if _is_closed_error(exc):
            if observed.get("savings_balance") or observed.get("member_name"):
                return LookupOutput(
                    outcome=ResultStatus.SUCCESS,
                    detail="human finished after taking control",
                    observed=observed,
                )
            return LookupOutput(
                outcome=ResultStatus.BUSINESS_OUTCOME,
                outcome_code=OutcomeCode.HUMAN_TAKEOVER,
                detail="browser closed while human had control",
                observed=observed,
                step=_step_label(step),
            )
        try:
            closed = page.is_closed()
        except Exception:
            closed = False
        if closed:
            if observed.get("savings_balance") or observed.get("member_name"):
                return LookupOutput(
                    outcome=ResultStatus.SUCCESS,
                    detail="human finished after taking control",
                    observed=observed,
                )
            return LookupOutput(
                outcome=ResultStatus.BUSINESS_OUTCOME,
                outcome_code=OutcomeCode.HUMAN_TAKEOVER,
                detail="browser closed while human had control",
                observed=observed,
                step=_step_label(step),
            )
        try:
            body, screen = await page_text(page)
        except Exception:
            return None
        observed.update(extract_outputs(body, screen, self.wanted_outputs))
        runtime = classify_runtime(body, screen)
        if runtime.kind in {ConditionKind.BUSINESS, ConditionKind.VALIDATION, ConditionKind.TAKEOVER}:
            return classify_page(body, screen, wanted=self.wanted_outputs)
        if (
            observed.get("savings_balance")
            or observed.get("member_name")
            or observed.get("confirmation_number")
            or (screen or "").upper() == ScreenId.MEMBER
        ):
            print("Human already advanced the page; skipping leftover recording steps")
            return LookupOutput(
                outcome=ResultStatus.SUCCESS,
                screen_id=screen,
                detail="human completed the step while in control",
                observed=observed,
            )
        return None

    async def _after_step(
        self,
        page: Page,
        step: UserFlowStep,
        observed: dict[str, str],
        skip_business: bool = False,
    ) -> LookupOutput | None:
        """Checkpoint and run verify/capture checks after a step."""
        label = _step_label(step)
        for _ in range(3):
            body, screen = await page_text(page)
            observed.update(extract_outputs(body, screen, self.wanted_outputs))
            cond = classify_runtime(body, screen)
            if cond.kind == ConditionKind.CLEAR:
                self.ck.record_page(step=label, body=body, screen=screen, wanted=self.wanted_outputs)
                return None
            if cond.kind == ConditionKind.TAKEOVER:
                self.holder["human_owns"] = True
                self.holder["keep_browser"] = bool(self.headed)
                self.ck.record(
                    step=label,
                    screen_id=screen,
                    kind="takeover",
                    detail=cond.message,
                    observed=observed,
                    ok=False,
                )
                return LookupOutput(
                    outcome=ResultStatus.BUSINESS_OUTCOME,
                    outcome_code=OutcomeCode.HUMAN_TAKEOVER,
                    detail=cond.message,
                    screen_id=screen,
                    step=label,
                    observed=observed,
                )
            if cond.kind == ConditionKind.RECOVERABLE:
                print(f"Recoverable {cond.code}: {cond.message}")
                if self.ctx is not None:
                    self.ctx.log("recoverable", code=cond.code, step=label)
                if not await self._recover(page, cond.button):
                    self.ck.record(
                        step=label,
                        screen_id=screen,
                        kind="recoverable",
                        detail=cond.message,
                        observed=observed,
                        ok=False,
                    )
                    return LookupOutput(
                        outcome=ResultStatus.FAILED,
                        step=label,
                        expected=f"recover {cond.code} by clicking {cond.button}",
                        detail=cond.message,
                        screen_id=screen,
                        observed=observed,
                    )
                await asyncio.sleep(0.4)
                continue
            if cond.kind == ConditionKind.BUSINESS:
                if skip_business:
                    return None
                print(f"Business outcome {cond.code}: {cond.message}")
                self.ck.record(
                    step=label,
                    screen_id=screen,
                    kind=ConditionKind.BUSINESS,
                    detail=cond.message,
                    observed=observed,
                    ok=False,
                )
                return LookupOutput(
                    outcome=ResultStatus.BUSINESS_OUTCOME,
                    outcome_code=cond.code,
                    detail=cond.message,
                    screen_id=screen,
                    step=label,
                    observed=observed,
                )
            self.ck.record(
                step=label,
                screen_id=screen,
                kind="validation",
                detail=cond.message,
                observed=observed,
                ok=False,
            )
            return LookupOutput(
                outcome=ResultStatus.FAILED,
                step=label,
                expected="valid input accepted by the form",
                detail=cond.message,
                screen_id=screen,
                observed=observed,
            )
        return LookupOutput(
            outcome=ResultStatus.FAILED,
            step=label,
            expected="recoverable condition to clear",
            detail="still blocked after retries",
            observed=observed,
        )

    async def _recover(self, page: Page, button: str | None) -> bool:
        """Click Continue Session / Dismiss when a recoverable page appears."""
        if not button:
            await asyncio.sleep(2)
            await _wait_frames(page)
            return True
        for frame in _iter_frames(page):
            loc = frame.locator(
                f'input[type="submit"][value="{button}"], input[type="button"][value="{button}"]'
            ).first
            try:
                await loc.wait_for(state="visible", timeout=2500)
            except PlaywrightTimeout:
                continue
            try:
                await loc.click(timeout=3000)
            except Exception:
                continue
            await _wait_frames(page)
            return True
        return False

    async def _human_owns_rest(self, page: Page, browser, observed: dict[str, str]) -> LookupOutput:
        """Human finished or aborted; return the outcome from the current page."""
        print("Workflow execution stopped — human owns the remaining steps")
        return LookupOutput(
            outcome=ResultStatus.BUSINESS_OUTCOME,
            outcome_code=OutcomeCode.HUMAN_TAKEOVER,
            detail="approval popup: human owns remaining steps (Approve or Cancel)",
            observed=observed,
        )


def duplicate_click(previous: UserFlowStep | None, step: UserFlowStep) -> bool:
    """True if this click is the same control as the previous click."""
    if previous is None or step.type != "click" or previous.type != "click":
        return False
    label = (step.assertText or "").strip()
    return bool(label) and label == (previous.assertText or "").strip()


async def _wait_until_browser_gone(page: Page, browser) -> None:
    """Wait until the operator closes the headed browser."""
    while True:
        try:
            if not browser.is_connected():
                return
            if page.is_closed():
                return
        except Exception:
            return
        await asyncio.sleep(0.4)


def _is_closed_error(exc: BaseException) -> bool:
    """True if Playwright raised because the page/browser closed."""
    name = type(exc).__name__
    text = str(exc).lower()
    return "TargetClosed" in name or "has been closed" in text or "target closed" in text


def _step_label(step: UserFlowStep) -> str:
    """Short label for a web replay step."""
    name = step.assertText or step.value or step.url or ""
    return f"{step.type}:{name}".strip(":")[:120]


def step_matches(step: UserFlowStep, after: str) -> bool:
    """True if a verify check belongs after this recorded step."""
    needle = (after or "").lower().strip()
    if not needle:
        return False
    haystacks = [step.assertText or "", step.value or "", step.key or ""]
    for group in step.selectors or []:
        haystacks.extend(group)
    blob = " ".join(haystacks).lower()
    if needle in blob:
        return True
    for prefix in ("click ", "click on ", "select ", "enter ", "set "):
        if needle.startswith(prefix) and needle[len(prefix) :] in blob:
            return True
    return False


def _checks_after(success: SuccessCriteria | None, step: UserFlowStep) -> list[VerifyCheck]:
    """Success checks that run immediately after this step."""
    if not success:
        return []
    return [check for check in success.checks if step_matches(step, check.after)]


def _legacy_final(success: SuccessCriteria | None) -> bool:
    """True if this flow still uses old final-page must_see lists."""
    return bool(success and not success.checks and (success.must_see or success.screen_id))


def _criteria_from_check(check: VerifyCheck) -> SuccessCriteria:
    """Turn one VerifyCheck into SuccessCriteria for extract_page."""
    return SuccessCriteria(
        description=check.description,
        must_see=check.must_see,
        must_not_see=check.must_not_see,
        screen_id=check.screen_id,
    )


def _fill_criteria(criteria: SuccessCriteria, values: dict[str, str]) -> SuccessCriteria:
    """Fill {placeholders} inside success criteria strings."""
    return SuccessCriteria(
        description=fill_placeholders(criteria.description, values),
        checks=criteria.checks,
        must_see=[fill_placeholders(item, values) for item in criteria.must_see],
        must_not_see=[fill_placeholders(item, values) for item in criteria.must_not_see],
        screen_id=criteria.screen_id,
    )


def read_page(body: str, screen: str | None = None) -> dict[str, str]:
    """All facts this runner knows how to read from a MEMSYS page."""
    facts: dict[str, str] = {}
    if "Name:" in body:
        facts["member_name"] = body.split("Name:", 1)[1].split("Status:", 1)[0].strip()
    match = _BALANCE.search(body)
    if match:
        facts["savings_balance"] = match.group(1)
    if "member_name" not in facts:
        found = _SUBACCT.search(body)
        if found:
            facts["member_name"] = found.group(1).strip()
    confirm = _CONFIRM.search(body)
    if confirm:
        facts["confirmation_number"] = confirm.group(1)
    if screen:
        facts["screen_id"] = screen
    return facts


def extract_outputs(
    body: str,
    screen: str | None = None,
    wanted: list[str] | None = None,
) -> dict[str, str]:
    """Keep only the keys listed on the capability outputs[]. Empty list = all known keys."""
    facts = read_page(body.replace("\xa0", " "), screen)
    names = [name for name in (wanted or []) if name]
    if not names:
        return facts
    return {name: facts[name] for name in names if name in facts}


def page_facts(body: str, screen: str | None = None) -> dict[str, str]:
    """Pull named outputs (balance, message, …) from page text."""
    return extract_outputs(body, screen)


def classify_page(
    body: str,
    screen: str | None,
    criteria: SuccessCriteria | None = None,
    values: dict[str, str] | None = None,
    wanted: list[str] | None = None,
) -> LookupOutput:
    """Map page text to LookupOutput (success / business / fail)."""
    body = body.replace("\xa0", " ")
    runtime = classify_runtime(body, screen)
    observed = _with_lookup_capture(body, screen, extract_outputs(body, screen, wanted), runtime)
    if runtime.kind == ConditionKind.BUSINESS:
        return LookupOutput(
            outcome=ResultStatus.BUSINESS_OUTCOME,
            outcome_code=runtime.code,
            screen_id=screen,
            detail=observed.get("message") or runtime.message,
            observed=observed,
        )
    if runtime.kind == ConditionKind.TAKEOVER:
        return LookupOutput(
            outcome=ResultStatus.BUSINESS_OUTCOME,
            outcome_code=runtime.code,
            screen_id=screen,
            detail=runtime.message,
            observed=observed,
        )
    if runtime.kind == ConditionKind.VALIDATION:
        return LookupOutput(
            outcome=ResultStatus.FAILED,
            screen_id=screen,
            expected="valid input accepted by the form",
            detail=runtime.message,
            observed=observed,
        )
    if runtime.kind == ConditionKind.RECOVERABLE:
        return LookupOutput(
            outcome=ResultStatus.FAILED,
            screen_id=screen,
            expected=f"recover {runtime.code} by clicking {runtime.button}",
            detail=runtime.message,
            observed=observed,
        )

    folded = body.lower()
    if criteria:
        check = _fill_criteria(criteria, values or {})
        for phrase in check.must_not_see:
            if phrase and phrase.lower() in folded:
                return LookupOutput(
                    outcome=ResultStatus.FAILED,
                    screen_id=screen,
                    expected=check.description or "page without that error",
                    detail=f"saw {phrase!r}",
                    observed=observed,
                )
        missing: list[str] = []
        for phrase in check.must_see:
            if phrase and phrase.lower() not in folded:
                missing.append(phrase)
        if check.screen_id and screen and check.screen_id.upper() != screen.upper():
            missing.append(f"screen {check.screen_id}")
        if missing:
            return LookupOutput(
                outcome=ResultStatus.FAILED,
                screen_id=screen,
                expected=check.description or "query success",
                detail="missing: " + ", ".join(missing),
                observed=observed,
            )
        return LookupOutput(
            outcome=ResultStatus.SUCCESS,
            screen_id=screen,
            detail=check.description or "steps completed",
            observed=observed,
        )

    return LookupOutput(
        outcome=ResultStatus.SUCCESS,
        screen_id=screen,
        detail="steps completed",
        observed=observed,
    )


def _with_lookup_capture(
    body: str,
    screen: str | None,
    observed: dict[str, str],
    runtime,
) -> dict[str, str]:
    """Note found vs not-found on the page. Does not fail the run by itself."""
    facts = dict(observed)
    if runtime.kind == ConditionKind.BUSINESS:
        facts["message"] = page_business_line(body) or runtime.message
        facts["member_found"] = "false" if runtime.code == OutcomeCode.MEMBER_NOT_FOUND else "true"
    elif facts.get("member_name") or (screen or "").upper() in {ScreenId.MEMBER, ScreenId.SUBACCT}:
        facts["member_found"] = "true"
    return facts


async def extract_page(
    page: Page,
    criteria: SuccessCriteria | None = None,
    values: dict[str, str] | None = None,
    wanted: list[str] | None = None,
) -> LookupOutput:
    """Snapshot the page, classify it, and return LookupOutput."""
    body, screen = await page_text(page)
    return classify_page(body, screen, criteria, values, wanted=wanted)
