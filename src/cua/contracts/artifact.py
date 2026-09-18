"""Typed, versioned, agent-invocable capability.

Bound to a vendor product, not a tenant. Tenant differences live in overlays.
Locators are ranked strategies — never session-scoped Playwright refs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from cua.contracts.actions import (
    ActionType,
    LocatorKind,
    OnError,
    PiiClass,
    RiskClass,
    SurfaceKind,
)


class LocatorStrategy(BaseModel):
    kind: LocatorKind
    value: str
    name: str | None = None
    unstable: bool = False


class Checkpoint(BaseModel):
    kind: Literal["screen_id", "text_present", "url_pattern", "role_present", "dialog_absent"]
    value: str


class ParamSpec(BaseModel):
    name: str
    type: Literal["string", "number", "boolean"] = "string"
    required: bool = True
    pii_class: PiiClass = PiiClass.none
    description: str = ""


class OutputSpec(BaseModel):
    name: str
    type: Literal["string", "number", "boolean"] = "string"
    extractor: LocatorStrategy
    description: str = ""


class ActionStep(BaseModel):
    id: str
    intent: str
    action: ActionType
    target: list[LocatorStrategy] = Field(default_factory=list)
    input_binding: str | None = None
    preconditions: list[Checkpoint] = Field(default_factory=list)
    postconditions: list[Checkpoint] = Field(default_factory=list)
    on_error: OnError = OnError.fail
    outcome_code: str | None = None

    @field_validator("target")
    @classmethod
    def visual_must_be_last(cls, strategies: list[LocatorStrategy]) -> list[LocatorStrategy]:
        vis = [i for i, s in enumerate(strategies) if s.kind == LocatorKind.visual]
        if vis and vis != [len(strategies) - 1]:
            raise ValueError("visual locators must be the last fallback")
        return strategies


class TenantOverlay(BaseModel):
    """Per-institution specialization of a vendor-product capability."""

    tenant_id: str
    locator_aliases: dict[str, str] = Field(default_factory=dict)
    extra_dismiss: list[LocatorStrategy] = Field(default_factory=list)
    version_range: str | None = None


class Lineage(BaseModel):
    discovered_from_run_id: str | None = None
    model: str | None = None
    created_at: str | None = None


class Capability(BaseModel):
    schema_version: Literal["capability.v1"] = "capability.v1"
    id: str
    version: str = "1"
    name: str
    description: str
    vendor_app: str
    surface_kind: SurfaceKind = SurfaceKind.web
    risk_class: RiskClass = RiskClass.read
    params: list[ParamSpec] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)
    steps: list[ActionStep]
    success_checkpoint: Checkpoint
    policy_ref: str = "default"
    overlays: list[TenantOverlay] = Field(default_factory=list)
    lineage: Lineage = Field(default_factory=Lineage)

    def param_names(self) -> set[str]:
        return {p.name for p in self.params}

    def bind(self, params: dict[str, Any]) -> dict[str, Any]:
        missing = [p.name for p in self.params if p.required and p.name not in params]
        if missing:
            raise ValueError(f"missing required params: {missing}")
        extra = set(params) - self.param_names()
        if extra:
            raise ValueError(f"unknown params: {sorted(extra)}")
        return dict(params)
