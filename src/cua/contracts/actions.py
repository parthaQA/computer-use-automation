from __future__ import annotations

from enum import Enum


class SurfaceKind(str, Enum):
    web = "web"
    desktop = "desktop"


class ActionType(str, Enum):
    click = "click"
    type = "type"
    select = "select"
    extract = "extract"
    navigate = "navigate"
    dismiss = "dismiss"
    wait = "wait"


class LocatorKind(str, Enum):
    """Ranked from most portable to least.

    role_name / label / text / name_attr work on web AX trees and desktop UIA.
    visual (screenshot + coords) is a discovery fallback and must be flagged
    unstable — it is not the production locator.
    """

    role_name = "role_name"
    label = "label"
    text = "text"
    name_attr = "name_attr"
    ax_path = "ax_path"
    visual = "visual"


class OnError(str, Enum):
    business_outcome = "business_outcome"
    recover = "recover"
    fail = "fail"
    escalate = "escalate"


class RiskClass(str, Enum):
    read = "read"
    reversible_write = "reversible_write"
    irreversible = "irreversible"


class PiiClass(str, Enum):
    none = "none"
    identifier = "identifier"
    financial = "financial"
    secret = "secret"


class ControlOwner(str, Enum):
    automation = "AUTOMATION"
    human = "HUMAN"
