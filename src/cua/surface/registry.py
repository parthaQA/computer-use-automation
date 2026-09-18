from __future__ import annotations

from cua.contracts.actions import SurfaceKind
from cua.surface.base import SurfaceDriver


def get_driver(kind: SurfaceKind, **kwargs) -> SurfaceDriver:
    if kind == SurfaceKind.web:
        from cua.surface.playwright_web import PlaywrightWebDriver

        return PlaywrightWebDriver(**kwargs)
    raise NotImplementedError(
        f"No driver for {kind.value}. Desktop UIA/AX is the next adapter on this port."
    )
