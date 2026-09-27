import asyncio
import json
from types import SimpleNamespace

from cuaflow.discover.hitl import (
    PageSession,
    approval_decision,
    collapse_human_items,
    describe_human_event,
    drain_human_log,
    interruption_reason,
    is_approval_overlay,
    live_view_url,
    poll_takeover_page,
    read_overlay,
    wait_for_page_enter,
)
from cuaflow.orchestrator.lease import ControlLease, Driver
from cuaflow.orchestrator.models import RunResult
from cuaflow.orchestrator.orchestrator import Orchestrator


def test_live_view_url_falls_back_to_headed_window() -> None:
    assert live_view_url(SimpleNamespace()) == "headed browser window (this machine)"
    agent = SimpleNamespace(browser_session=SimpleNamespace(cdp_url="http://127.0.0.1:9222"))
    assert live_view_url(agent) == "http://127.0.0.1:9222"


def test_lease_is_held_by_automation_then_released() -> None:
    lease = ControlLease()
    epoch = lease.grant(Driver.AUTOMATION)
    lease.assert_held_by(Driver.AUTOMATION, epoch)
    lease.release()
    assert lease.holder == Driver.NONE


def test_any_visible_overlay_is_an_interruption() -> None:
    reason = interruption_reason(overlay="MEMSYS — Human approval required")
    assert reason is not None
    assert "blocking UI" in reason
    assert interruption_reason(overlay="", page_text="membership inquiry") is None
    assert interruption_reason(overlay="", page_text="Please verify you are human") is not None


def test_approval_overlay_is_detected() -> None:
    assert is_approval_overlay("MEMSYS — Human approval required\nVerify you are human")
    assert not is_approval_overlay("Member inquiry")


def test_read_overlay_includes_iframe() -> None:
    class Frame:
        async def evaluate(self, script: str) -> str:
            return "MEMSYS — Human approval required"

    class Page:
        def __init__(self) -> None:
            self.frames = [Frame()]

        async def evaluate(self, script: str) -> str:
            return ""

    text = asyncio.run(read_overlay(PageSession(Page())))
    assert "Human approval required" in text


def test_wait_for_page_enter_resumes_without_stdin() -> None:
    class Page:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, script: str) -> str:
            self.calls += 1
            if "querySelectorAll" in script or ".overlay" in script:
                return ""
            if "removeItem" in script or "cuaflowHitlActive = false" in script:
                return "cleared"
            if "__cuaflowTakeover = ''" in script:
                return "cleared"
            return "resume" if self.calls > 4 else "armed"

    page = Page()

    class Session:
        async def get_current_page(self):
            return page

    agent = SimpleNamespace(browser_session=Session())
    holder: dict = {}
    assert asyncio.run(wait_for_page_enter(agent, holder)) == "continue_after"


def test_wait_for_page_enter_stays_paused_while_overlay_open() -> None:
    class Page:
        def __init__(self) -> None:
            self.overlay = "MEMSYS — Human approval required"
            self.polls = 0

        async def evaluate(self, script: str) -> str:
            if "querySelectorAll" in script or ".overlay" in script:
                return self.overlay
            if "cuaflowHitlActive = false" in script:
                return "cleared"
            if "__cuaflowTakeover = ''" in script:
                return "cleared"
            if "JSON.stringify" in script:
                return "[]"
            if "cuaflow-hitl-bar" in script or "Return to automation" in script:
                self.polls += 1
                if self.polls >= 3:
                    self.overlay = ""
                    return "resume"
                return "resume"
            if "sessionStorage.removeItem" in script and "cuaflowHitlActive" not in script:
                return "armed"
            return "armed"

    page = Page()

    class Session:
        async def get_current_page(self):
            return page

    agent = SimpleNamespace(browser_session=Session())
    holder: dict = {}
    assert asyncio.run(wait_for_page_enter(agent, holder)) == "continue_after"
    assert page.overlay == ""
    assert "human click Approve" not in (holder.get("human_acts") or [])


def test_approval_decision_from_clicks_and_url() -> None:
    assert approval_decision(["human click Cancel"], saw_approval=True) == "cancel"
    assert approval_decision(["human click Approve"], saw_approval=True) == "approve"
    assert (
        approval_decision([], overlay="MEMSYS — Human approval required", saw_approval=True) is None
    )
    assert (
        approval_decision(
            [],
            overlay="",
            urls="http://127.0.0.1:8000/main/subacct?mem=12345",
            saw_approval=True,
        )
        == "cancel"
    )
    assert (
        approval_decision(
            [],
            overlay="",
            urls="http://127.0.0.1:8000/main/subacct/confirm?mem=12345",
            saw_approval=True,
        )
        == "approve"
    )
    assert approval_decision(["human click Cancel"], saw_approval=False) == "cancel"


def test_wait_for_page_enter_cancel_hands_back() -> None:
    class Page:
        url = "http://127.0.0.1:8000/main/subacct?mem=12345"
        frames: list = []

        async def evaluate(self, script: str) -> str:
            if "JSON.stringify" in script:
                return json.dumps([{"kind": "click", "label": "Cancel", "actor": "human"}])
            if "querySelectorAll" in script or ".overlay" in script:
                return ""
            if "cuaflowHitlActive = false" in script:
                return "cleared"
            if "__cuaflowTakeover" in script:
                return "cleared"
            return "armed"

    holder: dict = {"approval_handoff": True}
    assert asyncio.run(wait_for_page_enter(PageSession(Page()), holder)) == "continue_after"
    assert holder["approval_decision"] == "cancel"
    assert any("click Cancel" in item for item in holder["page_acts"])


def test_describe_human_event_hides_password() -> None:
    assert describe_human_event({"kind": "click", "label": "Sign On"}) == "human click Sign On"
    assert describe_human_event(
        {"kind": "change", "label": "PWD", "value": "«redacted»"}
    ) == "human change PWD = «redacted»"
    assert describe_human_event({"kind": "click", "label": "Approve"}) == "human click Approve"
    assert describe_human_event({"kind": "click", "label": "Cancel"}) == "human click Cancel"
    assert describe_human_event({"kind": "click", "label": "Post New Share"}) == "human click Post New Share"
    assert describe_human_event({"kind": "key", "key": "a", "label": "MEM"}) == "human key a in MEM"
    assert describe_human_event({"kind": "type", "value": "12345", "label": "MEM"}) == "human type 12345 in MEM"
    assert describe_human_event({"kind": "key", "key": "«redacted»", "label": "PWD"}) == "human key «redacted» in PWD"
    assert (
        describe_human_event({"kind": "click", "label": "Sign On", "actor": "automation"})
        == "automation click Sign On"
    )
    assert describe_human_event({"kind": "click", "label": "Approve", "actor": "human"}) == "human click Approve"


def test_collapse_human_items_merges_printable_keys() -> None:
    items = collapse_human_items(
        [
            {"kind": "key", "key": "1", "label": "MEM"},
            {"kind": "key", "key": "2", "label": "MEM"},
            {"kind": "key", "key": "3", "label": "MEM"},
            {"kind": "key", "key": "Enter", "label": "MEM"},
            {"kind": "click", "label": "Approve"},
        ]
    )
    assert items == [
        {"kind": "type", "label": "MEM", "value": "123"},
        {"kind": "key", "key": "Enter", "label": "MEM"},
        {"kind": "click", "label": "Approve"},
    ]


def test_drain_human_log_records_click_and_change() -> None:
    class Page:
        frames: list = []

        async def evaluate(self, script: str) -> str:
            if "JSON.stringify" in script:
                return '[{"kind":"click","label":"Dismiss"},{"kind":"change","label":"PWD","value":"«redacted»"}]'
            return "armed"

    holder: dict = {}
    asyncio.run(drain_human_log(PageSession(Page()), holder))
    assert holder["human_acts"] == [
        "human click Dismiss",
        "human change PWD = «redacted»",
    ]


def test_drain_human_log_records_approve_cancel_post_and_keys() -> None:
    class Page:
        frames: list = []

        async def evaluate(self, script: str) -> str:
            if "JSON.stringify" in script:
                return json.dumps(
                    [
                        {"kind": "click", "label": "Cancel"},
                        {"kind": "click", "label": "Approve"},
                        {"kind": "click", "label": "Approve"},
                        {"kind": "click", "label": "Post New Share"},
                        {"kind": "key", "key": "1", "label": "MEM"},
                        {"kind": "key", "key": "2", "label": "MEM"},
                        {"kind": "key", "key": "3", "label": "MEM"},
                        {"kind": "key", "key": "Enter", "label": "MEM"},
                        {"kind": "key", "key": "«redacted»", "label": "PWD"},
                    ]
                )
            return "armed"

    holder: dict = {}
    asyncio.run(drain_human_log(PageSession(Page()), holder))
    assert holder["human_acts"] == [
        "human click Cancel",
        "human click Approve",
        "human click Post New Share",
        "human type 123 in MEM",
        "human key Enter in MEM",
        "human key «redacted» in PWD",
    ]


def test_drain_keeps_prior_actions_and_tags_actors() -> None:
    class Page:
        frames: list = []

        def __init__(self) -> None:
            self.n = 0

        async def evaluate(self, script: str) -> str:
            if "JSON.stringify" in script:
                self.n += 1
                if self.n == 1:
                    return json.dumps(
                        [{"kind": "click", "label": "Sign On", "actor": "automation"}]
                    )
                if self.n == 2:
                    return json.dumps(
                        [{"kind": "click", "label": "Approve", "actor": "human"}]
                    )
                return "[]"
            return "armed"

    holder: dict = {"ctx": SimpleNamespace(events=[])}

    def log(event: str, **fields: object) -> None:
        holder["ctx"].events.append({"event": event, **fields})

    holder["ctx"].log = log
    page = Page()
    agent = PageSession(page)
    asyncio.run(drain_human_log(agent, holder))
    asyncio.run(drain_human_log(agent, holder))
    assert holder["page_acts"] == [
        "automation click Sign On",
        "human click Approve",
    ]
    assert holder["human_acts"] is holder["page_acts"]
    assert holder["ctx"].events == [
        {"event": "page_action", "actor": "automation", "what": "click Sign On"},
        {"event": "page_action", "actor": "human", "what": "click Approve"},
    ]


def test_await_human_resumes_from_on_page_wait(tmp_path) -> None:
    async def scenario() -> None:
        orch = Orchestrator(runs_dir=str(tmp_path))

        async def engine(ctx):
            async def wait_resume() -> str:
                return "continue_after"

            mode = await ctx.await_human(
                "approval popup",
                live_view_url="headed browser window",
                wait_resume=wait_resume,
                human_actions=lambda: ["human click Dismiss"],
            )
            assert mode == "continue_after"
            return RunResult.success({"ok": "1"})

        run = await orch.start_run("discover", "hitl", engine)
        run = await orch.wait(run)
        assert run.result is not None
        assert run.result.status.value == "success"
        events_path = tmp_path / run.run_id / "events.jsonl"
        lines = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        finished = next(item for item in lines if item["event"] == "workflow_finished")
        assert finished["extracted"]["outcome"] == "success"
        assert finished["extracted"]["observed"]["ok"] == "1"
        human = next(item for item in lines if item["event"] == "human_action")
        assert human["what"] == "human click Dismiss"
        assert run.interventions[0].human_actions[0].what == "human click Dismiss"

    asyncio.run(scenario())


def test_poll_takeover_page_reads_h_from_iframe() -> None:
    class Frame:
        def __init__(self) -> None:
            self.flag = "takeover"

        async def evaluate(self, script: str) -> str:
            if "cuaflowTakeoverBound" in script or "e.key !== 'h'" in script:
                return self.flag
            if "__cuaflowTakeover = ''" in script:
                self.flag = "cleared"
                return "cleared"
            return ""

    class Page:
        def __init__(self) -> None:
            self.frames = [Frame()]

        async def evaluate(self, script: str) -> str:
            return "armed"

    assert asyncio.run(poll_takeover_page(PageSession(Page()))) is True


def test_poll_takeover_page_idle_is_false() -> None:
    class Page:
        frames: list = []

        async def evaluate(self, script: str) -> str:
            return "armed"

    assert asyncio.run(poll_takeover_page(PageSession(Page()))) is False
