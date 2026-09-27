import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from cuaflow.discover.lookup import explain_result
from cuaflow.evidence import page_from_agent, save_failure
from cuaflow.orchestrator.models import RunResult


class _Frame:
    url = "http://127.0.0.1:8000/main/inquiry"

    async def content(self) -> str:
        return "<p>Member ID is required</p>"


class _Page:
    def __init__(self) -> None:
        self.frames = [_Frame()]

    async def screenshot(self, path: str, full_page: bool = True) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n")

    async def content(self) -> str:
        return "<html>outer</html>"


def test_save_failure_writes_screenshot_and_dom(tmp_path: Path) -> None:
    ctx = SimpleNamespace(run_dir=tmp_path, events=[])

    def log(event: str, **fields: object) -> None:
        ctx.events.append({"event": event, **fields})

    ctx.log = log
    paths = asyncio.run(save_failure(_Page(), ctx, step="click:Search"))
    shot = tmp_path / "failure" / "screenshot.png"
    html = tmp_path / "failure" / "page.html"
    assert paths["screenshot"] == str(shot)
    assert paths["dom"] == str(html)
    assert shot.exists() and shot.stat().st_size > 0
    body = html.read_text(encoding="utf-8")
    assert "outer" in body
    assert "Member ID is required" in body
    assert ctx.events[0]["event"] == "failure_evidence"
    assert ctx.events[0]["step"] == "click:Search"


def test_save_failure_is_a_no_op_without_page(tmp_path: Path) -> None:
    ctx = SimpleNamespace(run_dir=tmp_path, log=lambda *a, **k: None)
    assert asyncio.run(save_failure(None, ctx)) == {}


class _PageSecret:
    frames: list = []

    async def screenshot(self, path: str, full_page: bool = True) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n")

    async def content(self) -> str:
        return "<html>password=demo</html>"


def test_save_failure_scrubs_html(tmp_path: Path) -> None:
    ctx = SimpleNamespace(
        run_dir=tmp_path,
        log=lambda *a, **k: None,
        scrub=lambda text: text.replace("demo", "«redacted»"),
    )
    asyncio.run(save_failure(_PageSecret(), ctx, step="failed"))
    body = (tmp_path / "failure" / "page.html").read_text(encoding="utf-8")
    assert "demo" not in body
    assert "«redacted»" in body


def test_page_from_agent_unwraps_browser_session() -> None:
    inner = object()

    class Session:
        async def get_current_page(self):
            return SimpleNamespace(_page=inner)

    page = asyncio.run(page_from_agent(SimpleNamespace(browser_session=Session())))
    assert page is inner


def test_explain_result_lists_failure_files() -> None:
    failed = RunResult.failed("click:Search", "valid input", "Member ID is required")
    failed.outputs["screenshot"] = "runs/abc/failure/screenshot.png"
    failed.outputs["dom"] = "runs/abc/failure/page.html"
    text = explain_result(failed)
    assert "screenshot: runs/abc/failure/screenshot.png" in text
    assert "dom: runs/abc/failure/page.html" in text


def test_write_discover_pack_copies_run_logs(tmp_path: Path) -> None:
    from cuaflow.evidence import write_discover_pack

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event":"discover_started"}\n', encoding="utf-8")
    (run_dir / "run.json").write_text('{"state":"SUCCEEDED"}\n', encoding="utf-8")
    ctx = SimpleNamespace(run_dir=run_dir, run=SimpleNamespace(run_id="abc", surface="web"), scrub=lambda x: x)
    manifest = write_discover_pack(tmp_path, ctx, extra={"agent_steps": 4})
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["llm"] is True
    assert data.get("video") is None
    assert "recorder" not in data
    assert data["events"] == "events.jsonl"
    assert data["run_log"] == "run.json"
    assert data["agent_steps"] == 4
    assert (tmp_path / "events.jsonl").is_file()
    assert (tmp_path / "run.json").is_file()


def test_write_replay_pack_copies_workflow(tmp_path: Path) -> None:
    from cuaflow.evidence import copy_replay_source, use_case_slug, write_replay_pack

    source = tmp_path / "live_discover.json"
    source.write_text('{"id":"live_discover"}\n', encoding="utf-8")
    slug = use_case_slug("verify the member record")
    copied = copy_replay_source(tmp_path, source, slug=slug)
    assert copied == tmp_path / "verify-member-record.replay.json"
    assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text('{"event":"replay_started"}\n', encoding="utf-8")
    ctx = SimpleNamespace(run_dir=run_dir, run=SimpleNamespace(run_id="rep1", surface="web"), scrub=lambda x: x)
    manifest = write_replay_pack(tmp_path, ctx, extra={"workflow": copied.name})
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["kind"] == "replay"
    assert data["llm"] is False
    assert data.get("video") is None
    assert data["workflow"] == "verify-member-record.replay.json"


def test_use_case_slug_from_prompt() -> None:
    from cuaflow.evidence import use_case_slug

    assert use_case_slug("click Open Sub-Account then Continue") == "open-sub-account"
    assert use_case_slug("search member id 99999. verify the member record") == "verify-member-record"
    assert use_case_slug("lookup savings balance") == "lookup-savings-balance"


def test_scrub_evidence_tree_hides_operator_and_password(tmp_path: Path) -> None:
    from cuaflow.evidence import scrub_evidence_tree
    from cuaflow.safety.redact import Redactor

    conv = tmp_path / "conversation.json"
    conv.mkdir()
    leak = conv / "turn_1.txt"
    leak.write_text(
        "Need to enter operator id OPRID99, password s3cretpw, click Sign On.",
        encoding="utf-8",
    )
    flow = tmp_path / "verify-member-record.userflow.json"
    flow.write_text('{"title":"Enter operator id OPRID99","value":"OPRID99"}', encoding="utf-8")
    (tmp_path / "shot.bin").write_bytes(b"\x00binary")
    redactor = Redactor()
    redactor.register("OPRID99", "param:operator")
    redactor.register("s3cretpw", "param:password")
    changed = scrub_evidence_tree(tmp_path, redactor.scrub)
    assert changed >= 2
    body = leak.read_text(encoding="utf-8")
    assert "OPRID99" not in body
    assert "s3cretpw" not in body
    assert "«param:operator»" in body
    json_body = flow.read_text(encoding="utf-8")
    assert "OPRID99" not in json_body
    assert (tmp_path / "shot.bin").read_bytes() == b"\x00binary"


def test_write_replay_pack_scrubs_copied_workflow(tmp_path: Path) -> None:
    from cuaflow.evidence import copy_replay_source, write_replay_pack
    from cuaflow.safety.redact import Redactor

    source = tmp_path / "live_discover.json"
    source.write_text('{"value":"OPRID99","password":"s3cretpw"}', encoding="utf-8")
    copied = copy_replay_source(tmp_path, source, slug="verify-member-record")
    redactor = Redactor()
    redactor.register("OPRID99", "param:operator")
    redactor.register("s3cretpw", "param:password")
    ctx = SimpleNamespace(
        run_dir=tmp_path / "run",
        run=SimpleNamespace(run_id="rep1", surface="web"),
        scrub=redactor.scrub,
    )
    ctx.run_dir.mkdir()
    (ctx.run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    write_replay_pack(tmp_path, ctx, extra={"workflow": copied.name})
    body = copied.read_text(encoding="utf-8")
    assert "OPRID99" not in body
    assert "s3cretpw" not in body


def test_public_query_is_actions_only() -> None:
    from cuaflow.evidence import public_query

    text = public_query(
        "Open http://127.0.0.1:8000/signon. Enter operator id {operator}. Enter password {password}. "
        "Click Sign On. Enter member id 99999. Click Search. After Click Search, verify on that page: not found."
    )
    assert "http://" not in text
    assert "{operator}" not in text
    assert "{password}" not in text
    assert "enter operator id and password" in text.lower()
    assert "click on sign on button" in text.lower()
    assert "search member id 99999" in text.lower()
    assert "click on search button" in text.lower()
    already = (
        "open base_url, enter operator id and password, click on sign on button. "
        "search member id 99999 and click on search button. verify the member record"
    )
    cleaned = public_query(already)
    assert "open base_url, enter operator id and password, click on sign on button" in cleaned.lower()
    assert "open base_url, enter operator id and password" in text.lower()


def test_collect_webm_copies_playwright_file(tmp_path: Path) -> None:
    from cuaflow.evidence import collect_webm, write_replay_pack

    raw = tmp_path / "pw-video"
    raw.mkdir()
    (raw / "page@abc.webm").write_bytes(b"webm-bytes")
    dest = collect_webm(tmp_path, "replay.webm")
    assert dest == tmp_path / "replay.webm"
    assert dest.read_bytes() == b"webm-bytes"
    ctx = SimpleNamespace(
        run_dir=tmp_path / "run",
        run=SimpleNamespace(run_id="rep1", surface="web"),
        scrub=lambda x: x,
    )
    ctx.run_dir.mkdir()
    (ctx.run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    manifest = write_replay_pack(tmp_path, ctx)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["video"] == "replay.webm"
    assert data["recorder"] == "playwright record_video_dir"


def test_write_discover_pack_includes_screenshot_and_video(tmp_path: Path) -> None:
    from cuaflow.evidence import write_discover_pack

    (tmp_path / "screenshot.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "discover.webm").write_bytes(b"webm-bytes")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    ctx = SimpleNamespace(run_dir=run_dir, run=SimpleNamespace(run_id="abc", surface="web"), scrub=lambda x: x)
    data = json.loads(write_discover_pack(tmp_path, ctx).read_text(encoding="utf-8"))
    assert data["screenshot"] == "screenshot.png"
    assert data["video"] == "discover.webm"
    assert data["recorder"] == "playwright record_video_dir"


def test_collect_recording_copies_webm(tmp_path: Path) -> None:
    from cuaflow.evidence import collect_recording, collect_webm

    raw = tmp_path / "pw-video"
    raw.mkdir()
    (raw / "session.webm").write_bytes(b"webm-bytes")
    dest = collect_recording(tmp_path, "discover")
    assert dest == tmp_path / "discover.webm"
    assert dest.read_bytes() == b"webm-bytes"
    copied = collect_webm(tmp_path, "discover.webm")
    assert copied == dest


def test_capture_screenshot_uses_browser_session(tmp_path: Path) -> None:
    from cuaflow.evidence import capture_screenshot

    class Session:
        async def take_screenshot(self, path: str, full_page: bool = False) -> bytes:
            Path(path).write_bytes(b"\x89PNG\r\n")
            return b"\x89PNG\r\n"

    shot = asyncio.run(capture_screenshot(SimpleNamespace(browser_session=Session()), tmp_path))
    assert shot == tmp_path / "screenshot.png"
    assert shot.read_bytes().startswith(b"\x89PNG")


def test_capture_screenshot_prefers_playwright_page(tmp_path: Path) -> None:
    from cuaflow.evidence import capture_screenshot

    class Page:
        async def screenshot(self, path: str, full_page: bool = True) -> None:
            Path(path).write_bytes(b"\x89PNG-page")

    class Session:
        async def take_screenshot(self, path: str, full_page: bool = False) -> bytes:
            Path(path).write_bytes(b"session")
            return b"session"

    shot = asyncio.run(
        capture_screenshot(SimpleNamespace(browser_session=Session()), tmp_path, page=Page())
    )
    assert shot.read_bytes() == b"\x89PNG-page"


def test_parse_args_rediscover_defaults_off() -> None:
    from cuaflow.dispatch import parse_args

    args = parse_args(["open base_url"])
    assert args.rediscover is False
    assert parse_args(["open base_url", "--rediscover"]).rediscover is True
