import asyncio
import json
from pathlib import Path

import pytest

from cuaflow.discover.userflow import UserFlow, UserFlowStep
from cuaflow.orchestrator.models import RunResult
from cuaflow.orchestrator.orchestrator import Orchestrator
from cuaflow.replay.desktop_flow import DesktopFlowRunner
from cuaflow.surface import (
    DesktopSurface,
    MemoryDesktop,
    WebSurface,
    locators_for,
    make_surface,
    run_step,
)
from cuaflow.surface.tk_memsys import TkMemsys


def _lookup_steps() -> list[UserFlowStep]:
    """One artifact: web CSS plus desktop automationId/name on the same steps."""
    return [
        UserFlowStep(type="navigate", url="http://127.0.0.1:8000/signon"),
        UserFlowStep(
            type="change",
            value="12345",
            selectors=[["input[name='MEMID']"], ["automationId/MEMID"]],
        ),
        UserFlowStep(
            type="click",
            assertText="Search",
            selectors=[["text/Search"], ["name/Search"]],
        ),
        UserFlowStep(type="keyDown", key="Enter"),
    ]


class _RecordingWeb:
    kind = "web"

    def __init__(self) -> None:
        self.log: list[tuple] = []

    async def open(self, url: str) -> None:
        self.log.append(("open", url))

    async def click(self, locators: list[str], text: str | None = None) -> None:
        self.log.append(("click", tuple(locators), text))

    async def fill(self, locators: list[str], value: str) -> None:
        self.log.append(("fill", tuple(locators), value))

    async def press(self, key: str) -> None:
        self.log.append(("press", key))

    async def snapshot(self) -> tuple[str, str | None]:
        return "MEM02", "MEM02"


async def _play(surface, steps: list[UserFlowStep]) -> None:
    for step in steps:
        await run_step(surface, step, url=step.url or "", value=step.value or "")


def test_locators_split_web_vs_desktop() -> None:
    change, click = _lookup_steps()[1], _lookup_steps()[2]
    web_fill = locators_for("web", change)
    desk_fill = locators_for("desktop", change)
    assert "input[name='MEMID']" in web_fill
    assert "automationId/MEMID" not in web_fill
    assert desk_fill == ["automationId/MEMID"]
    assert "text/Search" in locators_for("web", click)
    assert "name/Search" in locators_for("desktop", click)
    assert "input[name='MEMID']" not in locators_for("desktop", change)


def test_same_steps_run_on_web_and_desktop_surfaces() -> None:
    steps = _lookup_steps()
    web = _RecordingWeb()
    desk_backend = MemoryDesktop()
    desk_backend.put("automationId", "MEMID")
    desk_backend.put("name", "Search")
    desk_backend.body = "Name: HERNANDEZ, MARIA"
    desk_backend.screen = "MEM02"
    desktop = DesktopSurface(desk_backend)

    asyncio.run(_play(web, steps))
    asyncio.run(_play(desktop, steps))

    assert web.log[0] == ("open", "http://127.0.0.1:8000/signon")
    assert web.log[1][0] == "fill" and web.log[1][2] == "12345"
    assert web.log[2][0] == "click" and web.log[2][2] == "Search"
    assert web.log[3] == ("press", "Enter")

    assert desk_backend.log[0] == ("open", "http://127.0.0.1:8000/signon")
    assert ("fill", "automationId", "MEMID", "12345") in desk_backend.log
    assert any(item[0] == "click" and item[-1] == "Search" for item in desk_backend.log)
    assert ("press", "Enter") in desk_backend.log
    assert asyncio.run(desktop.snapshot()) == ("Name: HERNANDEZ, MARIA", "MEM02")


def test_make_surface_picks_web_or_desktop() -> None:
    desktop = make_surface("desktop", backend=MemoryDesktop())
    assert desktop.kind == "desktop"
    assert isinstance(desktop, DesktopSurface)
    with pytest.raises(ValueError, match="unknown surface"):
        make_surface("tablet")


def test_unwired_desktop_explains_missing_backend() -> None:
    surface = make_surface("desktop")
    with pytest.raises(RuntimeError, match="no OS backend"):
        asyncio.run(surface.open("memsys.exe"))


def test_capability_names_the_surface() -> None:
    flow = UserFlow(title="lookup", steps=_lookup_steps())
    assert flow.surface == "web"
    flow.surface = "desktop"
    assert flow.surface == "desktop"


def test_web_surface_kind() -> None:
    assert WebSurface.kind == "web"
    assert DesktopSurface.kind == "desktop"


def test_one_artifact_holds_web_and_desktop_locators() -> None:
    flow = UserFlow.model_validate_json(
        Path("workflows/lookup_savings_balance.json").read_text(encoding="utf-8")
    )
    assert flow.id == "lookup_savings_balance"
    mem = flow.steps[-2]
    assert any("input[name='MEMID']" in group[0] for group in mem.selectors or [])
    assert any(group[0] == "automationId/MEMID" for group in mem.selectors or [])
    nav = flow.steps[0]
    assert nav.url == "{base_url}/signon"
    assert nav.desktop_url == "{app}"
    assert any("input[name='MEMID']" in item for item in locators_for("web", mem))
    assert locators_for("desktop", mem) == ["automationId/MEMID"]


def test_events_and_run_json_record_desktop_surface(tmp_path) -> None:
    async def engine(ctx):
        ctx.log("surface_opened", driver="tk", title="MEMSYS Desktop")
        ctx.log("surface_step", type="click", label="click:Search")
        return RunResult.success({"ok": "1"})

    async def go():
        orch = Orchestrator(runs_dir=str(tmp_path))
        run = await orch.start_run("replay", "lookup_savings_balance", engine, surface="desktop")
        return await orch.wait(run)

    run = asyncio.run(go())
    assert run.surface == "desktop"
    lines = (tmp_path / run.run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        payload = json.loads(line)
        assert payload["surface"] == "desktop"
    saved = json.loads((tmp_path / run.run_id / "run.json").read_text(encoding="utf-8"))
    assert saved["surface"] == "desktop"
    assert any(json.loads(line)["event"] == "surface_opened" for line in lines)


def test_desktop_runner_drives_tk_memsys_not_chrome() -> None:
    flow = UserFlow.model_validate_json(
        Path("workflows/lookup_savings_balance.json").read_text(encoding="utf-8")
    )
    backend = TkMemsys(visible=False)
    assert backend.root.title() == "MEMSYS Desktop"
    runner = DesktopFlowRunner(headed=False, backend=backend, hold_s=0, step_pause=0)
    try:
        result = asyncio.run(runner.run(flow, {"password": "demo", "member_id": "12345"}))
        assert result.outcome == "success"
        assert result.screen_id == "MEM02"
        assert result.observed.get("savings_balance") == "1,204.55"
        assert backend.log[0][0] == "open"
        assert backend.log[0][1] == "desktop:memsys"
        assert backend.root.title() == "MEMSYS Desktop"
        assert any(item[0] == "click" and item[-1] == "Search" for item in backend.log)
    finally:
        backend.close()
