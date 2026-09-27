"""Desktop surface. Same open/click/fill/press/snapshot as web; UIA-style locators.

The artifact still says click Search / fill {member_id}. This class looks those up
by name or automationId instead of CSS. Plug in a real OS backend later
(pywinauto / UIA). Tests use MemoryDesktop.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cua.enums import SurfaceKind
from .locators import locators_for, parse_desktop

_UNWIRED = (
    "desktop surface has no OS backend yet; pass MemoryDesktop in tests "
    "or a UIA backend when driving a real exe"
)


@runtime_checkable
class DesktopBackend(Protocol):
    async def open(self, target: str) -> None:
        """Open the named desktop application or window."""

    async def click(self, how: str, name: str) -> None:
        """Click a desktop control located by how/name."""

    async def fill(self, how: str, name: str, value: str) -> None:
        """Type into a desktop field located by how/name."""

    async def press(self, key: str) -> None:
        """Press a key in the desktop application."""

    async def snapshot(self) -> tuple[str, str | None]:
        """Return visible text and a screen id if the app has one."""


class UnwiredDesktop:
    """Default backend: fail clearly until someone plugs in a real session."""

    async def open(self, target: str) -> None:
        """Raise until a real OS backend is plugged in."""
        raise RuntimeError(_UNWIRED)

    async def click(self, how: str, name: str) -> None:
        """Raise until a real OS backend is plugged in."""
        raise RuntimeError(_UNWIRED)

    async def fill(self, how: str, name: str, value: str) -> None:
        """Raise until a real OS backend is plugged in."""
        raise RuntimeError(_UNWIRED)

    async def press(self, key: str) -> None:
        """Raise until a real OS backend is plugged in."""
        raise RuntimeError(_UNWIRED)

    async def snapshot(self) -> tuple[str, str | None]:
        """Raise until a real OS backend is plugged in."""
        raise RuntimeError(_UNWIRED)


class MemoryDesktop:
    """In-process stand-in for a desktop window. Records acts; tests seed controls."""

    def __init__(self) -> None:
        """Seed an empty control map and action log for tests."""
        self.log: list[tuple] = []
        self.controls: dict[tuple[str, str], str] = {}
        self.body = ""
        self.screen: str | None = None

    def put(self, how: str, name: str, value: str = "") -> None:
        """Store a named value in the desktop backend."""
        self.controls[(how, name)] = value

    async def open(self, target: str) -> None:
        """Record an open against the in-memory desktop window."""
        self.log.append(("open", target))

    async def click(self, how: str, name: str) -> None:
        """Click a control in the in-memory desktop."""
        self._resolve(how, name)
        self.log.append(("click", how, name))

    async def fill(self, how: str, name: str, value: str) -> None:
        """Fill a control in the in-memory desktop."""
        key = self._resolve(how, name)
        self.controls[key] = value
        self.log.append(("fill", how, name, value))

    async def press(self, key: str) -> None:
        """Record a key press on the in-memory desktop."""
        self.log.append(("press", key))

    async def snapshot(self) -> tuple[str, str | None]:
        """Return the seeded body text and screen id."""
        return self.body, self.screen

    def _resolve(self, how: str, name: str) -> tuple[str, str]:
        """Match a control by how/name, then by name alone."""
        if (how, name) in self.controls:
            return how, name
        for key in self.controls:
            if key[1] == name:
                return key
        raise LookupError(f"no desktop control matched: {how}/{name}")


class DesktopSurface:
    kind = SurfaceKind.DESKTOP

    def __init__(self, backend: DesktopBackend | None = None) -> None:
        """Wrap a DesktopBackend and keep locators_for(desktop) on each step."""
        self.backend = backend if backend is not None else UnwiredDesktop()

    async def open(self, url: str) -> None:
        """Open the desktop target through the backend."""
        await self.backend.open(url)

    async def click(self, locators: list[str], text: str | None = None) -> None:
        """Click the first matching desktop locator."""
        await self._try(locators, text, lambda how, name: self.backend.click(how, name))

    async def fill(self, locators: list[str], value: str) -> None:
        """Fill the first matching desktop locator."""
        await self._try(locators, None, lambda how, name: self.backend.fill(how, name, value))

    async def press(self, key: str) -> None:
        """Press a key through the desktop backend."""
        await self.backend.press(key)

    async def snapshot(self) -> tuple[str, str | None]:
        """Snapshot the desktop backend."""
        return await self.backend.snapshot()

    def locators(self, step) -> list[str]:
        """Return UIA-style locators recorded on this UserFlow step."""
        return locators_for(self.kind, step)

    async def _try(self, locators: list[str], text: str | None, act) -> None:
        """Run a desktop action and convert lookup errors into failures."""
        last: Exception | None = None
        candidates = list(locators)
        if text:
            candidates.append(f"name/{text}")
            candidates.append(f"text/{text}")
        if not candidates:
            raise LookupError("no desktop locators on this step")
        for item in candidates:
            how, name = parse_desktop(item)
            try:
                await act(how, name)
                return
            except LookupError as exc:
                last = exc
                continue
        if last:
            raise last
        raise LookupError(f"no desktop control matched: {candidates}")
