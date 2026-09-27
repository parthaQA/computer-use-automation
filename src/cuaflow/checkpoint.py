"""Per-step checkpoints: confirm we reached a page state, keep a running trail.

Used by discover and replay. A click succeeding is not enough; we record what
the page actually showed after the step.
"""

from __future__ import annotations

import json
from typing import Any

from cua.enums import ConditionKind
from pydantic import Field

from .orchestrator.models import Model


class Checkpoint(Model):
    n: int
    step: str
    screen_id: str | None = None
    kind: str = ConditionKind.CLEAR
    ok: bool = True
    detail: str = ""
    observed: dict[str, str] = Field(default_factory=dict)


class Checkpointer:
    """Running page state for one run: current screen, facts, and the step trail."""

    def __init__(self, ctx: Any | None = None) -> None:
        """Start an empty checkpoint trail for this run."""
        self.ctx = ctx
        self.items: list[Checkpoint] = []
        self.screen_id: str | None = None
        self.observed: dict[str, str] = {}

    def record(
        self,
        *,
        step: str,
        screen_id: str | None = None,
        kind: str = ConditionKind.CLEAR,
        detail: str = "",
        observed: dict[str, str] | None = None,
        ok: bool | None = None,
    ) -> Checkpoint:
        """Append one step checkpoint (screen, kind, and observed facts)."""
        facts = {
            key: value
            for key, value in (observed or {}).items()
            if key not in {"screenshot", "dom", "checkpoint_trail"} and value
        }
        self.observed.update(facts)
        if screen_id:
            self.screen_id = screen_id
        if self.ctx is not None and hasattr(self.ctx, "hide"):
            for key in ("member_name", "name"):
                if facts.get(key):
                    self.ctx.hide(f"param:{key}", facts[key])
        if ok is None:
            ok = kind == ConditionKind.CLEAR
        item = Checkpoint(
            n=len(self.items) + 1,
            step=step,
            screen_id=screen_id or self.screen_id,
            kind=kind,
            ok=ok,
            detail=detail or "",
            observed=facts,
        )
        self.items.append(item)
        print(f"Checkpoint {item.n}: {item.step}  screen={item.screen_id or '-'}  kind={item.kind}")
        if self.ctx is not None:
            self.ctx.log(
                "checkpoint",
                n=item.n,
                step=item.step,
                screen_id=item.screen_id,
                kind=item.kind,
                ok=item.ok,
                detail=(item.detail or "")[:160],
            )
            self.save()
        return item

    def record_page(
        self,
        *,
        step: str,
        body: str,
        screen: str | None,
        wanted: list[str] | None = None,
    ) -> Checkpoint:
        """Classify the live page after a step and record that checkpoint."""
        from .replay.conditions import classify_runtime
        from .replay.playwright_flow import extract_outputs

        cond = classify_runtime(body, screen)
        facts = extract_outputs(body, screen, wanted)
        return self.record(
            step=step,
            screen_id=screen,
            kind=cond.kind,
            detail=cond.message,
            observed=facts,
            ok=cond.kind == "clear",
        )

    def snapshot(self) -> dict[str, Any]:
        """Return the current screen, facts, and trail for logs."""
        return {
            "screen_id": self.screen_id,
            "steps": len(self.items),
            "observed": dict(self.observed),
            "trail": [f"{item.n}:{item.step}:{item.screen_id or '-'}:{item.kind}" for item in self.items],
        }

    def save(self) -> None:
        """Write checkpoints.json under the run directory."""
        if self.ctx is None:
            return
        try:
            path = self.ctx.run_dir / "checkpoints.json"
            payload = {
                "screen_id": self.screen_id,
                "observed": self.observed,
                "items": [item.model_dump(mode="json") for item in self.items],
            }
            if hasattr(self.ctx, "scrub"):
                payload = self.ctx.scrub(payload)
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def attach(self, outputs: dict[str, Any]) -> dict[str, Any]:
        """Copy the trail onto a result so What happened / events.jsonl can show it."""
        snap = self.snapshot()
        self.save()
        if self.ctx is not None:
            self.ctx.log(
                "checkpoint_state",
                screen_id=snap["screen_id"],
                steps=snap["steps"],
                trail=snap["trail"],
            )
        outputs["checkpoint_trail"] = snap["trail"]
        if snap["screen_id"] and not outputs.get("screen_id"):
            outputs["screen_id"] = snap["screen_id"]
        return outputs


def agent_step_label(agent: Any, n: int) -> str:
    """Build a short label like click:3 from the agent's last action."""
    try:
        history = getattr(getattr(agent, "history", None), "history", None) or []
        item = history[-1]
        actions = getattr(getattr(item, "model_output", None), "action", None) or []
        if not actions:
            return f"agent:{n}"
        data = actions[-1].model_dump(exclude_none=True) if hasattr(actions[-1], "model_dump") else dict(actions[-1])
        name = next((key for key in data if key != "type"), None) or data.get("type") or "act"
        return f"{name}:{n}"
    except Exception:
        return f"agent:{n}"


async def record_from_browser(ck: Checkpointer, agent: Any) -> None:
    """Discover: snapshot the live page after an agent step."""
    from .evidence import page_from_agent
    from .surface.web import WebSurface

    page = await page_from_agent(agent)
    if page is None:
        return
    try:
        body, screen = await WebSurface(page).snapshot()
    except Exception:
        return
    ck.record_page(step=agent_step_label(agent, len(ck.items) + 1), body=body, screen=screen)
