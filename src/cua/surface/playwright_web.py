"""Playwright + accessibility snapshot. Optional extra: `pip install cua[web]`."""

from __future__ import annotations

from cua.contracts.actions import ActionType, LocatorKind, SurfaceKind
from cua.contracts.artifact import LocatorStrategy
from cua.surface.base import ActionRequest, ActionResult, Observation


class PlaywrightWebDriver:
    kind = SurfaceKind.web

    def __init__(self, start_url: str | None = None, headed: bool = True):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise ImportError("Install the web extra: pip install -e .[web] && playwright install chromium") from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not headed)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        if start_url:
            self._page.goto(start_url)

    @property
    def page(self):
        return self._page

    def observe(self, *, screenshot: bool = False) -> Observation:
        page = self._page
        ax = page.locator("body").aria_snapshot() if hasattr(page.locator("body"), "aria_snapshot") else page.inner_text("body")[:4000]
        shot = page.screenshot() if screenshot else None
        return Observation(
            surface_kind=SurfaceKind.web,
            title=page.title(),
            url=page.url,
            ax_tree=ax if isinstance(ax, str) else str(ax),
            screenshot_png=shot,
        )

    def act(self, request: ActionRequest) -> ActionResult:
        loc = self._resolve(request.target)
        if request.action == ActionType.click:
            loc.click()
        elif request.action == ActionType.type:
            loc.fill(request.value or "")
        elif request.action == ActionType.select:
            loc.select_option(request.value or "")
        elif request.action == ActionType.extract:
            return ActionResult(ok=True, extracted=loc.inner_text(), observed="extracted")
        elif request.action == ActionType.dismiss:
            loc.click()
        elif request.action == ActionType.navigate:
            self._page.goto(request.value or "")
        elif request.action == ActionType.wait:
            self._page.wait_for_timeout(int(request.value or 500))
        else:
            return ActionResult(ok=False, observed=f"unsupported {request.action}")
        return ActionResult(ok=True, observed=request.action.value)

    def checkpoint(self, kind: str, value: str) -> bool:
        body = self._page.inner_text("body")
        if kind in ("text_present", "screen_id"):
            return value in body or value in self._page.title()
        if kind == "url_pattern":
            return value in (self._page.url or "")
        if kind == "dialog_absent":
            return "System Message" not in body
        return value in body

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._pw.stop()

    def _resolve(self, strategies: list[LocatorStrategy]):
        page = self._page
        last_err: Exception | None = None
        for s in strategies:
            try:
                if s.kind == LocatorKind.role_name:
                    return page.get_by_role("button", name=s.value) if not s.name else page.get_by_role(s.value, name=s.name)
                if s.kind == LocatorKind.name_attr:
                    return page.locator(f"[name='{s.value}']")
                if s.kind == LocatorKind.text:
                    return page.get_by_text(s.value)
                if s.kind == LocatorKind.label:
                    return page.get_by_label(s.value)
                loc = page.get_by_text(s.value)
                loc.wait_for(timeout=2000)
                return loc
            except Exception as e:
                last_err = e
                continue
        raise LookupError(f"no locator matched: {strategies!r}") from last_err
