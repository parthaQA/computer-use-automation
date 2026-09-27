from __future__ import annotations

import json
from pathlib import Path

from cua.enums import RunKind, SurfaceKind
from ..safety.policy import load_policy
from ..discover.lookup import LookupOutput
from ..discover.userflow import LOOKUP_FLOW_ID, MEMBER_ID_INPUT, OPERATOR_INPUT, PASSWORD_INPUT, UserFlow, UserFlowStep
from ..orchestrator.models import RunResult
from ..orchestrator.orchestrator import Engine, RunContext
from ..settings import Settings, web_origin
from ..tenant import DEFAULT_APP, Tenant
from .desktop_flow import DesktopFlowRunner
from .playwright_flow import PlaywrightFlowRunner, fill_placeholders


def open_target(step: UserFlowStep, kind: str, values: dict[str, str]) -> str:
    """URL or desktop app id for this step after filling placeholders."""
    raw = (step.desktop_url if kind == SurfaceKind.DESKTOP and step.desktop_url else None) or step.url or ""
    return fill_placeholders(raw, values)


def surface_of_workflow(path: str | Path) -> str:
    """Read the default surface field from a workflow JSON file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return SurfaceKind.WEB
    return str(data.get("surface") or SurfaceKind.WEB).lower()


def parse_set(items: list[str] | None) -> dict[str, str]:
    """Turn --set name=value flags into a dict. Last value wins for a repeated name."""
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"expected name=value, got {item!r}")
        name, value = item.split("=", 1)
        name = name.strip()
        if not name:
            raise ValueError(f"expected name=value, got {item!r}")
        out[name] = value
    return out


def workflow_inputs(
    settings: Settings,
    member_id: str,
    extra: dict[str, str] | None = None,
    tenant: Tenant | None = None,
) -> dict[str, str]:
    """Build replay inputs from .env credentials plus member_id."""
    origin = (tenant.origin() if tenant else None) or web_origin(settings)
    app = tenant.app_target() if tenant else DEFAULT_APP
    values = {
        PASSWORD_INPUT: settings.memsys_password,
        OPERATOR_INPUT: settings.memsys_operator,
        MEMBER_ID_INPUT: member_id,
        "base_url": origin,
        "app": app,
    }
    for name, value in (extra or {}).items():
        if name in {PASSWORD_INPUT, OPERATOR_INPUT, "base_url", "app"} or not value:
            continue
        values[name] = value
    values[MEMBER_ID_INPUT] = member_id
    values[PASSWORD_INPUT] = settings.memsys_password
    values[OPERATOR_INPUT] = settings.memsys_operator
    return values


def replay_engine(
    settings: Settings,
    member_id: str,
    *,
    headed: bool = True,
    workflow_path: str | Path | None = None,
    cdp_url: str | None = None,
    inputs: dict[str, str] | None = None,
    surface: str | None = None,
    tenant: Tenant | None = None,
) -> Engine:
    """Return the orchestrator engine that runs a UserFlow without an LLM."""
    async def engine(ctx: RunContext) -> RunResult:
        """Load the JSON, open web or desktop, run steps, write evidence."""
        path = Path(workflow_path or Path(settings.cua_workflows_dir) / f"{LOOKUP_FLOW_ID}.json")
        if not path.exists():
            return RunResult.failed(
                "load_workflow",
                str(path),
                "user-flow JSON missing — run python -m cuaflow.discover first",
            )

        ctx.step("load_workflow")
        flow = UserFlow.model_validate_json(path.read_text(encoding="utf-8"))
        kind = (surface or flow.surface or SurfaceKind.WEB).lower()
        if kind not in {SurfaceKind.WEB, SurfaceKind.DESKTOP}:
            return RunResult.failed("load_workflow", "web or desktop", f"unknown surface {kind!r}")
        ctx.run.surface = kind
        if tenant:
            ctx.run.tenant_id = tenant.id
            ctx.log(
                "tenant_bound",
                tenant=tenant.id,
                url=tenant.origin() or "",
                app=tenant.app_target(),
            )
        ctx.log(
            "workflow_loaded",
            path=str(path),
            id=flow.id,
            version=flow.version,
            title=flow.title,
            default_surface=flow.surface,
            surface=kind,
            steps=len(flow.steps),
            inputs=[item.name for item in flow.inputs],
        )

        params = workflow_inputs(settings, member_id, extra=inputs, tenant=tenant)
        ctx.log(
            "workflow_inputs",
            names=[item.name for item in flow.inputs],
            provided=[name for name in params if name != PASSWORD_INPUT],
        )

        if kind == SurfaceKind.DESKTOP:
            ctx.step("run_desktop")
            runner = DesktopFlowRunner(
                headed=headed,
                ctx=ctx,
                policy=load_policy(settings.cua_policy_path),
            )
            try:
                extracted = await runner.run(flow, params)
            except Exception as exc:
                ctx.checkpointer.attach({})
                return RunResult.failed("run_desktop", "replay to finish", f"{type(exc).__name__}: {exc}")
            result = _to_result(extracted, member_id, path, inputs=params, surface=SurfaceKind.DESKTOP)
            ctx.checkpointer.attach(result.outputs)
            return result

        ctx.step("run_playwright")
        from ..evidence import (
            copy_replay_source,
            discover_dir,
            use_case_slug,
            write_replay_pack,
        )

        folder = discover_dir(settings, ctx, kind=RunKind.REPLAY, label=flow.title, flow_id=flow.id)
        slug = use_case_slug(flow.title, flow_id=flow.id)
        source_copy = copy_replay_source(folder, path, slug=slug)
        ctx.log("replay_evidence_armed", dir=str(folder), workflow=str(source_copy))
        holder: dict = {"stop": False, "defer_takeover": True}
        runner = PlaywrightFlowRunner(
            headed=headed,
            viewport={"width": settings.cua_viewport_width, "height": settings.cua_viewport_height},
            cdp_url=cdp_url,
            ctx=ctx,
            holder=holder,
            policy=load_policy(settings.cua_policy_path),
            evidence_dir=folder,
        )
        try:
            extracted = await runner.run(flow, params)
        except Exception as exc:
            ctx.checkpointer.attach({})
            return RunResult.failed("run_playwright", "replay to finish", f"{type(exc).__name__}: {exc}")
        extra = {"workflow": source_copy.name, "source_json": str(path)}
        webm = folder / "replay.webm"
        shot = folder / "screenshot.png"
        if webm.is_file():
            extra["video"] = "replay.webm"
            extra["recorder"] = "playwright record_video_dir"
        pack = write_replay_pack(folder, ctx, extra=extra)
        result = _to_result(extracted, member_id, path, inputs=params, surface=SurfaceKind.WEB)
        result.outputs["evidence_dir"] = str(folder)
        result.outputs["evidence_manifest"] = str(pack)
        result.outputs["workflow_replay"] = str(source_copy)
        if webm.is_file():
            result.outputs["video"] = str(webm)
        if shot.is_file():
            result.outputs["screenshot"] = str(shot)
        ctx.checkpointer.attach(result.outputs)
        print(f"evidence: {folder}")
        return result

    return engine


def _to_result(
    extracted: LookupOutput,
    member_id: str,
    path: Path,
    inputs: dict[str, str] | None = None,
    surface: str = SurfaceKind.WEB,
) -> RunResult:
    """Turn extracted page output into a RunResult for this member."""
    passed = {
        name: value
        for name, value in (inputs or {}).items()
        if name not in {PASSWORD_INPUT, OPERATOR_INPUT} and value
    }
    outputs = {
        "surface": surface,
        "member_id": member_id,
        **passed,
        "screen_id": extracted.screen_id,
        "detail": extracted.detail,
        "workflow_path": str(path),
        **extracted.observed,
    }
    if extracted.outcome_code:
        outputs["outcome_code"] = extracted.outcome_code
    if extracted.step:
        outputs["step"] = extracted.step
    if extracted.expected:
        outputs["expected"] = extracted.expected
    return extracted.to_run_result(
        outputs,
        default_step="run_desktop" if surface == SurfaceKind.DESKTOP else "run_playwright",
        default_expected=extracted.detail or "query success",
    )
