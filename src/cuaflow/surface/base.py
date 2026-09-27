"""Shared step dispatch. The recorded UserFlow does not import Playwright or UIA."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cua.enums import StepType, SurfaceKind
from .locators import locators_for


@runtime_checkable
class Surface(Protocol):
    """One live session. Discover and replay share this, not Playwright types."""

    kind: str

    async def open(self, url: str) -> None:
        """Navigate or launch to a start place."""

    async def click(self, locators: list[str], text: str | None = None) -> None:
        """Activate a control. locators are surface-specific; text is the human label."""

    async def fill(self, locators: list[str], value: str) -> None:
        """Type or set a value."""

    async def press(self, key: str) -> None:
        """Key down."""

    async def snapshot(self) -> tuple[str, str | None]:
        """Visible text and a screen id if the app has one (e.g. MEM02)."""


async def run_step(surface: Surface, step, *, url: str = "", value: str = "") -> None:
    """Run one UserFlow step on whichever surface is plugged in.

    url/value should already have placeholders filled. Replay and tests share this.
    """
    locs = locators_for(getattr(surface, "kind", SurfaceKind.WEB), step)
    if step.type == StepType.NAVIGATE:
        await surface.open(url or step.url or "")
    elif step.type == StepType.CLICK:
        if (step.button or "").lower() in {"secondary", "right", "2"}:
            raise ValueError("context click is not an allowed action")
        await surface.click(locs, step.assertText)
    elif step.type == StepType.CHANGE:
        await surface.fill(locs, value if value != "" else (step.value or ""))
    elif step.type == StepType.KEY_DOWN:
        await surface.press(step.key or "")
    else:
        raise ValueError(f"unsupported step type: {step.type}")
