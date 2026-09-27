"""Tenant is who runs the vendor app. The capability JSON stays vendor-scoped.

Pass --tenant cu-a. The id is looked up in tenants/tenants.json:
  url  — fills {base_url} on web
  app  — fills {app} on desktop

Overlay is a later delta (renamed field). This module does not re-record per tenant.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from ..discover.userflow import UserFlow, UserFlowStep
from ..orchestrator.models import Model

from cua.enums import AppId, DEFAULT_TENANTS_PATH

DEFAULT_APP = AppId.DESKTOP_MEMSYS


class Tenant(Model):
    """One customer instance of the vendor product."""

    id: str
    url: str | None = None  # origin only, e.g. https://memsys.cu-a.test
    app: str | None = None  # desktop target, e.g. desktop:memsys

    def origin(self) -> str | None:
        """Return this tenant's web origin, or None."""
        raw = (self.url or "").strip().rstrip("/")
        return raw or None

    def app_target(self) -> str:
        """Return the desktop app id (desktop:...)."""
        raw = (self.app or "").strip() or DEFAULT_APP
        if raw.lower().startswith(("desktop:", "app:")):
            return raw
        return f"desktop:{raw}"


def _entry(value: object) -> dict[str, str]:
    """Normalize a tenants.json value into {url, app}."""
    if isinstance(value, str):
        origin = value.strip().rstrip("/")
        return {"url": origin} if origin else {}
    if not isinstance(value, dict):
        return {}
    origin = str(value.get("url") or "").strip().rstrip("/")
    app = str(value.get("app") or value.get("desktop") or "").strip()
    out: dict[str, str] = {}
    if origin:
        out["url"] = origin
    if app:
        out["app"] = app
    return out


def load_tenants(path: str | Path | None = None) -> dict[str, dict[str, str]]:
    """id → {url, app}. File may use a string URL or an object with url and app."""
    file = Path(path or DEFAULT_TENANTS_PATH)
    if not file.is_file():
        return {}
    raw = json.loads(file.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("tenants"), dict):
        raw = raw["tenants"]
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        ident = str(key).strip()
        entry = _entry(value)
        if ident and entry:
            out[ident] = entry
    return out


def make_tenant(
    tenant_id: str | None = None,
    url: str | None = None,
    *,
    tenants: dict | None = None,
    path: str | Path | None = None,
) -> Tenant | None:
    """Resolve --tenant. URL and app come from the map unless --tenant-url is set."""
    ident = (tenant_id or "").strip()
    origin = (url or "").strip().rstrip("/") or None
    app: str | None = None
    if not ident and not origin:
        return None
    mapping = tenants if tenants is not None else load_tenants(path)
    if ident:
        if ident not in mapping and not origin:
            known = ", ".join(sorted(mapping)) or "(none)"
            raise KeyError(f"unknown tenant {ident!r}; known: {known}")
        entry = _entry(mapping.get(ident)) if ident in mapping else {}
        if not origin:
            origin = entry.get("url")
        app = entry.get("app")
    return Tenant(id=ident or "default", url=origin, app=app)


class TenantOverlay(Model):
    """Optional specialization on top of Tenant. Not required for url binding."""

    tenant: str
    base_id: str
    host: str | None = None
    selectors: dict[str, str] = {}


def apply_overlay(flow: UserFlow, overlay: TenantOverlay) -> UserFlow:
    """Copy the base flow and apply tenant host / locator overrides. Never edits the file."""
    copy = UserFlow.model_validate(flow.model_dump())
    steps: list[UserFlowStep] = []
    for step in copy.steps:
        if overlay.host and step.type == "navigate" and step.url:
            step.url = _swap_host(step.url, overlay.host)
        if overlay.selectors:
            step.selectors = _swap_selectors(step.selectors, overlay.selectors)
        steps.append(step)
    copy.steps = steps
    return copy


def _swap_host(url: str, host: str) -> str:
    """Replace the host in a recorded URL with the overlay host."""
    parsed = urlparse(url)
    if not parsed.hostname:
        return url
    replaced = parsed._replace(netloc=host if ":" in host else f"{host}:{parsed.port}" if parsed.port else host)
    return urlunparse(replaced)


def _swap_selectors(groups: list[list[str]] | None, mapping: dict[str, str]) -> list[list[str]] | None:
    """Prepend overlay locators when they match this step."""
    if not groups:
        return groups
    blob = " ".join(item for group in groups for item in group).upper()
    out = [list(group) for group in groups]
    for needle, replacement in mapping.items():
        if needle.upper() in blob:
            out.insert(0, [replacement])
            break
    return out
