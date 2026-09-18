"""Surface port — the seam between perception/action and the recorded flow.

Web today (Playwright + accessibility tree). Desktop later implements the
same ActionType vocabulary against UI Automation / AX APIs.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from cua.contracts.actions import ActionType, SurfaceKind
from cua.contracts.artifact import LocatorStrategy


class Observation(BaseModel):
    surface_kind: SurfaceKind
    title: str = ""
    url: str | None = None
    screen_hint: str | None = None
    ax_tree: str
    screenshot_png: bytes | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    action: ActionType
    target: list[LocatorStrategy] = Field(default_factory=list)
    value: str | None = None


class ActionResult(BaseModel):
    ok: bool
    observed: str = ""
    extracted: str | None = None


@runtime_checkable
class SurfaceDriver(Protocol):
    kind: SurfaceKind

    def observe(self, *, screenshot: bool = False) -> Observation: ...

    def act(self, request: ActionRequest) -> ActionResult: ...

    def checkpoint(self, kind: str, value: str) -> bool: ...

    def close(self) -> None: ...
