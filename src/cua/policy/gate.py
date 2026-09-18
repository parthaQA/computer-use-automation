from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from cua.contracts.actions import ActionType, RiskClass


class PolicyDenied(Exception):
    pass


class PolicyDocument(BaseModel):
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_actions: list[ActionType] = Field(default_factory=lambda: list(ActionType))
    irreversible_requires_human: bool = True


class PolicyGate:
    def __init__(self, doc: PolicyDocument):
        self.doc = doc

    @classmethod
    def load(cls, path: Path) -> "PolicyGate":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(PolicyDocument.model_validate(data))

    def check_url(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if host not in self.doc.allowed_hosts and urlparse(url).scheme not in ("",):
            # allow relative / empty during in-app nav
            if url.startswith("/"):
                return
            if host not in self.doc.allowed_hosts:
                raise PolicyDenied(f"host not allowlisted: {host}")

    def check_action(self, action: ActionType, risk: RiskClass, *, human_approved: bool) -> None:
        if action not in self.doc.allowed_actions:
            raise PolicyDenied(f"action not allowlisted: {action}")
        if (
            risk == RiskClass.irreversible
            and self.doc.irreversible_requires_human
            and not human_approved
        ):
            raise PolicyDenied("irreversible action requires human approval or HITL")
