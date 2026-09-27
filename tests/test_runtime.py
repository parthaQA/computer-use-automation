import asyncio
from pathlib import Path
from types import SimpleNamespace

from cuaflow.runtime import PlaywrightChrome, free_local_port


def test_free_local_port_is_bindable() -> None:
    port = free_local_port()
    assert 0 < port < 65536
    assert free_local_port() != 0


def test_playwright_chrome_screenshot_uses_live_page(tmp_path: Path) -> None:
    class Page:
        def __init__(self) -> None:
            self.video = SimpleNamespace(path="clip.webm")

        async def screenshot(self, path: str, full_page: bool = True) -> None:
            Path(path).write_bytes(b"\x89PNG\r\n")

    page = Page()
    chrome = PlaywrightChrome(
        playwright=None,
        context=SimpleNamespace(pages=[page]),
        page=page,
        cdp_url="http://127.0.0.1:9222",
        video=page.video,
    )
    dest = tmp_path / "screenshot.png"
    shot = asyncio.run(chrome.screenshot(dest))
    assert shot == dest
    assert dest.read_bytes().startswith(b"\x89PNG")
    extra = SimpleNamespace(video=SimpleNamespace(path="tab.webm"))
    chrome.track_page(extra)
    assert chrome.page is extra
    assert chrome.video is extra.video
