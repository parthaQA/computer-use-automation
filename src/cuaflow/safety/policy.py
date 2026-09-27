"""Allowlist and irreversible-action rules. Keep this file boring on purpose.

A step is allowed only if:
  - its URL host is in allowed_hosts
  - its URL path starts with an allowed_paths prefix
  - its action type is in allowed_actions

Risky clicks (irreversible) are not auto-run. Headed runs ask a human.
Headless runs block.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from cua.enums import (
    ALLOWED_ACTIONS,
    ALLOWED_PATHS,
    ActionKind,
    DEFAULT_HOSTS,
    DEFAULT_POLICY_PATH,
    IRREVERSIBLE_LABELS,
    Placeholder,
    SurfaceKind,
    UrlPath,
)

_URL = re.compile(r"https?://[^\s]+", re.I)
_BASE_OPEN = re.compile(r"(?:\{base_url\}|\bbase_url\b)(/\S*)?", re.I)

# User wording → action kind. More specific phrases come first.
# A match is blocked only when that kind is not in allowed_actions.
_PROMPT_ACTIONS = [
    ("right click", re.compile(r"right[\s-]?click|context[\s-]?click|context[\s-]?menu", re.I), "contextclick"),
    ("double click", re.compile(r"double[\s-]?click", re.I), "doubleclick"),
    ("drag", re.compile(r"\bdrag\b|\bdrop\b", re.I), "drag"),
    ("hover", re.compile(r"\bhover\b", re.I), "hover"),
    ("evaluate", re.compile(r"\bevaluate\b|javascript", re.I), "evaluate"),
    ("scroll", re.compile(r"\bscroll\b", re.I), "scroll"),
    ("screenshot", re.compile(r"\bscreenshot\b", re.I), "screenshot"),
    ("wait", re.compile(r"\bwait\b", re.I), "wait"),
    ("extract", re.compile(r"\bextract\b", re.I), "extract"),
    ("done", re.compile(r"\bmark (the )?task done\b", re.I), "done"),
    ("navigate", re.compile(r"\bnavigate\b|\bopen url\b|\bopen https?://|\bopen\s+app\b|\bopen\s+(?:\{)?base_url", re.I), "navigate"),
    ("click", re.compile(r"\bclick\b", re.I), "click"),
    ("change", re.compile(r"\btype\b|\benter\b|\bfill\b|\bselect\b", re.I), "change"),
    ("keydown", re.compile(r"\bpress\b|\bkeydown\b|\bkey down\b", re.I), "keydown"),
]


def urls_in(text: str) -> list[str]:
    """Find http(s) URLs in free text."""
    return [match.group(0).rstrip(".,);") for match in _URL.finditer(text or "")]

DEFAULT = {
    "allowed_hosts": list(DEFAULT_HOSTS),
    "allowed_paths": [path.value for path in ALLOWED_PATHS],
    "allowed_actions": [kind.value for kind in ALLOWED_ACTIONS],
    "irreversible": [label.value for label in IRREVERSIBLE_LABELS],
    "discover_disallowed": ["headless"],
}

# Agent action names → the four recorder kinds (plus a few inspect actions).
ACTION_NAMES = {
    "navigate": "navigate",
    "go_to_url": "navigate",
    "open_tab": "navigate",
    "click": "click",
    "click_element": "click",
    "click_element_by_index": "click",
    "change": "change",
    "input": "change",
    "input_text": "change",
    "select_dropdown": "change",
    "select_dropdown_option": "change",
    "keydown": "keydown",
    "send_keys": "keydown",
    "press": "keydown",
    "wait": "wait",
    "scroll": "scroll",
    "extract": "extract",
    "extract_content": "extract",
    "extract_page_content": "extract",
    "screenshot": "screenshot",
    "done": "done",
    "ask_human": "ask_human",
    "contextclick": "contextclick",
    "context_click": "contextclick",
    "right_click": "contextclick",
    "rightclick": "contextclick",
    "context_menu": "contextclick",
    "doubleclick": "doubleclick",
    "double_click": "doubleclick",
    "drag": "drag",
    "hover": "hover",
    "evaluate": "evaluate",
}


def action_kind(name: str) -> str:
    """Map an agent or recorder action name to a policy kind."""
    return ACTION_NAMES.get((name or "").lower(), (name or "").lower())


class Policy:
    def __init__(self, data: dict | None = None) -> None:
        """Store allowlisted hosts, paths, actions, and irreversible labels."""
        raw = data or {}
        self.allowed_hosts = [item.lower() for item in raw.get("allowed_hosts") or []]
        self.allowed_paths = list(raw.get("allowed_paths") or [])
        self.allowed_actions = [item.lower() for item in raw.get("allowed_actions") or []]
        self.irreversible = [item.lower() for item in raw.get("irreversible") or []]
        self.discover_disallowed = [item.lower() for item in raw.get("discover_disallowed") or ["headless"]]

    def check_url(self, url: str) -> str | None:
        """Return a block reason if this URL is off the allowlist."""
        if not url:
            return "url is missing"
        placeholder = self._placeholder_path(url)
        if placeholder is not None:
            if not self._path_ok(placeholder):
                return f"path {placeholder} is not on the allowlist"
            return None
        parsed = urlparse(url)
        if parsed.scheme in {SurfaceKind.DESKTOP, "app"}:
            return None
        if not parsed.scheme and not parsed.netloc:
            return None
        host = (parsed.hostname or "").lower()
        if host not in self.allowed_hosts:
            return f"host {host or '(none)'} is not on the allowlist"
        path = parsed.path or "/"
        if not self._path_ok(path):
            return f"path {path} is not on the allowlist"
        return None

    def check_action(self, name: str) -> str | None:
        """Return a block reason if this action kind is not allowed."""
        kind = action_kind(name)
        if kind not in self.allowed_actions:
            return f"action {kind} is not on the allowlist"
        return None

    def check_discover(self, headed: bool) -> str | None:
        """Discover needs a visible window so a human can take the live session."""
        if not headed and "headless" in self.discover_disallowed:
            return "headless is not allowed during discover"
        return None

    def check_prompt(self, text: str) -> str | None:
        """Refuse a task that asks for a host, path, or action the allowlist does not include."""
        blob = text or ""
        for kind, _label in self.prompt_actions(blob):
            blocked = self.check_action(kind)
            if blocked:
                return blocked
        for url in urls_in(blob):
            blocked = self.check_url(url)
            if blocked:
                return blocked
        for match in _BASE_OPEN.finditer(blob):
            token = match.group(0).rstrip(".,);")
            blocked = self.check_url(token)
            if blocked:
                return blocked
        return None

    def prompt_actions(self, text: str) -> list[tuple[str, str]]:
        """Action kinds the user asked for, with a short label. Specific clicks beat a normal click."""
        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        for label, pattern, kind in _PROMPT_ACTIONS:
            if kind in seen:
                continue
            if pattern.search(text or ""):
                seen.add(kind)
                found.append((kind, label))
        if seen & {"contextclick", "doubleclick"}:
            found = [(kind, label) for kind, label in found if kind != "click"]
        return found

    def blocked_prompt_actions(self, text: str) -> list[tuple[str, str]]:
        """List prompt phrases that map to disallowed actions."""
        return [(kind, label) for kind, label in self.prompt_actions(text) if self.check_action(kind)]

    def check_step(self, step: object) -> str | None:
        """Return a block reason if a UserFlow step is off policy."""
        kind = action_kind(str(getattr(step, "type", "") or ""))
        button = str(getattr(step, "button", "") or "").lower()
        if kind == ActionKind.CLICK and button in {"secondary", "right", "2"}:
            kind = ActionKind.CONTEXT_CLICK
        blocked = self.check_action(kind)
        if blocked:
            return blocked
        if kind == ActionKind.NAVIGATE:
            url = str(getattr(step, "url", "") or "")
            desktop = str(getattr(step, "desktop_url", "") or "")
            if not url and desktop:
                return self.check_url(desktop)
            return self.check_url(url)
        return None

    def is_risky(self, text: str) -> bool:
        """Return True if this label is an irreversible click."""
        blob = (text or "").lower()
        return any(needle and needle in blob for needle in self.irreversible)

    def is_risky_step(self, step: object) -> bool:
        """Return True if this recorded step is irreversible."""
        parts = [
            str(getattr(step, "assertText", "") or ""),
            str(getattr(step, "value", "") or ""),
            str(getattr(step, "type", "") or ""),
        ]
        for group in getattr(step, "selectors", None) or []:
            parts.extend(str(item) for item in group)
        return self.is_risky(" ".join(parts))

    def _placeholder_path(self, url: str) -> str | None:
        """Path part of {base_url}/... for allowlist checks."""
        raw = (url or "").strip()
        lower = raw.lower()
        rest = None
        if lower in {"base_url", Placeholder.BASE_URL.lower(), Placeholder.BASE_URL}:
            rest = UrlPath.SIGNON
        elif Placeholder.BASE_URL in raw:
            rest = raw.split(Placeholder.BASE_URL, 1)[1] or UrlPath.SIGNON
        elif lower.startswith("base_url/"):
            rest = raw[len("base_url") :]
        else:
            return None
        path = rest.split("?")[0] or UrlPath.SIGNON
        if not path.startswith("/"):
            path = "/" + path
        return path

    def _path_ok(self, path: str) -> bool:
        """Return True if this URL path is under an allowed prefix."""
        for prefix in self.allowed_paths:
            if path == prefix:
                return True
            if prefix != "/" and path.startswith(prefix.rstrip("/") + "/"):
                return True
        return False


def load_policy(path: str | Path | None = None) -> Policy:
    """Load Policy from JSON, or the built-in allowlist."""
    file = Path(path or DEFAULT_POLICY_PATH)
    data = dict(DEFAULT)
    if file.is_file():
        loaded = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data.update(loaded)
    return Policy(data)
