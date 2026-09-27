"""Discover a MEMSYS path with a headed LLM, then save UserFlow JSON for replay."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from cua.enums import (
    InputName,
    LabMember,
    OutcomeCode,
    Placeholder,
    ResultStatus,
    SecretToken,
    SurfaceKind,
)

from ..checkpoint import record_from_browser
from ..evidence import (
    capture_screenshot,
    collect_webm,
    discover_dir,
    page_from_agent,
    public_query,
    save_failure,
    use_case_slug,
    write_discover_pack,
    write_history,
)
from ..orchestrator.models import RunResult
from ..orchestrator.orchestrator import RunContext
from ..runtime import launch_recorded_chrome, make_browser, make_llm
from ..safety.policy import load_policy, urls_in
from ..settings import Settings, expand_base_url, web_origin, web_start_url
from ..surface.web import WebSurface
from .from_canonical import flow_from_sentences
from .hitl import HITL_TASK, hitl_step_hook, hitl_tools, pause_agent, stop_agent, watch_operator_key
from .userflow import LOOKUP_FLOW_ID, SuccessCriteria, export_user_flow, write_user_flow

PASSWORD_PLACEHOLDER = SecretToken.PASSWORD
OPERATOR_PLACEHOLDER = SecretToken.OPERATOR

_MEMBER_ID = re.compile(r"\b(\d{5})\b")
_PRODUCT = re.compile(r"product(?:\s+type)?\s+(.+?)(?:\s+and\s+opening|\s*$)", re.I)
_DEPOSIT = re.compile(r"opening\s+deposit\s+(\d+(?:\.\d{1,2})?)", re.I)
_SKIP_EXPLAIN = {
    "password",
    "userflow_path",
    "workflow_path",
    "evidence_dir",
    "checkpoint_trail",
}


class LookupOutput(BaseModel):
    """What the agent (or replay classifier) extracted from the current page."""

    model_config = ConfigDict(extra="ignore")

    outcome: str = ResultStatus.SUCCESS
    detail: str = ""
    observed: dict[str, str] = Field(default_factory=dict)
    screen_id: str | None = None
    outcome_code: str | None = None
    step: str | None = None
    expected: str | None = None

    def to_run_result(
        self,
        outputs: dict[str, Any],
        *,
        default_step: str,
        default_expected: str,
    ) -> RunResult:
        """Turn this page extraction into the run's final RunResult."""
        if self.outcome == ResultStatus.SUCCESS:
            return RunResult.success(outputs)
        if self.outcome == ResultStatus.BUSINESS_OUTCOME:
            return RunResult.business_outcome(self.outcome_code or "BUSINESS", outputs)
        return RunResult.failed(
            self.step or default_step,
            self.expected or default_expected,
            self.detail or "failed",
            outputs=outputs,
        )


def lookup_engine(
    settings: Settings,
    prompt: str,
    *,
    headed: bool = True,
    json_path: str | Path | None = None,
    success: SuccessCriteria | None = None,
    surface: str = SurfaceKind.WEB,
    canonical_steps: list[str] | None = None,
    tenant: Any = None,
    display_prompt: str | None = None,
) -> Callable[[RunContext], Any]:
    """Return an orchestrator engine that discovers (web LLM or desktop steps)."""
    secrets: dict[str, str] = {}
    if settings.memsys_password:
        secrets[settings.memsys_password] = Placeholder.PASSWORD
    for item in secrets_in_prompt(prompt, settings):
        secrets[item] = Placeholder.PASSWORD

    async def engine(ctx: RunContext) -> RunResult:
        """Run headed discover on the requested surface."""
        if surface == SurfaceKind.DESKTOP:
            return await _run_desktop(
                ctx,
                settings,
                prompt,
                headed=headed,
                json_path=json_path,
                success=success,
                canonical_steps=canonical_steps,
                tenant=tenant,
                display_prompt=display_prompt,
                secrets=secrets,
            )
        return await _run_web(
            ctx,
            settings,
            prompt,
            secrets,
            headed=headed,
            json_path=json_path,
            success=success,
            tenant=tenant,
            display_prompt=display_prompt,
        )

    return engine


async def _discover_policy_check(
    policy: Any,
    agent: Any,
    ctx: RunContext,
    holder: dict[str, Any],
    headed: bool,
) -> str | None:
    """Block if the live page URL or last agent action is off the allowlist."""
    from ..checkpoint import agent_step_label
    from ..evidence import page_from_agent as _page

    page = await _page(agent)
    url = str(getattr(page, "url", "") or "")
    if url:
        blocked = policy.check_url(url)
        if blocked:
            label = agent_step_label(agent, 0)
            ctx.log("policy_denied", step=label, reason=blocked, url=url)
            return blocked
    try:
        history = getattr(getattr(agent, "history", None), "history", None) or []
        item = history[-1]
        actions = getattr(getattr(item, "model_output", None), "action", None) or []
        data = actions[-1].model_dump(exclude_none=True) if hasattr(actions[-1], "model_dump") else dict(actions[-1])
        name = next((key for key in data if key != "type"), None) or data.get("type") or ""
        blocked = policy.check_action(str(name))
        if blocked:
            return blocked
    except Exception:
        pass
    _ = (ctx, holder, headed)
    return None


async def _close_discover_browser(agent: Any) -> None:
    """Force-stop the headed session so video is flushed, then close the agent."""
    if agent is None:
        return
    session = getattr(agent, "browser_session", None) or getattr(agent, "browser", None)
    if session is not None and hasattr(session, "kill"):
        try:
            await session.kill()
            return
        except Exception:
            pass
    closer = getattr(agent, "close", None)
    if closer is None:
        return
    try:
        await closer()
    except Exception:
        pass


async def _run_web(
    ctx: RunContext,
    settings: Settings,
    prompt: str,
    secrets: dict[str, str],
    *,
    headed: bool,
    json_path: str | Path | None,
    success: SuccessCriteria | None,
    tenant: Any,
    display_prompt: str | None,
) -> RunResult:
    """Headed Chrome + LLM discover. Writes UserFlow JSON when the agent finishes."""
    from browser_use import Agent

    start_url = first_url(prompt, settings, origin=tenant.origin() if tenant is not None else None)
    member_id = member_id_in_prompt(prompt)
    policy = load_policy(settings.cua_policy_path)
    blocked = policy.check_url(start_url)
    if blocked:
        return RunResult.failed("policy", "url on the allowlist", blocked)
    blocked = policy.check_prompt(prompt)
    if blocked:
        return RunResult.failed("policy", "action on the allowlist", blocked)
    blocked = policy.check_discover(True)
    if blocked:
        return RunResult.failed("policy", "headed browser for discover", blocked)
    flow_path = Path(json_path or Path(settings.cua_workflows_dir) / f"{LOOKUP_FLOW_ID}.json")
    export_secrets = dict(secrets)
    if member_id:
        export_secrets[member_id] = "{member_id}"
    if settings.memsys_operator:
        export_secrets[settings.memsys_operator] = "{operator}"
        ctx.hide("param:operator", settings.memsys_operator)
    for value in secrets:
        ctx.hide("param:password", value)
    shown = public_query((display_prompt or prompt).split(" After ", 1)[0].strip())
    ctx.step("open_browser")
    ctx.log("discover_started", url=start_url, prompt=shown)
    ctx.log("surface_opened", driver="playwright", app="memsys", title="Chromium")
    ctx.log("policy_loaded", hosts=policy.allowed_hosts, actions=policy.allowed_actions)
    folder = discover_dir(settings, ctx, label=shown or prompt)
    try:
        llm = make_llm(settings)
    except ValueError as exc:
        return RunResult.failed("open_browser", "API key in .env", str(exc))

    holder: dict = {"stop": False, "ctx": ctx}
    result: RunResult | None = None
    page: Any = None
    history: Any = None
    agent: Any = None
    watcher: asyncio.Task | None = None
    chrome: Any = None
    video_dir = folder / "pw-video"
    video_dir.mkdir(parents=True, exist_ok=True)
    try:
        chrome = await launch_recorded_chrome(
            headed=True,
            viewport={"width": settings.cua_viewport_width, "height": settings.cua_viewport_height},
            video_dir=video_dir,
            profile_dir=folder / "pw-profile",
        )
        ctx.log("discover_video_armed", dir=str(video_dir), cdp=chrome.cdp_url)
    except Exception as exc:
        ctx.log("discover_video_skipped", reason=str(exc)[:240])
        chrome = None
    try:
        agent = Agent(
            task=agent_task(prompt, secrets, operator=settings.memsys_operator) + HITL_TASK,
            llm=llm,
            browser=make_browser(
                settings,
                headed=True,
                start_url=start_url,
                allowed_hosts=policy.allowed_hosts,
                cdp_url=chrome.cdp_url if chrome is not None else None,
                keep_alive=True,
            ),
            tools=hitl_tools(ctx, holder),
            sensitive_data={
                PASSWORD_PLACEHOLDER: next(iter(secrets), settings.memsys_password),
                OPERATOR_PLACEHOLDER: settings.memsys_operator,
            },
            output_model_schema=LookupOutput,
            use_judge=False,
            enable_signal_handler=False,
            calculate_cost=True,
            step_timeout=1800,
            save_conversation_path=str(folder / "conversation.json"),
        )
        holder["agent"] = agent
        hitl = hitl_step_hook(ctx, holder)

        async def on_step_end(agent: Any) -> None:
            """After each LLM step: HITL, checkpoint, screenshot, and policy check."""
            from ..checkpoint import agent_step_label

            await hitl(agent)
            if chrome is not None:
                await chrome.screenshot(folder / "screenshot.png")
            else:
                await capture_screenshot(agent, folder)
            if holder.get("approval_decision") == "cancel":
                stop_agent(agent)
                holder["stop"] = True
                return
            await record_from_browser(ctx.checkpointer, agent)
            ctx.log("surface_step", label=agent_step_label(agent, 0), driver="playwright")
            if holder.get("policy_denied"):
                return
            reason = await _discover_policy_check(policy, agent, ctx, holder, True)
            if reason:
                holder["policy_denied"] = reason
                ctx.log("policy_denied", reason=reason)
                pause_agent(agent)

        print(
            "Human in the loop is on. Press H in the browser window (not this terminal) to take control. "
            "Finish the page, then press Enter there to resume."
        )
        watcher = asyncio.create_task(watch_operator_key(ctx, holder))
        ctx.step("run_task")
        try:
            history = await agent.run(
                max_steps=settings.cua_max_discover_steps,
                on_step_start=hitl,
                on_step_end=on_step_end,
            )
        except Exception as exc:
            if holder.get("approval_decision") != "cancel":
                raise
            ctx.log("discover_closed", reason="operator cancelled approval", error=str(exc)[:200])
            history = getattr(agent, "history", None)
        if holder.get("approval_decision") == "cancel":
            result = await _finish_declined_approval(
                ctx,
                agent,
                folder,
                flow_path,
                member_id=member_id,
                shown=shown,
                secrets=export_secrets,
                success=success,
                tenant=tenant,
                prompt=prompt,
            )
            return result
        extracted = history.structured_output
        final_text = history.final_result() or ""
        denied = holder.get("policy_denied")
        agent_ok = (extracted and extracted.outcome in {"success", "business_outcome"}) or history.is_successful()
        if denied and not agent_ok:
            outputs: dict = {"evidence_dir": str(folder)}
            paths = await save_failure(
                await page_from_agent(agent),
                ctx,
                step="policy",
            )
            outputs.update(paths)
            ctx.checkpointer.attach(outputs)
            result = RunResult.failed(
                "policy",
                "allowed by policy",
                str(holder["policy_denied"]),
                outputs=outputs,
            )
            return result

        page = await page_from_agent(agent)
        if page is not None:
            try:
                body, screen = await WebSurface(page).snapshot()
                ctx.log("surface_snapshot", driver="playwright", screen_id=screen, chars=len(body or ""))
            except Exception:
                pass

        ctx.step("save_user_flow")
        flow = export_user_flow(
            agent,
            title=shown or public_query(prompt.split(" After ", 1)[0].strip()),
            secrets=export_secrets,
            success=success,
            member_id=member_id,
            flow_id=flow_path.stem,
            origin=tenant.origin() if tenant is not None else None,
        )
        write_user_flow(flow_path, flow)
        ctx.log(
            "user_flow_saved",
            path=str(flow_path),
            steps=len(flow.steps),
            success=history.is_successful(),
            agent_steps=history.number_of_steps(),
            final=final_text[:500],
        )

        outputs = {
            "member_id": member_id,
            "surface": SurfaceKind.WEB,
            "userflow_path": str(flow_path),
            "evidence_dir": str(folder),
            "detail": extracted.detail if extracted else final_text[:240],
        }
        if extracted:
            outputs["screen_id"] = extracted.screen_id
            outputs.update(extracted.observed)
            if extracted.step:
                outputs["step"] = extracted.step
            if extracted.expected:
                outputs["expected"] = extracted.expected
            result = extracted.to_run_result(
                outputs,
                default_step="run_task",
                default_expected=success.description if success else "query success",
            )
        elif history.is_successful() is False:
            result = RunResult.failed("run_task", "agent to finish the prompt", final_text or "agent failed")
        else:
            result = RunResult.success(outputs)
        if result.status == ResultStatus.FAILED:
            paths = await save_failure(
                await page_from_agent(agent),
                ctx,
                step=result.error.step if result.error else "run_task",
            )
            result.outputs.update(paths)
        ctx.checkpointer.attach(result.outputs)
        return result
    finally:
        holder["stop"] = True
        if watcher is not None:
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass
        shot = None
        video = None
        try:
            if chrome is not None:
                shot = await chrome.screenshot(folder / "screenshot.png")
            else:
                shot = await capture_screenshot(agent, folder, page)
        except Exception:
            pass
        if shot is not None:
            ctx.log("discover_screenshot", path=str(shot))
        if chrome is not None:
            if agent is not None:
                try:
                    await agent.close()
                except Exception:
                    pass
            try:
                saved = await chrome.save_video(folder / "discover.webm")
            except Exception:
                saved = None
            video = saved or collect_webm(folder, "discover.webm")
            chrome = None
        else:
            await _close_discover_browser(agent)
            await asyncio.sleep(0.2)
            video = collect_webm(folder, "discover.webm")
        if video is not None:
            ctx.log("discover_video", path=str(video))
        write_history(folder, history, ctx)
        if flow_path.is_file():
            try:
                slug = use_case_slug(prompt)
                shutil.copy2(flow_path, folder / f"{slug}.userflow.json")
            except Exception:
                pass
        steps = history.number_of_steps() if history is not None else None
        extra: dict[str, Any] = {"agent_steps": steps}
        if video is not None:
            extra["recorder"] = "playwright record_video_dir"
        pack = write_discover_pack(folder, ctx, extra=extra)
        if result is not None:
            result.outputs["evidence_dir"] = str(folder)
            if pack is not None:
                result.outputs["manifest"] = str(pack)
            if shot is not None:
                result.outputs["screenshot"] = str(shot)
            if video is not None:
                result.outputs["video"] = str(video)
        _ = headed


async def _finish_declined_approval(
    ctx: RunContext,
    agent: Any,
    folder: Path,
    flow_path: Path,
    *,
    member_id: str | None,
    shown: str,
    secrets: dict[str, str],
    success: SuccessCriteria | None,
    tenant: Any,
    prompt: str,
) -> RunResult:
    """Save the record so far and close discover after the operator cancelled approval."""
    observed: dict[str, str] = {}
    screen = None
    page = await page_from_agent(agent)
    if page is not None:
        try:
            body, screen = await WebSurface(page).snapshot()
            from ..replay.playwright_flow import extract_outputs

            observed = extract_outputs(body, screen)
            ctx.log("surface_snapshot", driver="playwright", screen_id=screen, chars=len(body or ""))
        except Exception:
            pass
    try:
        ctx.step("save_user_flow")
        flow = export_user_flow(
            agent,
            title=shown or public_query(prompt.split(" After ", 1)[0].strip()),
            secrets=secrets,
            success=success,
            member_id=member_id,
            flow_id=flow_path.stem,
            origin=tenant.origin() if tenant is not None else None,
        )
        write_user_flow(flow_path, flow)
        ctx.log("user_flow_saved", path=str(flow_path), steps=len(flow.steps))
    except Exception:
        pass
    detail = "operator cancelled sub-account approval"
    outputs: dict = {
        "member_id": member_id,
        "surface": SurfaceKind.WEB,
        "evidence_dir": str(folder),
        "detail": detail,
        **observed,
    }
    if screen:
        outputs["screen_id"] = screen
    if flow_path.is_file():
        outputs["userflow_path"] = str(flow_path)
    ctx.log("discover_closed", reason=detail, outcome_code=OutcomeCode.HUMAN_TAKEOVER)
    result = RunResult.business_outcome(OutcomeCode.HUMAN_TAKEOVER, outputs)
    ctx.checkpointer.attach(result.outputs)
    return result


async def _run_desktop(
    ctx: RunContext,
    settings: Settings,
    prompt: str,
    *,
    headed: bool,
    json_path: str | Path | None,
    success: SuccessCriteria | None,
    canonical_steps: list[str] | None,
    tenant: Any,
    display_prompt: str | None,
    secrets: dict[str, str],
) -> RunResult:
    """Desktop discover: map rewritten sentences onto the Tk MEMSYS surface (no LLM clicks)."""
    from ..replay.desktop_flow import DesktopFlowRunner
    from ..tenant import DEFAULT_APP

    policy = load_policy(settings.cua_policy_path)
    blocked = policy.check_discover(headed)
    if blocked:
        return RunResult.failed("policy", "headed browser for discover", blocked)
    blocked = policy.check_prompt(prompt)
    if blocked:
        return RunResult.failed("policy", "action on the allowlist", blocked)
    member_id = member_id_in_prompt(prompt)
    lines = [str(part).strip() for part in (canonical_steps or []) if str(part).strip()]
    if not lines:
        lines = [part.strip() for part in prompt.replace(". ", "\n").splitlines() if part.strip()]
    try:
        flow = flow_from_sentences(
            lines,
            title=public_query((display_prompt or prompt).split(" After ", 1)[0].strip())[:120]
            or "MEMSYS lookup",
            success=success,
            member_id=member_id,
            flow_id=Path(json_path).stem if json_path else LOOKUP_FLOW_ID,
        )
    except ValueError as exc:
        return RunResult.failed("load_workflow", "rewritten UI steps", str(exc))

    flow_path = Path(json_path or Path(settings.cua_workflows_dir) / f"{LOOKUP_FLOW_ID}.json")
    if settings.memsys_operator:
        ctx.hide("param:operator", settings.memsys_operator)
    for value in secrets_in_prompt(prompt, settings):
        ctx.hide("param:password", value)
    app = tenant.app_target() if tenant is not None else DEFAULT_APP
    values = {
        InputName.PASSWORD: settings.memsys_password,
        InputName.OPERATOR: settings.memsys_operator,
        InputName.MEMBER_ID: member_id or LabMember.ACTIVE,
        InputName.APP: app,
        InputName.BASE_URL: (tenant.origin() if tenant is not None else None) or web_origin(settings),
    }

    ctx.step("open_desktop")
    ctx.log("discover_started", prompt=prompt, target=app)
    ctx.step("run_task")
    runner = DesktopFlowRunner(headed=headed, ctx=ctx, policy=policy, hold_s=2.0 if headed else 0)
    extracted = await runner.run(flow, values)
    outputs: dict = {
        "member_id": member_id or values[InputName.MEMBER_ID],
        "surface": SurfaceKind.DESKTOP,
        "screen_id": extracted.screen_id,
        "detail": extracted.detail,
        **extracted.observed,
    }
    if extracted.outcome != ResultStatus.FAILED:
        ctx.step("save_user_flow")
        write_user_flow(flow_path, flow)
        outputs["userflow_path"] = str(flow_path)
        ctx.log("user_flow_saved", path=str(flow_path), steps=len(flow.steps))
    result = extracted.to_run_result(outputs, default_step="run_task")
    ctx.checkpointer.attach(result.outputs)
    return result


def result_summary(result: RunResult) -> str:
    """One-line console status: success, business_outcome CODE, or failed step/observed."""
    if result.status == ResultStatus.SUCCESS:
        detail = result.outputs.get("detail") or ""
        return f"success  {detail}".rstrip()
    if result.status == ResultStatus.BUSINESS_OUTCOME:
        detail = result.outputs.get("detail") or ""
        return f"business_outcome  {result.outcome_code}  {detail}".rstrip()
    err = result.error
    if err is None:
        return "failed"
    return f"failed  step={err.step}  expected={err.expected}  observed={err.observed}"


def explain_result(result: RunResult | None, *, capability: str | None = None) -> str:
    """Multi-line 'What happened' block for the operator (no password fields)."""
    lines = ["What happened"]
    if capability:
        lines.append(f"  capability: {capability}")
    if result is None:
        lines.append("  status: no result")
        return "\n".join(lines)
    lines.append(f"  status: {result.status.value}")
    if result.outcome_code:
        lines.append(f"  outcome_code: {result.outcome_code}")
    if result.error is not None:
        lines.append(f"  step: {result.error.step}")
        lines.append(f"  expected: {result.error.expected}")
        lines.append(f"  observed: {result.error.observed}")
    for key, value in result.outputs.items():
        if value in (None, "") or key in _SKIP_EXPLAIN or "password" in key.lower():
            continue
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def first_url(prompt: str, settings: Settings, *, origin: str | None = None) -> str:
    """Start URL from an explicit http(s) in the prompt, else MEMSYS_BASE_URL / tenant origin."""
    found = urls_in(prompt or "")
    if found:
        return found[0]
    expanded = expand_base_url(prompt or "", settings, origin=origin)
    found = urls_in(expanded)
    if found:
        return found[0]
    return web_start_url(settings, origin=origin)


def member_id_in_prompt(prompt: str) -> str | None:
    """Return the first 5-digit membership number in the prompt, if any."""
    match = _MEMBER_ID.search(prompt or "")
    return match.group(1) if match else None


def secrets_in_prompt(prompt: str, settings: Settings) -> dict[str, str]:
    """Map password literals from .env to {password} so rewrite can scrub them."""
    out: dict[str, str] = {}
    password = (getattr(settings, "memsys_password", None) or "").strip()
    if password:
        out[password] = str(Placeholder.PASSWORD)
    _ = prompt
    return out


def extra_inputs_from_prompt(prompt: str) -> dict[str, str]:
    """Parse product type and opening deposit from the user goal."""
    extras: dict[str, str] = {}
    product = _PRODUCT.search(prompt or "")
    if product:
        extras[InputName.PRODUCT] = product.group(1).strip().rstrip(".")
    deposit = _DEPOSIT.search(prompt or "")
    if deposit:
        extras[InputName.OPENING_DEPOSIT] = deposit.group(1)
    return extras


def agent_task(prompt: str, secrets: dict[str, str], operator: str = "") -> str:
    """LLM task text with operator id and password replaced by non-secret tokens."""
    text = prompt or ""
    if operator:
        text = text.replace(operator, OPERATOR_PLACEHOLDER)
    for value in secrets:
        if value:
            text = text.replace(value, PASSWORD_PLACEHOLDER)
    text = text.replace(Placeholder.OPERATOR, OPERATOR_PLACEHOLDER)
    text = text.replace(Placeholder.PASSWORD, PASSWORD_PLACEHOLDER)
    return text
