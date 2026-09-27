from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from browser_use import Browser
from browser_use.llm.base import BaseChatModel

from .settings import Settings


def silence_windows_aio_pipes() -> None:
    """Playwright/browser-use leaves a closed pipe; Python 3.12 then errors in __del__."""
    if sys.platform != "win32":
        return
    from asyncio.proactor_events import _ProactorBasePipeTransport

    original = _ProactorBasePipeTransport.__del__
    if getattr(original, "_cua_quiet", False):
        return

    def __del__(self, *args, **kwargs):  # noqa: N807
        """Ignore closed-pipe errors when Playwright tears down on Windows."""
        try:
            original(self, *args, **kwargs)
        except ValueError:
            pass
        except RuntimeError as exc:
            if "Event loop is closed" not in str(exc):
                raise

    __del__._cua_quiet = True  # type: ignore[attr-defined]
    _ProactorBasePipeTransport.__del__ = __del__  # type: ignore[method-assign]


def make_llm(settings: Settings) -> BaseChatModel:
    """Build the discover LLM from .env (Anthropic or Browser Use)."""
    from browser_use import ChatAnthropic, ChatBrowserUse

    prefer = (os.environ.get("CUA_LLM") or "").strip().lower()
    if prefer in {"browser-use", "browser_use", "bu"} and settings.browser_use_api_key:
        return ChatBrowserUse()
    if settings.anthropic_api_key:
        return ChatAnthropic(model=settings.anthropic_model, api_key=settings.anthropic_api_key)
    if settings.browser_use_api_key:
        return ChatBrowserUse()
    raise ValueError("set ANTHROPIC_API_KEY or BROWSER_USE_API_KEY in .env")


def make_browser(
    settings: Settings,
    *,
    headed: bool = True,
    start_url: str | None = None,
    allowed_hosts: list[str] | None = None,
    cdp_url: str | None = None,
    keep_alive: bool | None = None,
) -> Browser:
    """Build the headed Browser Use Chrome session for discover."""
    silence_windows_aio_pipes()
    hosts = list(allowed_hosts or [])
    fallback = urlparse(start_url or settings.memsys_base_url).hostname or "127.0.0.1"
    if fallback not in hosts:
        hosts.append(fallback)
    allowed_domains: list[str] = []
    for host in hosts:
        allowed_domains.extend([host, f"http://{host}", f"https://{host}"])
    extra: dict = {}
    if cdp_url:
        extra["cdp_url"] = cdp_url
        extra["keep_alive"] = True
    if keep_alive is not None:
        extra["keep_alive"] = keep_alive
    return Browser(
        headless=not headed,
        viewport={"width": settings.cua_viewport_width, "height": settings.cua_viewport_height},
        allowed_domains=allowed_domains,
        **extra,
    )


def free_local_port() -> int:
    """Bind an ephemeral localhost TCP port and return its number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class PlaywrightChrome:
    """Headed Chromium launched by Playwright so we can record webm and take screenshots."""

    playwright: Any
    context: Any
    page: Any
    cdp_url: str
    video: Any = None
    profile_dir: Path | None = None

    def track_page(self, page: Any) -> None:
        """Follow new tabs so screenshot/video stay on the live page."""
        self.page = page
        video = getattr(page, "video", None)
        if video is not None:
            self.video = video

    async def screenshot(self, dest: Path) -> Path | None:
        """Write screenshot.png from the live Playwright page."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        pages: list[Any] = []
        if self.page is not None:
            pages.append(self.page)
        try:
            pages.extend(item for item in (self.context.pages or []) if item not in pages)
        except Exception:
            pass
        for page in pages:
            try:
                await page.screenshot(path=str(dest), full_page=True)
                if dest.is_file() and dest.stat().st_size > 0:
                    return dest
            except Exception:
                continue
        return dest if dest.is_file() and dest.stat().st_size > 0 else None

    async def save_video(self, dest: Path) -> Path | None:
        """Close the context (required to flush webm) and copy the recording to dest."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        videos: list[Any] = []
        if self.video is not None:
            videos.append(self.video)
        try:
            for page in list(self.context.pages or []):
                video = getattr(page, "video", None)
                if video is not None and video not in videos:
                    videos.append(video)
        except Exception:
            pass
        try:
            await self.context.close()
        except Exception:
            pass
        await asyncio.sleep(0.2)
        for video in videos:
            try:
                await video.save_as(str(dest))
                if dest.is_file() and dest.stat().st_size > 0:
                    break
            except Exception:
                continue
        try:
            await self.playwright.stop()
        except Exception:
            pass
        if self.profile_dir is not None:
            shutil.rmtree(self.profile_dir, ignore_errors=True)
            self.profile_dir = None
        return dest if dest.is_file() and dest.stat().st_size > 0 else None


async def launch_recorded_chrome(
    *,
    headed: bool,
    viewport: dict[str, int],
    video_dir: Path,
    profile_dir: Path | None = None,
) -> PlaywrightChrome:
    """Launch headed Chromium with Playwright record_video_dir and a CDP endpoint."""
    from playwright.async_api import async_playwright

    silence_windows_aio_pipes()
    video_dir = Path(video_dir)
    video_dir.mkdir(parents=True, exist_ok=True)
    profile = Path(profile_dir) if profile_dir else Path(tempfile.mkdtemp(prefix="cua-pw-"))
    profile.mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    last_exc: Exception | None = None
    context = None
    port = 0
    for _ in range(8):
        port = free_local_port()
        try:
            context = await pw.chromium.launch_persistent_context(
                str(profile),
                headless=not headed,
                viewport=viewport,
                record_video_dir=str(video_dir),
                record_video_size=dict(viewport),
                args=[
                    f"--remote-debugging-port={port}",
                    "--remote-debugging-address=127.0.0.1",
                    "--remote-allow-origins=*",
                ],
            )
            break
        except Exception as exc:
            last_exc = exc
            context = None
    if context is None:
        try:
            await pw.stop()
        except Exception:
            pass
        shutil.rmtree(profile, ignore_errors=True)
        raise last_exc or RuntimeError("could not launch headed Chrome")
    page = context.pages[0] if context.pages else await context.new_page()
    chrome = PlaywrightChrome(
        playwright=pw,
        context=context,
        page=page,
        cdp_url=f"http://127.0.0.1:{port}",
        video=getattr(page, "video", None),
        profile_dir=profile,
    )
    context.on("page", chrome.track_page)
    await _wait_for_cdp(chrome.cdp_url)
    return chrome


async def _wait_for_cdp(cdp_url: str, timeout_s: float = 8.0) -> None:
    """Wait until Chromium answers /json/version so Browser Use can attach."""
    import urllib.request

    url = cdp_url.rstrip("/") + "/json/version"
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        try:
            await asyncio.to_thread(lambda: urllib.request.urlopen(url, timeout=1).read())
            return
        except Exception:
            await asyncio.sleep(0.15)
