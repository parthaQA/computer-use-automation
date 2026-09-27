from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..checkpoint import Checkpointer
from ..safety.redact import Redactor
from cua.enums import ResumeMode, SurfaceKind
from .lease import ControlLease, Driver
from .models import HumanAction, Intervention, InterventionStatus, ResultStatus, Run, RunResult, now
from .states import TERMINAL_STATES, RunState, check_transition

RESUME_MODES = {ResumeMode.RETRY_STEP, ResumeMode.CONTINUE_AFTER, ResumeMode.ABORT}

# Which final state matches which final result
FINAL_STATE = {
    ResultStatus.SUCCESS: RunState.SUCCEEDED,
    ResultStatus.BUSINESS_OUTCOME: RunState.BUSINESS_OUTCOME,
    ResultStatus.FAILED: RunState.FAILED,
}


class RunAborted(Exception):
    """Raised inside the engine when a human chose 'abort'."""


class RunContext:
    """What the engine (the code doing the actual work) gets to talk to the orchestrator."""

    def __init__(self, orch: "Orchestrator", run: Run, lease: ControlLease) -> None:
        """Bind this context to one Run (logs, redaction, checkpoints)."""
        self.run = run
        self.epoch = 0                       # automation's current ticket number
        self.current_step: str | None = None
        self._orch = orch
        self._lease = lease
        self.checkpointer = Checkpointer(self)

    def hide(self, label: str, value: str) -> None:
        """Remember a secret/PII value so later logs and files do not store it."""
        redactor = self._orch._redactors.get(self.run.run_id)
        if redactor is not None:
            redactor.register(value, label)

    def scrub(self, data: Any) -> Any:
        """Redact secrets from a payload before it is logged or written."""
        redactor = self._orch._redactors.get(self.run.run_id)
        return redactor.scrub(data) if redactor is not None else data

    def assert_in_control(self) -> None:
        """Automation must call this before touching the browser."""
        self._lease.assert_held_by(Driver.AUTOMATION, self.epoch)

    def step(self, name: str) -> None:
        """Announce the start of a step (also checks that we are still the driver)."""
        self.assert_in_control()
        self.current_step = name
        self.log("step_started", step=name)

    @property
    def run_dir(self) -> Path:
        """Directory for this run's events.jsonl and checkpoints."""
        return self._orch.runs_dir / self.run.run_id

    def log(self, event: str, **fields: Any) -> None:
        """Append one event to the run diary (and events.jsonl)."""
        self._orch._log(self.run, event, **fields)

    async def request_human(self, reason: str, **context: Any) -> str:
        """Pause the run and wait for a human. Returns how the human wants to continue."""
        return await self._orch._request_human(self, reason, context)

    async def await_human(
        self,
        reason: str,
        *,
        live_view_url: str,
        operator: str = "operator",
        wait_resume: Callable[[], Awaitable[str]] | None = None,
        human_actions: Callable[[], list[str]] | None = None,
    ) -> str:
        """Pause automation, hand the live session to a human, then continue from this page."""

        async def _operator() -> None:
            """HITL helper the engine can call to pause for a human."""
            for _ in range(200):
                if self.run.state == RunState.PAUSED and self.run.interventions:
                    break
                await asyncio.sleep(0.05)
            if not self.run.interventions or self.run.state != RunState.PAUSED:
                return
            iv = self.run.interventions[-1]
            self._orch.claim(iv.id, operator)
            print()
            print(f"HUMAN IN THE LOOP  intervention={iv.id}")
            print(f"reason: {reason}")
            print(f"live_view_url: {live_view_url}")
            print("Automation is paused. Finish the popup in the browser, then press Enter on that page — not in this terminal.")
            mode = "continue_after"
            if wait_resume is not None:
                mode = await wait_resume()
            if mode not in RESUME_MODES:
                mode = "continue_after"
            notes = [item for item in (human_actions() if human_actions else []) if item]
            if not notes:
                notes = ["returned the page to automation"]
            for note in notes:
                self._orch.record_human_action(iv.id, note)
            self._orch.resume(iv.id, mode)

        helper = asyncio.create_task(_operator())
        try:
            return await self.request_human(reason, live_view_url=live_view_url)
        finally:
            if not helper.done():
                helper.cancel()
            try:
                await helper
            except (asyncio.CancelledError, Exception):
                pass


# An engine is any async function that takes a RunContext and returns a RunResult
Engine = Callable[[RunContext], Awaitable[RunResult]]


class Orchestrator:
    def __init__(self, runs_dir: str = "runs") -> None:
        """Create the runs/ folder and in-memory run index."""
        self.runs_dir = Path(runs_dir)
        self.runs: dict[str, Run] = {}
        self._leases: dict[str, ControlLease] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._waiters: dict[str, asyncio.Future] = {}   # intervention id -> "sleeping run"
        self._iv_to_run: dict[str, str] = {}            # intervention id -> run id
        self._redactors: dict[str, Redactor] = {}       # run id -> its redactor

    # ------------------------------------------------------------------
    # Starting and waiting for runs
    # ------------------------------------------------------------------
    async def start_run(
        self,
        kind: str,
        name: str,
        engine: Engine,
        secrets: dict[str, str] | None = None,
        surface: str = SurfaceKind.WEB,
        tenant_id: str = "",
    ) -> Run:
        """secrets = values to hide in everything we write, e.g. {"param:member_id": "10045"}"""
        run = Run(
            kind=kind,
            name=name,
            surface=(surface or SurfaceKind.WEB).lower(),
            tenant_id=(tenant_id or "").strip(),
        )
        self.runs[run.run_id] = run
        self._leases[run.run_id] = ControlLease()

        redactor = Redactor()
        for label, value in (secrets or {}).items():
            redactor.register(value, label)
        self._redactors[run.run_id] = redactor

        (self.runs_dir / run.run_id).mkdir(parents=True, exist_ok=True)
        self._log(run, "run_created", kind=kind, name=name)
        self._tasks[run.run_id] = asyncio.create_task(self._execute(run, engine))
        return run

    async def wait(self, run: Run) -> Run:
        """Wait until the run finishes and return the updated Run."""
        await self._tasks[run.run_id]
        return run

    async def _execute(self, run: Run, engine: Engine) -> None:
        """Run the engine, handle HITL pauses, persist the result."""
        lease = self._leases[run.run_id]
        ctx = RunContext(self, run, lease)
        try:
            self._move(run, RunState.PREPARING)
            # LATER: start the browser, log in, load the capability here.
            ctx.epoch = lease.grant(Driver.AUTOMATION)
            self._move(run, RunState.RUNNING)

            result = await engine(ctx)  # <-- the real work happens here

            run.result = result
            self._move(run, FINAL_STATE[result.status])
        except RunAborted:
            run.result = RunResult.failed(ctx.current_step, "run to finish", "aborted by operator")
            self._move(run, RunState.ABORTED)
        except Exception as exc:  # any crash becomes a clear FAILED result
            run.result = RunResult.failed(ctx.current_step, "run to finish", f"{type(exc).__name__}: {exc}")
            if run.state not in TERMINAL_STATES:
                self._move(run, RunState.FAILED)
        finally:
            lease.release()
            if run.result is not None:
                self._log(run, "workflow_finished", extracted=run.result.as_extracted())
            self._log(run, "run_finished", state=run.state.value)
            self._save_run(run)

    # ------------------------------------------------------------------
    # Human handoff: called by the engine
    # ------------------------------------------------------------------
    async def _request_human(self, ctx: RunContext, reason: str, context: dict[str, Any]) -> str:
        """Open an intervention and wait until the operator resolves it."""
        run = ctx.run
        lease = self._leases[run.run_id]

        iv = Intervention(reason=reason, step=ctx.current_step, context=context)
        run.interventions.append(iv)
        self._iv_to_run[iv.id] = run.run_id
        waiter = asyncio.get_running_loop().create_future()
        self._waiters[iv.id] = waiter

        lease.release()                      # automation puts the wheel down
        self._move(run, RunState.PAUSED)
        self._log(run, "intervention_opened", intervention=iv.id, step=iv.step, reason=reason)

        mode = await waiter                  # the run sleeps here until resume() is called

        if mode == "abort":
            raise RunAborted(iv.id)
        ctx.epoch = lease.grant(Driver.AUTOMATION)  # automation gets the wheel back (new ticket)
        self._move(run, RunState.RUNNING)
        self._log(run, "control_returned", intervention=iv.id, mode=mode)
        return mode

    # ------------------------------------------------------------------
    # Human handoff: called by the human / operator side
    # ------------------------------------------------------------------
    def claim(self, intervention_id: str, operator: str) -> None:
        """A human takes control of the live session."""
        iv, run = self._lookup(intervention_id)
        if iv.status != InterventionStatus.OPEN:
            raise ValueError(f"intervention {iv.id} is not open (status={iv.status.value})")
        iv.status = InterventionStatus.CLAIMED
        iv.operator = operator
        self._leases[run.run_id].grant(Driver.HUMAN)
        self._move(run, RunState.HUMAN_CONTROL)
        self._log(run, "human_took_control", intervention=iv.id, operator=operator)

    def record_human_action(self, intervention_id: str, description: str) -> None:
        """Note what the human did. (Later this will be captured automatically.)"""
        iv, run = self._lookup(intervention_id)
        if run.state != RunState.HUMAN_CONTROL or self._leases[run.run_id].holder != Driver.HUMAN:
            raise ValueError("only the human who is in control can record actions")
        iv.human_actions.append(HumanAction(what=description))
        self._log(run, "human_action", intervention=iv.id, what=description)

    def resume(self, intervention_id: str, mode: str) -> None:
        """The human is done. mode = retry_step | continue_after | abort."""
        if mode not in RESUME_MODES:
            raise ValueError(f"mode must be one of {sorted(RESUME_MODES)}")
        iv, run = self._lookup(intervention_id)
        if iv.status == InterventionStatus.RESOLVED:
            raise ValueError(f"intervention {iv.id} is already resolved")

        if run.state == RunState.HUMAN_CONTROL:  # hand the wheel back first
            self._leases[run.run_id].release()
            self._move(run, RunState.PAUSED)
            self._log(run, "human_handed_back", intervention=iv.id)

        iv.status = InterventionStatus.RESOLVED
        iv.resume_mode = mode
        self._waiters[iv.id].set_result(mode)  # wakes the sleeping run

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    def _lookup(self, intervention_id: str) -> tuple[Intervention, Run]:
        """Get a run by id or raise."""
        run = self.runs[self._iv_to_run[intervention_id]]
        iv = next(i for i in run.interventions if i.id == intervention_id)
        return iv, run

    def _move(self, run: Run, new_state: RunState) -> None:
        """Transition run state and log state_changed."""
        check_transition(run.state, new_state)  # raises if the move is illegal
        old = run.state
        run.state = new_state
        self._log(run, "state_changed", old=old.value, new=new_state.value)

    def _log(self, run: Run, event: str, **fields: Any) -> None:
        """Write one event: scrub it, keep it in memory, append it to events.jsonl, print it."""
        clean = self._redactors[run.run_id].scrub(fields)   # only our fields, not ts/event
        record = {"ts": now(), "event": event, **clean, "surface": run.surface}
        if run.tenant_id:
            record["tenant"] = run.tenant_id
        run.events.append(record)

        path = self.runs_dir / run.run_id / "events.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        details = " ".join(f"{k}={v}" for k, v in clean.items())
        print(f"[{run.run_id}] {event:<20} {details}")

    def _save_run(self, run: Run) -> None:
        """Write runs/<run_id>/run.json (scrubbed). Output VALUES are never stored, only their names."""
        data = run.model_dump(mode="json")

        if data.get("result"):
            data["result"]["outputs"] = {name: "«not stored»" for name in data["result"]["outputs"]}

        redactor = self._redactors[run.run_id]
        for key in ("name", "result", "interventions"):  # scrub these parts, keep ids/timestamps intact
            data[key] = redactor.scrub(data[key])

        path = self.runs_dir / run.run_id / "run.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")