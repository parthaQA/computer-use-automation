"""Playwright-backed surface. Same UserFlow steps as desktop; CSS/xpath locators here."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from cua.enums import SurfaceKind
from .locators import locators_for


Poll = Callable[[], Awaitable[None]]


def chrome_selector_to_playwright(selector: str) -> str:
    """Convert a Chrome-recorder selector to a Playwright selector."""
    if selector.startswith("xpath//"):
        return "xpath=//" + selector[7:].lstrip("/")
    if selector.startswith("text/"):
        return f'text="{selector[5:]}"'
    if selector.startswith("aria/"):
        return f'[aria-label="{selector[5:]}"]'
    return selector


def select_option_attempts(value: str) -> list[dict[str, str]]:
    """Match a <select> by option value or visible label (e.g. '02 Secondary Shares')."""
    text = (value or "").strip()
    attempts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key, candidate in (("value", text), ("label", text)):
        if candidate and (key, candidate) not in seen:
            attempts.append({key: candidate})
            seen.add((key, candidate))
    token = text.split()[0] if text else ""
    if token and token != text:
        for key, candidate in (("value", token), ("label", token)):
            if (key, candidate) not in seen:
                attempts.append({key: candidate})
                seen.add((key, candidate))
    return attempts


def _iter_frames(page: Page):
    """Yield the page and every iframe so locators can search inside frames."""
    seen: list = []
    for item in (page, *list(getattr(page, "frames", None) or [])):
        if item not in seen:
            seen.append(item)
            yield item


async def _wait_frames(page: Page) -> None:
    """Wait for the page and frames to finish a navigation."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=3000)
    except PlaywrightTimeout:
        pass
    for frame in list(getattr(page, "frames", None) or []):
        try:
            await frame.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            continue


async def _goto(page: Page, url: str) -> None:
    """Navigate with a few retries on timeout."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await _wait_frames(page)
            return
        except PlaywrightTimeout as exc:
            last = exc
            await asyncio.sleep(1.2 * (attempt + 1))
    if last:
        raise last
    raise PlaywrightTimeout("navigation failed")


async def page_text(page: Page) -> tuple[str, str | None]:
    """Visible body text plus MEMSYS screen id, including iframes."""
    chunks = [await page.inner_text("body")]
    for frame in page.frames:
        try:
            chunks.append(await frame.inner_text("body"))
        except Exception:
            continue
    body = "\n".join(chunks).replace("\xa0", " ")
    screen = None
    for target in [page, *page.frames]:
        try:
            if await target.locator(".screenid").count():
                screen = (await target.locator(".screenid").first.inner_text()).strip()
                break
        except Exception:
            continue
    return body, screen


async def _select_option(locator: Any, value: str) -> None:
    """Choose a <select> option by value or visible label."""
    last_error: Exception | None = None
    for kwargs in select_option_attempts(value):
        try:
            await locator.select_option(**kwargs)
            return
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise LookupError(f"no select option matched: {value!r}")


class WebSurface:
    kind = SurfaceKind.WEB

    def __init__(self, page: Page, *, poll: Poll | None = None) -> None:
        """Bind this surface to a Playwright page."""
        self.page = page
        self.poll = poll

    async def open(self, url: str) -> None:
        """Open this URL in Chrome."""
        await _goto(self.page, url)

    async def click(self, locators: list[str], text: str | None = None) -> None:
        """Click the first matching locator; wait for navigation if it happens."""
        locator = await self.find(locators, text)
        try:
            async with self.page.expect_navigation(wait_until="load", timeout=2500):
                await locator.click()
        except PlaywrightTimeout:
            pass
        await _wait_frames(self.page)

    async def fill(self, locators: list[str], value: str) -> None:
        """Type into an input or choose a select option."""
        locator = await self.find(locators)
        tag = ""
        try:
            tag = (await locator.evaluate("el => (el.tagName || '').toLowerCase()")) or ""
        except Exception:
            tag = ""
        if tag == "select":
            await _select_option(locator, value)
        else:
            await locator.fill(value)

    async def press(self, key: str) -> None:
        """Press a keyboard key on the page."""
        await self.page.keyboard.press(key)

    async def snapshot(self) -> tuple[str, str | None]:
        """Return (page text, screen id) for checkpoints."""
        return await page_text(self.page)

    async def find(self, locators: list[str], text: str | None = None):
        """Wait until one of the locators is visible; raise if none match."""
        selectors = [chrome_selector_to_playwright(item) for item in locators]
        if text:
            extra = f"input[type='submit'][value='{text}']"
            if extra not in selectors:
                selectors.append(extra)
        for _ in range(4):
            if self.poll is not None:
                await self.poll()
            for selector in selectors:
                for frame in _iter_frames(self.page):
                    loc = frame.locator(selector).first
                    try:
                        await loc.wait_for(state="visible", timeout=1200)
                        return loc
                    except PlaywrightTimeout:
                        continue
            await asyncio.sleep(0.4)
            await _wait_frames(self.page)
        raise LookupError(f"no selector matched: {selectors}")

    def locators(self, step) -> list[str]:
        """Web locators stored on this UserFlow step."""
        return locators_for(self.kind, step)
