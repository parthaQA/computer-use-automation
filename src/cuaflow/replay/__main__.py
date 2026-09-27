"""Replay a capability by name (or a JSON file) with Playwright. No LLM.

    python -m cuaflow.replay lookup_savings_balance --member-id 12345 --surface web
    python -m cuaflow.replay lookup_savings_balance --member-id 12345 --tenant lab
    python -m cuaflow.replay lookup_savings_balance --member-id 12345 --tenant cu-a
    python -m cuaflow.replay lookup_savings_balance --member-id 12345 --surface desktop --tenant lab
    python -m cuaflow.replay workflows/lookup_savings_balance.json --member-id 12345
    python -m cuaflow.replay open_subaccount --member-id 12345 --set product=02 --set opening_deposit=25.00
    python -m cuaflow.replay --list
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from cua.enums import LabMember, RunKind, SurfaceKind
from cuaflow.discover.lookup import explain_result
from cuaflow.discover.userflow import find_capability, list_capabilities
from cuaflow.orchestrator.orchestrator import Orchestrator
from cuaflow.replay.engine import parse_set, replay_engine, surface_of_workflow
from cuaflow.settings import load_settings
from cuaflow.tenant import make_tenant


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse replay CLI (capability name, member-id, surface, tenant)."""
    parser = argparse.ArgumentParser(description="Replay a capability by name, or a JSON file")
    parser.add_argument("name", nargs="?", help="Capability id (lookup_savings_balance) or path to JSON")
    parser.add_argument("--member-id", default=None, help="Membership number (sets member_id)")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="name=value",
        help="Capability input, repeatable. Example: --set product=02 --set opening_deposit=25.00",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--surface",
        choices=[SurfaceKind.WEB, SurfaceKind.DESKTOP],
        default=None,
        help="web (Chrome, URL from .env) or desktop (no URL). Default: the file's surface field.",
    )
    parser.add_argument(
        "--tenant",
        default=None,
        help="Tenant id. url and app come from tenants/tenants.json (same vendor JSON).",
    )
    parser.add_argument(
        "--tenant-url",
        default=None,
        help="Override the mapped URL. Default: the url for --tenant, else MEMSYS_BASE_URL.",
    )
    parser.add_argument("--list", action="store_true", help="List capabilities in the workflows folder")
    parser.add_argument(
        "--cdp",
        default=None,
        help="Attach to an existing Chromium via CDP, e.g. http://localhost:9222",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    """CLI entry: replay a saved workflow JSON with Playwright (no LLM)."""
    args = parse_args(argv)
    settings = load_settings()
    if args.list:
        rows = list_capabilities(settings.cua_workflows_dir)
        if not rows:
            print("no capabilities recorded yet")
            return 0
        for row in rows:
            print(f"{row['id']}  v{row['version']}  {row['title']}  ({row['file']})")
        return 0

    name = (args.name or "").strip()
    if not name:
        print("a capability name is required", file=sys.stderr)
        print("example: python -m cuaflow.replay lookup_savings_balance --member-id 12345", file=sys.stderr)
        return 2

    try:
        params = parse_set(args.set)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.member_id is not None:
        params["member_id"] = args.member_id
    params.setdefault("member_id", LabMember.ACTIVE)
    member_id = params["member_id"]

    path = find_capability(settings.cua_workflows_dir, name)
    if path is None:
        print(f"capability not found: {name}", file=sys.stderr)
        return 2
    surface = (args.surface or surface_of_workflow(path)).lower()
    try:
        tenant = make_tenant(args.tenant, args.tenant_url, path=settings.cua_tenants_path)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"capability={name}  json={path}  surface={surface}")
    if tenant:
        print(f"tenant={tenant.id}  url={tenant.origin() or '(env MEMSYS_BASE_URL)'}  app={tenant.app_target()}")
    shown = ", ".join(
        f"{key}={value}" for key, value in params.items() if key not in {"password", "operator"}
    )
    print(f"inputs: {shown}")
    orch = Orchestrator(runs_dir=settings.cua_runs_dir)
    secrets = {
        "param:password": settings.memsys_password,
        "param:operator": settings.memsys_operator,
    }
    run = await orch.start_run(
        kind=RunKind.REPLAY,
        name=name,
        surface=surface,
        tenant_id=tenant.id if tenant else "",
        engine=replay_engine(
            settings,
            member_id,
            headed=not args.headless,
            workflow_path=path,
            cdp_url=args.cdp,
            inputs=params,
            surface=surface,
            tenant=tenant,
        ),
        secrets=secrets,
    )
    run = await orch.wait(run)
    result = run.result
    if result and result.outputs.get("evidence_dir"):
        from cuaflow.evidence import refresh_manifest, scrub_evidence_tree, sync_run_logs

        folder = Path(result.outputs["evidence_dir"])
        sync_run_logs(folder, Path(settings.cua_runs_dir) / run.run_id)
        refresh_manifest(folder)
        redactor = orch._redactors.get(run.run_id)
        scrub_evidence_tree(folder, redactor.scrub if redactor is not None else None)
    print()
    print(explain_result(result, capability=name))
    print(f"  run_id: {run.run_id}")
    print(f"  surface: {run.surface}")
    if run.tenant_id:
        print(f"  tenant: {run.tenant_id}")
    print(f"  json: {path}")
    print(f"  events: {Path(settings.cua_runs_dir) / run.run_id / 'events.jsonl'}")
    if result and result.outputs.get("workflow_replay"):
        print(f"  workflow: {result.outputs['workflow_replay']}")
    if result and result.outputs.get("evidence_dir"):
        print(f"  evidence: {result.outputs['evidence_dir']}")
    return 0 if result and result.status.value in {"success", "business_outcome"} else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
