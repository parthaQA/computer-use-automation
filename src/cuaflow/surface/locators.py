"""Pick locators from a UserFlow step for the surface that is running.

A step can list both web and desktop locators. Replay keeps the ones this
surface understands and ignores the rest.

  web:      input[name='MEMID'], xpath//..., text/Search, aria/Search, name/MEMID
  desktop:  name/MEMID, automationId/MEMID, text/Search, class/Button
  both:     text/..., aria/..., name/...
"""

from __future__ import annotations

import re
from cua.enums import RecordedUrl, StepType, SurfaceKind
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..discover.userflow import UserFlowStep

_NAME_ATTR = re.compile(r"\[name=['\"]([^'\"]+)['\"]\]", re.I)


def locators_for(kind: str, step: UserFlowStep) -> list[str]:
    """Locators for this surface (web vs desktop) from a step."""
    raw = [item for group in (step.selectors or []) for item in group if item]
    surface = (kind or SurfaceKind.WEB).lower()
    if surface == SurfaceKind.DESKTOP:
        picked = [item for item in raw if is_desktop_locator(item)]
        if not picked and step.assertText:
            return [f"name/{step.assertText}"]
        return picked
    picked = [to_web(item) for item in raw if is_web_locator(item)]
    if step.assertText:
        extra = f"input[type='submit'][value='{step.assertText}']"
        if extra not in picked:
            picked.append(extra)
    return picked


def is_web_locator(locator: str) -> bool:
    """True if this string is a CSS/xpath/text web locator."""
    lower = locator.lower()
    return not lower.startswith(("automationid/", "class/", "desktop:"))


def is_desktop_locator(locator: str) -> bool:
    """True if this string is a desktop name/automationId locator."""
    lower = locator.lower()
    if lower.startswith("xpath"):
        return False
    if locator.startswith(("input", "select", "button", "textarea", "a[", ".", "#", "[")):
        return False
    return True


def to_web(locator: str) -> str:
    """Keep only web locators from mixed selector groups."""
    if locator.startswith("name/"):
        ident = locator[5:]
        return f'[name="{ident}"]'
    return locator


def parse_desktop(locator: str) -> tuple[str, str]:
    """Return (how, name) e.g. ('automationId', 'MEMID') or ('name', 'Sign On')."""
    lower = locator.lower()
    mapping = (
        ("automationid/", "automationId"),
        ("name/", "name"),
        ("class/", "class"),
        ("text/", "text"),
        ("aria/", "name"),
        ("desktop:", "name"),
    )
    for prefix, how in mapping:
        if lower.startswith(prefix):
            return how, locator[len(prefix) :]
    return "name", locator


def enrich_step(step: UserFlowStep) -> None:
    """Add the other surface's locators so one recording can replay on web or desktop."""
    if getattr(step, "type", None) == StepType.NAVIGATE:
        if step.url and not step.desktop_url:
            step.desktop_url = RecordedUrl.APP
        return
    blob = " ".join(item for group in (step.selectors or []) for item in group)
    for match in _NAME_ATTR.finditer(blob):
        _append_locator(step, f"automationId/{match.group(1)}")
    text = getattr(step, "assertText", None)
    if text:
        _append_locator(step, f"name/{text}")


def _append_locator(step: UserFlowStep, locator: str) -> None:
    """Append a locator group if it is not already present."""
    groups = [list(group) for group in (step.selectors or [])]
    if any(locator in group for group in groups):
        return
    groups.append([locator])
    step.selectors = groups
