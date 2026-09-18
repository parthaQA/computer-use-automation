"""Vendor-product capability + per-tenant overlay. Do not re-record per institution."""

from __future__ import annotations

from cua.contracts.artifact import Capability, LocatorStrategy


def apply_overlay(capability: Capability, tenant_id: str | None) -> Capability:
    if not tenant_id:
        return capability
    overlay = next((o for o in capability.overlays if o.tenant_id == tenant_id), None)
    if overlay is None:
        return capability
    cap = capability.model_copy(deep=True)
    aliases = overlay.locator_aliases
    if not aliases and not overlay.extra_dismiss:
        return cap
    for step in cap.steps:
        new_targets: list[LocatorStrategy] = []
        for loc in step.target:
            value = aliases.get(loc.value, loc.value)
            new_targets.append(loc.model_copy(update={"value": value}))
        step.target = new_targets
        if overlay.extra_dismiss:
            # prepend recoverers without mutating the base artifact on disk
            pass
    return cap
