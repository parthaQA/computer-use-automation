"""Failure shots and evidence packs from discover / replay runs."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from cua.enums import DEFAULT_EVIDENCE_DIR, RunKind, SurfaceKind
from typing import Any, Callable

_EVIDENCE_TEXT = {".json", ".jsonl", ".txt", ".html", ".htm", ".md", ".log"}
_HTTP_URL = re.compile(r"https?://[^\s\"']+", re.I)
_ENTER_OPERATOR_THEN_PASSWORD = re.compile(
    r"(?i)enter\s+operator\s+id"
    r"(?:\s+(?:«param:operator»|\{operator\}|x_operator))*"
    r"(?:\s+and)?(?:\s+enter)?"
    r"\s+password"
    r"(?:\s+(?:«param:password»|\{password\}|<secret>x_password</secret>|x_password))?"
)
_ENTER_OPERATOR_VALUE = re.compile(
    r"(?i)enter\s+operator\s+id(?:\s+(?:«param:operator»|\{operator\}|x_operator))+"
)
_ENTER_PASSWORD_VALUE = re.compile(
    r"(?i)enter\s+password(?:\s+(?:«param:password»|\{password\}|<secret>x_password</secret>|x_password))?"
)
_CLICK_SIGN_ON = re.compile(r"(?i)click(?:ing)?(?:\s+on)?(?:\s+the)?\s+sign\s+on(?:\s+button)?")
_ENTER_MEMBER_SEARCH = re.compile(
    r"(?i)enter\s+member\s+id\s+(\{member_id\}|\d+)\s*[.,]?\s*"
    r"click(?:ing)?(?:\s+on)?(?:\s+the)?\s+search(?:\s+button)?"
)
_CLICK_SEARCH = re.compile(r"(?i)click(?:ing)?(?:\s+on)?(?:\s+the)?\s+search(?:\s+button)?")
_AFTER_SEARCH_NOT_FOUND = re.compile(
    r"(?i)\s*after\s+click(?:\s+on)?\s+search(?:\s+button)?,?\s*verify on that page:\s*not found\.?"
)


def public_query(text: str) -> str:
    """User-facing query: actions only — no host, operator id, or password values."""
    out = " ".join((text or "").split())
    if not out:
        return out
    out = _HTTP_URL.sub("base_url", out)
    out = re.sub(r"(?i)\bopen\s+base_url(?:/signon)?", "open base_url", out)
    out = _ENTER_OPERATOR_THEN_PASSWORD.sub("enter operator id and password", out)
    out = _ENTER_OPERATOR_VALUE.sub("enter operator id", out)
    out = _ENTER_PASSWORD_VALUE.sub("enter password", out)
    out = re.sub(r"(?i)enter operator id\s*[.,]?\s*enter password", "enter operator id and password", out)
    out = re.sub(r"(?i)(?:«param:operator»|\{operator\}|x_operator)", "", out)
    out = re.sub(r"(?i)(?:«param:password»|\{password\}|<secret>x_password</secret>|x_password)", "", out)
    out = re.sub(r"(?i)enter operator id\s*[.,]?\s*enter password", "enter operator id and password", out)
    out = _CLICK_SIGN_ON.sub("click on sign on button", out)
    out = _ENTER_MEMBER_SEARCH.sub(r"search member id \1 and click on search button", out)
    out = _CLICK_SEARCH.sub("click on search button", out)
    out = _AFTER_SEARCH_NOT_FOUND.sub(". verify the member record", out)
    out = re.sub(r"(?i)open base_url\s*[.,]?\s*enter operator", "open base_url, enter operator", out)
    out = re.sub(r"(?i)and password\s*[.,]?\s*click on sign on", "and password, click on sign on", out)
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s+([.,])", r"\1", out)
    out = re.sub(r"\.{2,}", ".", out)
    return out.strip()


async def save_failure(page: Any, ctx: Any, *, step: str | None = None) -> dict[str, str]:
    """Write screenshot.png and page.html under runs/<id>/failure/. Never raises."""
    if ctx is None or page is None:
        return {}
    folder = Path(ctx.run_dir) / "failure"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        return {}

    paths: dict[str, str] = {}
    shot = folder / "screenshot.png"
    try:
        await page.screenshot(path=str(shot), full_page=True)
        if shot.exists() and shot.stat().st_size > 0:
            paths["screenshot"] = str(shot)
    except Exception:
        pass

    html_path = folder / "page.html"
    try:
        parts = [await page.content()]
        for frame in getattr(page, "frames", None) or []:
            try:
                parts.append(f"\n<!-- frame {getattr(frame, 'url', '')} -->\n")
                parts.append(await frame.content())
            except Exception:
                continue
        html = "".join(parts)
        if hasattr(ctx, "scrub"):
            html = ctx.scrub(html)
        html_path.write_text(html, encoding="utf-8")
        if html_path.exists():
            paths["dom"] = str(html_path)
    except Exception:
        pass

    if paths and hasattr(ctx, "log"):
        ctx.log("failure_evidence", step=step, **paths)
    return paths


async def page_from_agent(agent: Any) -> Any:
    """Best-effort Playwright page from a browser-use Agent. None if we cannot find one."""
    if agent is None:
        return None
    for name in ("browser_session", "browser"):
        session = getattr(agent, name, None)
        if session is None:
            continue
        getter = getattr(session, "get_current_page", None)
        if getter is None:
            continue
        try:
            page = getter()
            if hasattr(page, "__await__"):
                page = await page
        except Exception:
            continue
        inner = getattr(page, "playwright", None) or getattr(page, "_page", None)
        return inner or page
    return None


async def capture_screenshot(agent: Any, folder: Path, page: Any = None) -> Path | None:
    """Write evidence/screenshot.png from the Playwright page while it is still open."""
    dest = Path(folder) / "screenshot.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    targets: list[Any] = [page]
    session = getattr(agent, "browser_session", None) if agent is not None else None
    if session is None and agent is not None:
        session = getattr(agent, "browser", None)
    if agent is not None:
        try:
            targets.append(await page_from_agent(agent))
        except Exception:
            pass
    for target in targets:
        if target is None or not hasattr(target, "screenshot"):
            continue
        try:
            await target.screenshot(path=str(dest), full_page=True)
            if dest.is_file() and dest.stat().st_size > 0:
                return dest
        except Exception:
            continue
    if session is not None and hasattr(session, "take_screenshot"):
        try:
            await session.take_screenshot(path=str(dest), full_page=True)
            if dest.is_file() and dest.stat().st_size > 0:
                return dest
        except Exception:
            pass
    return dest if dest.is_file() and dest.stat().st_size > 0 else None


def use_case_slug(*parts: str, flow_id: str = "") -> str:
    """Stable folder name for a MEMSYS use case, e.g. open-sub-account, verify-member-record."""
    blob = " ".join(item for item in (flow_id, *parts) if item)
    lowered = blob.lower()
    if re.search(r"sub[\s_-]?account", lowered):
        return "open-sub-account"
    if re.search(
        r"member record|whether member|capture whether member|verify the member",
        lowered,
    ):
        return "verify-member-record"
    if re.search(r"savings|lookup_savings", lowered):
        return "lookup-savings-balance"
    if re.search(r"permission|servicing rights", lowered):
        return "permission-denied"
    if re.search(r"\blocked\b", lowered):
        return "member-locked"
    if "search" in lowered and "sub" not in lowered:
        return "verify-member-record"
    token = (flow_id or "").strip()
    if token and token not in {"live_discover"}:
        raw = token
    else:
        raw = blob or "run"
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return (slug[:48] or "run")


def evidence_folder(
    settings: Any,
    ctx: Any,
    *,
    kind: str,
    label: str = "",
    flow_id: str = "",
) -> Path:
    """evidence/<use-case>.<discover|replay>/ — readable use-case packs."""
    root = Path(getattr(settings, "cua_evidence_dir", None) or DEFAULT_EVIDENCE_DIR)
    slug = use_case_slug(label, flow_id=flow_id)
    kind = RunKind.REPLAY if kind == RunKind.REPLAY else RunKind.DISCOVER
    folder = root / f"{slug}.{kind}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def discover_dir(settings: Any, ctx: Any, *, label: str = "", kind: str = RunKind.DISCOVER, flow_id: str = "") -> Path:
    """Return evidence/<use-case>.<kind>/ and create it if needed."""
    return evidence_folder(settings, ctx, kind=kind, label=label, flow_id=flow_id)


def credential_scrubber(settings: Any | None = None) -> Callable[[Any], Any]:
    """Hide operator id and password from .env in any evidence payload."""
    from .safety.redact import Redactor
    from .settings import load_settings

    settings = settings or load_settings()
    redactor = Redactor()
    redactor.register(getattr(settings, "memsys_operator", ""), "param:operator")
    redactor.register(getattr(settings, "memsys_password", ""), "param:password")
    return redactor.scrub


def scrub_evidence_tree(folder: Path, scrub: Callable[[Any], Any] | None = None) -> int:
    """Rewrite text files under evidence/ so operator id and password never remain on disk."""
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    if scrub is None:
        scrub = credential_scrubber()
    changed = 0
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _EVIDENCE_TEXT:
            continue
        try:
            raw = path.read_bytes()
        except Exception:
            continue
        if b"\x00" in raw[:1024]:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                continue
        cleaned = scrub(text)
        if not isinstance(cleaned, str) or cleaned == text:
            continue
        path.write_text(cleaned, encoding="utf-8")
        changed += 1
    return changed


def copy_replay_source(folder: Path, workflow_path: Path, slug: str | None = None) -> Path:
    """Keep the JSON that replay executed, named <use-case>.replay.json."""
    stem = slug or use_case_slug(Path(workflow_path).stem)
    dest = Path(folder) / f"{stem}.replay.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(workflow_path, dest)
    return dest


def sync_run_logs(folder: Path, run_dir: Path | None) -> None:
    """Copy events, run.json, checkpoints, and failure shots into evidence/."""
    if run_dir is None:
        return
    run_dir = Path(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("events.jsonl", "checkpoints.json", "run.json"):
        src = run_dir / name
        dest = folder / name
        if src.is_file() and src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
    fail = run_dir / "failure"
    dest_fail = folder / "failure"
    if fail.is_dir() and fail.resolve() != dest_fail.resolve():
        if dest_fail.exists():
            shutil.rmtree(dest_fail)
        shutil.copytree(fail, dest_fail)


def write_history(folder: Path, history: Any, ctx: Any = None) -> Path | None:
    """Agent step log (no screenshots). Never raises."""
    if history is None:
        return None
    try:
        blob: dict[str, Any] = {
            "steps": history.number_of_steps() if hasattr(history, "number_of_steps") else None,
            "success": history.is_successful() if hasattr(history, "is_successful") else None,
            "final": (history.final_result() or "")[:4000] if hasattr(history, "final_result") else "",
        }
        if hasattr(history, "urls"):
            urls = history.urls()
            blob["urls"] = [str(item) for item in (urls or [])]
        if hasattr(history, "action_names"):
            blob["actions"] = [str(item) for item in (history.action_names() or [])]
        if ctx is not None and hasattr(ctx, "scrub"):
            blob = ctx.scrub(blob)
        path = folder / "history.json"
        path.write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path
    except Exception:
        return None


def _pack_screenshot(folder: Path) -> str | None:
    """Return screenshot.png if that file exists in the pack."""
    if (folder / "screenshot.png").is_file():
        return "screenshot.png"
    return None


def _pack_video(folder: Path) -> str | None:
    """Return the stable evidence video filename if present."""
    for name in ("replay.webm", "discover.webm", "discover.mp4"):
        path = folder / name
        if path.is_file() and path.stat().st_size > 0:
            return name
    return None


def collect_recording(folder: Path, stem: str) -> Path | None:
    """Copy the newest pw-video recording to evidence/<stem>.webm or .mp4."""
    folder = Path(folder)
    for name in (f"{stem}.webm", f"{stem}.mp4"):
        dest = folder / name
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
    sources: list[Path] = []
    video_dir = folder / "pw-video"
    if video_dir.is_dir():
        sources.extend(path for path in video_dir.glob("*.webm") if path.is_file())
        sources.extend(path for path in video_dir.glob("*.mp4") if path.is_file())
    sources.extend(path for path in folder.glob("*.webm") if path.stem != stem)
    sources.extend(path for path in folder.glob("*.mp4") if path.stem != stem)
    sources = [path for path in sources if path.is_file() and path.stat().st_size > 0]
    if not sources:
        return None
    src = max(sources, key=lambda path: path.stat().st_mtime)
    dest = folder / f"{stem}{src.suffix.lower()}"
    try:
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        return dest if dest.is_file() and dest.stat().st_size > 0 else None
    except Exception:
        return dest if dest.is_file() and dest.stat().st_size > 0 else None


def collect_webm(folder: Path, name: str = "replay.webm") -> Path | None:
    """Copy Playwright's pw-video/*.webm into a stable evidence filename."""
    folder = Path(folder)
    dest = folder / name
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    found = collect_recording(folder, Path(name).stem)
    if found is None:
        return None
    if found.name == name:
        return found
    if found.suffix.lower() == Path(name).suffix.lower():
        try:
            shutil.copy2(found, dest)
            return dest
        except Exception:
            return found
    return found


def refresh_manifest(folder: Path) -> Path:
    """Update file list after late copies (run.json is written after the engine returns)."""
    folder = Path(folder)
    path = folder / "manifest.json"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    files = sorted(item.name for item in folder.iterdir() if item.is_file())
    data["events"] = "events.jsonl" if (folder / "events.jsonl").is_file() else None
    data["run_log"] = "run.json" if (folder / "run.json").is_file() else None
    data["history"] = "history.json" if (folder / "history.json").is_file() else None
    data["screenshot"] = _pack_screenshot(folder)
    video = _pack_video(folder)
    data["video"] = video
    if video:
        data["recorder"] = "playwright record_video_dir"
    else:
        data.pop("recorder", None)
    data["files"] = files
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_discover_pack(folder: Path, ctx: Any, extra: dict[str, Any] | None = None) -> Path:
    """Copy run logs and write a small manifest (no secrets)."""
    folder.mkdir(parents=True, exist_ok=True)
    run_dir = Path(ctx.run_dir) if ctx is not None else None
    sync_run_logs(folder, run_dir)
    files = sorted(item.name for item in folder.iterdir() if item.is_file())
    video = _pack_video(folder)
    payload: dict[str, Any] = {
        "run_id": getattr(getattr(ctx, "run", None), "run_id", ""),
        "kind": RunKind.DISCOVER,
        "surface": getattr(getattr(ctx, "run", None), "surface", SurfaceKind.WEB),
        "llm": True,
        "live_view": "headed Chrome on this machine",
        "events": "events.jsonl" if (folder / "events.jsonl").is_file() else None,
        "run_log": "run.json" if (folder / "run.json").is_file() else None,
        "history": "history.json" if (folder / "history.json").is_file() else None,
        "screenshot": _pack_screenshot(folder),
        "video": video,
        "files": files,
        **(extra or {}),
    }
    if video:
        payload.setdefault("recorder", "playwright record_video_dir")
    if ctx is not None and hasattr(ctx, "scrub"):
        payload = ctx.scrub(payload)
    path = folder / "manifest.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    scrub_evidence_tree(folder, ctx.scrub if ctx is not None and hasattr(ctx, "scrub") else None)
    return path


def write_replay_pack(folder: Path, ctx: Any, extra: dict[str, Any] | None = None) -> Path:
    """Copy run logs and write a replay manifest (no LLM, no secrets)."""
    folder.mkdir(parents=True, exist_ok=True)
    run_dir = Path(ctx.run_dir) if ctx is not None else None
    sync_run_logs(folder, run_dir)
    files = sorted(item.name for item in folder.iterdir() if item.is_file())
    video = _pack_video(folder)
    payload: dict[str, Any] = {
        "run_id": getattr(getattr(ctx, "run", None), "run_id", ""),
        "kind": RunKind.REPLAY,
        "surface": getattr(getattr(ctx, "run", None), "surface", SurfaceKind.WEB),
        "llm": False,
        "live_view": "headed Chrome on this machine",
        "events": "events.jsonl" if (folder / "events.jsonl").is_file() else None,
        "run_log": "run.json" if (folder / "run.json").is_file() else None,
        "screenshot": _pack_screenshot(folder),
        "video": video,
        "files": files,
        **(extra or {}),
    }
    if video:
        payload.setdefault("recorder", "playwright record_video_dir")
    if ctx is not None and hasattr(ctx, "scrub"):
        payload = ctx.scrub(payload)
    path = folder / "manifest.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    scrub_evidence_tree(folder, ctx.scrub if ctx is not None and hasattr(ctx, "scrub") else None)
    return path
