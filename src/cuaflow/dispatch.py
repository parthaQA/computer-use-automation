"""Record once, replay after that — same prompt command either way."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from cua.enums import LabMember, RunKind, SurfaceKind
from cuaflow.discover.lookup import explain_result, extra_inputs_from_prompt, lookup_engine, member_id_in_prompt, secrets_in_prompt
from cuaflow.discover.userflow import SuccessCriteria
from cuaflow.orchestrator.orchestrator import Orchestrator
from cuaflow.replay.engine import replay_engine
from cuaflow.rewrite import CredentialPromptBlocked, PromptRewriter, credentials_in_prompt
from cuaflow.runtime import silence_windows_aio_pipes
from cuaflow.safety.policy import load_policy
from cuaflow.settings import Settings, load_settings
from cuaflow.tenant import Tenant

EXAMPLE = (
    "open base_url, enter operator id and password, click on sign on button. "
    "search member id 12345 and click on search button"
)


def normalize(prompt: str) -> str:
    """Lowercase and collapse whitespace so catalog keys compare stably."""
    return " ".join(prompt.lower().split())


_OPEN_TARGET = re.compile(r"\bopen(?:\s+url)?\s+(?:app\b|(?:\{)?base_url\}?)\S*", re.I)


def catalog_key(text: str) -> str:
    """Treat 'open app' and 'open base_url' as the same recorded task."""
    return normalize(_OPEN_TARGET.sub("open target", text or ""))


class Catalog:
    """Remembers which canonical (rewritten) task already has a JSON file."""

    def __init__(self, folder: str | Path) -> None:
        """Point this catalog at the workflows folder and load catalog.json."""
        self.folder = Path(folder)
        self.path = self.folder / "catalog.json"
        self.recordings: list[dict[str, str]] = self._load()

    def _load(self) -> list[dict[str, str]]:
        """Read catalog.json recordings, or an empty list."""
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return list(data.get("recordings") or [])

    def _save(self) -> None:
        """Write catalog.json with the current recordings."""
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"recordings": self.recordings}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _stored_text(self, item: dict[str, str]) -> str:
        """Text used to match a catalog row (rewritten, else original prompt)."""
        return item.get("rewritten") or item.get("prompt") or ""

    def find(self, rewritten: str) -> Path | None:
        """Return the workflow JSON path if this rewritten goal was already recorded."""
        key = catalog_key(rewritten)
        for item in self.recordings:
            if catalog_key(self._stored_text(item)) != key:
                continue
            path = self.folder / item["file"]
            if path.is_file():
                return path
        return None

    def add(self, prompt: str, json_path: Path, rewritten: str) -> None:
        """Remember that this user prompt maps to this workflow file."""
        key = catalog_key(rewritten)
        name = json_path.name
        self.recordings = [item for item in self.recordings if catalog_key(self._stored_text(item)) != key]
        self.recordings.append({"prompt": prompt.strip(), "rewritten": rewritten.strip(), "file": name})
        self._save()

    def new_json_path(self, rewritten: str) -> Path:
        """Pick a new workflows/<hash>.json path for this rewritten goal."""
        digest = hashlib.sha256(catalog_key(rewritten).encode()).hexdigest()[:12]
        return self.folder / f"{digest}.json"


class FlowOrchestrator:
    """Rewrite the prompt with an LLM, then discover once / replay many."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Load settings and the workflow catalog for discover-or-replay."""
        self.settings = settings or load_settings()
        self.catalog = Catalog(self.settings.cua_workflows_dir)

    async def run(
        self,
        prompt: str,
        *,
        headed: bool = True,
        policy_only: bool = False,
        surface: str = SurfaceKind.WEB,
        tenant: Tenant | None = None,
        rediscover: bool = False,
    ) -> int:
        """Rewrite the goal, then replay a catalog hit or discover a new path."""
        silence_windows_aio_pipes()
        prompt = prompt.strip()
        leaked = credentials_in_prompt(prompt, self.settings)
        if leaked:
            print(leaked)
            return 1
        policy = load_policy(self.settings.cua_policy_path)
        if not headed and not policy_only:
            blocked = policy.check_discover(False)
            if blocked:
                print(f"Policy blocked this rewritten context: {blocked}")
                return 1
        print("Rewriting prompt...")
        try:
            canonical = await PromptRewriter(self.settings).rewrite(prompt)
        except CredentialPromptBlocked as exc:
            print(str(exc))
            return 1
        print(f"Canonical steps: {canonical.rewritten}")
        if canonical.success and canonical.success.checks:
            for check in canonical.success.checks:
                if check.capture:
                    print(f"Capture after {check.after}: {check.description or 'whether the member record was found'}")
                else:
                    print(f"Verify after {check.after}: " + "; ".join(check.must_see or [check.description]))
        else:
            print("No verify requested — completing the clicks is enough.")

        denied = policy.check_prompt(prompt) or policy.check_prompt(canonical.rewritten)
        if denied:
            print(f"Policy blocked this rewritten context: {denied}")
            if policy_only:
                return 1
            agent_prompt = canonical.for_agent(settings=self.settings, surface=surface, tenant=tenant)
            code, _saved = await self._discover(
                agent_prompt if policy.check_prompt(agent_prompt) else prompt,
                self.catalog.folder / "_not_saved.json",
                surface=surface,
                canonical_steps=canonical.steps,
                tenant=tenant,
                display_prompt=prompt,
            )
            return code

        seen = [label for _kind, label in policy.prompt_actions(prompt + " " + canonical.rewritten)]
        print("Policy allowed this rewritten context" + (f": {', '.join(dict.fromkeys(seen))}" if seen else "."))
        if policy_only:
            print("Stopping after policy check (--policy-only). Browser was not opened.")
            return 0

        recorded = self.catalog.find(canonical.rewritten)
        if recorded and not rediscover:
            print(f"Already recorded. Replaying {recorded} on surface={surface}")
            member_id = canonical.member_id or member_id_in_prompt(prompt) or LabMember.ACTIVE
            extra = extra_inputs_from_prompt(prompt + " " + canonical.rewritten)
            extra["member_id"] = member_id
            return await self._replay(
                recorded, prompt, member_id=member_id, headed=headed, inputs=extra, surface=surface, tenant=tenant
            )

        if rediscover:
            json_path = Path(self.settings.cua_workflows_dir) / "live_discover.json"
            print(f"Rediscover: LLM on the live browser (catalog replay skipped) → {json_path}")
        else:
            json_path = self.catalog.new_json_path(canonical.rewritten)
            print(f"Not recorded yet. Discovering on surface={surface} → {json_path}")
        code, saved = await self._discover(
            canonical.for_agent(settings=self.settings, surface=surface, tenant=tenant),
            json_path,
            success=canonical.success,
            surface=surface,
            canonical_steps=canonical.steps,
            tenant=tenant,
            display_prompt=prompt,
        )
        if saved and not rediscover:
            stored = prompt
            for password in secrets_in_prompt(prompt, self.settings):
                stored = stored.replace(password, "{password}")
            self.catalog.add(stored, saved, rewritten=canonical.rewritten)
            print(f"Recorded {saved}")
        return code

    async def _discover(
        self,
        prompt: str,
        json_path: Path,
        *,
        success: SuccessCriteria | None = None,
        surface: str = SurfaceKind.WEB,
        canonical_steps: list[str] | None = None,
        tenant: Tenant | None = None,
        display_prompt: str | None = None,
    ) -> tuple[int, Path | None]:
        """Start a headed LLM discover run and return (exit_code, json_path)."""
        from cuaflow.evidence import public_query

        shown = public_query(display_prompt or prompt)
        result = await self._start(
            kind=RunKind.DISCOVER,
            name=shown[:120],
            engine=lookup_engine(
                self.settings,
                prompt,
                headed=True,
                json_path=json_path,
                success=success,
                surface=surface,
                canonical_steps=canonical_steps,
                tenant=tenant,
                display_prompt=shown,
            ),
            prompt=shown,
            surface=surface,
            tenant=tenant,
        )
        saved = Path(result.outputs["userflow_path"]) if result and result.outputs.get("userflow_path") else None
        ok = result is not None and result.status.value in {"success", "business_outcome"}
        return (0 if ok else 1), saved

    async def _replay(
        self,
        json_path: Path,
        prompt: str,
        *,
        member_id: str,
        headed: bool,
        inputs: dict[str, str] | None = None,
        surface: str = SurfaceKind.WEB,
        tenant: Tenant | None = None,
    ) -> int:
        """Start a Playwright/desktop replay of an existing workflow JSON."""
        result = await self._start(
            kind=RunKind.REPLAY,
            name=str(json_path),
            engine=replay_engine(
                self.settings,
                member_id,
                headed=headed,
                workflow_path=json_path,
                inputs=inputs,
                surface=surface,
                tenant=tenant,
            ),
            prompt=prompt,
            capability=json_path.stem,
            surface=surface,
            tenant=tenant,
        )
        return 0 if result and result.status.value in {"success", "business_outcome"} else 1

    async def _start(
        self,
        *,
        kind: str,
        name: str,
        engine,
        prompt: str,
        capability: str | None = None,
        surface: str = SurfaceKind.WEB,
        tenant: Tenant | None = None,
    ):
        """Create an orchestrator run, wait for it, print the result."""
        orch = Orchestrator(runs_dir=self.settings.cua_runs_dir)
        secrets = {"param:operator": self.settings.memsys_operator}
        for password in secrets_in_prompt(prompt, self.settings):
            secrets["param:password"] = password
        run = await orch.start_run(
            kind=kind,
            name=name,
            engine=engine,
            secrets=secrets,
            surface=surface,
            tenant_id=tenant.id if tenant else "",
        )
        run = await orch.wait(run)
        result = run.result
        if result and result.outputs.get("evidence_dir"):
            from cuaflow.evidence import refresh_manifest, scrub_evidence_tree, sync_run_logs

            folder = Path(result.outputs["evidence_dir"])
            sync_run_logs(folder, Path(self.settings.cua_runs_dir) / run.run_id)
            refresh_manifest(folder)
            redactor = orch._redactors.get(run.run_id)
            scrub_evidence_tree(folder, redactor.scrub if redactor is not None else None)
        print()
        print(explain_result(result, capability=capability or kind))
        print(f"  run_id: {run.run_id}")
        print(f"  surface: {run.surface}")
        if run.tenant_id:
            print(f"  tenant: {run.tenant_id}")
        print(f"  events: {Path(self.settings.cua_runs_dir) / run.run_id / 'events.jsonl'}")
        if result and result.outputs.get("userflow_path"):
            print(f"  json: {result.outputs['userflow_path']}")
        if result and result.outputs.get("workflow_path"):
            print(f"  json: {result.outputs['workflow_path']}")
        if result and result.outputs.get("evidence_dir"):
            print(f"  evidence: {result.outputs['evidence_dir']}")
        return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse discover CLI flags (prompt, surface, tenant, rediscover)."""
    parser = argparse.ArgumentParser(
        description="Record a browser flow the first time; replay it on the same prompt after that."
    )
    parser.add_argument("prompt", nargs="*", help="Task prompt")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--surface",
        choices=[SurfaceKind.WEB, SurfaceKind.DESKTOP],
        default=SurfaceKind.WEB,
        help="web (Chrome, URL from tenant map or MEMSYS_BASE_URL) or desktop (no URL). Default: web.",
    )
    parser.add_argument(
        "--tenant",
        default=None,
        help="Tenant id. url and app come from tenants/tenants.json.",
    )
    parser.add_argument(
        "--tenant-url",
        default=None,
        help="Override the mapped URL. Default: the url for --tenant, else MEMSYS_BASE_URL.",
    )
    parser.add_argument(
        "--policy-only",
        action="store_true",
        help="Rewrite and check the allowlist, then stop. Do not open the browser.",
    )
    parser.add_argument(
        "--rediscover",
        action="store_true",
        help="Run the LLM against the live browser even if this prompt is already in the catalog. Does not replace catalog JSON.",
    )
    return parser.parse_args(argv)


def read_prompt(args: argparse.Namespace) -> str:
    """Join leftover argv words into the user goal string."""
    text = " ".join(args.prompt).strip()
    if text:
        return text
    try:
        return input("Task: ").strip()
    except EOFError:
        return ""
