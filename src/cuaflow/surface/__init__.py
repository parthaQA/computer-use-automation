"""How we perceive and act on a UI. The recorded flow does not import this.

A UserFlow step is intent (click Sign On, fill {member_id}). A Surface turns that
intent into the live app: Playwright for web, a UIA backend for desktop.
Replay stays: load UserFlow → for each step call the surface → checkpoint.
"""

from __future__ import annotations

from cua.enums import SurfaceKind
from .base import Surface, run_step
from .desktop import DesktopSurface, MemoryDesktop, UnwiredDesktop
from .locators import locators_for
from .web import WebSurface


def make_surface(kind: str, **kwargs):
    """Plug-in: kind comes from UserFlow.surface ('web' or 'desktop')."""
    name = (kind or SurfaceKind.WEB).lower()
    if name == SurfaceKind.WEB:
        return WebSurface(**kwargs)
    if name == SurfaceKind.DESKTOP:
        return DesktopSurface(**kwargs)
    raise ValueError(f"unknown surface {kind!r}; use {SurfaceKind.WEB} or {SurfaceKind.DESKTOP}")


__all__ = [
    "DesktopSurface",
    "MemoryDesktop",
    "Surface",
    "UnwiredDesktop",
    "WebSurface",
    "locators_for",
    "make_surface",
    "run_step",
]
