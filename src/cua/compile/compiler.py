"""Compile an ActionTrace into a Capability. Schema is code, not prompt."""

from __future__ import annotations

from cua.contracts.artifact import Capability
from cua.discover.agent import ActionTrace


class Compiler:
    def compile(self, trace: ActionTrace) -> Capability:
        raise NotImplementedError(
            "Map trace steps to ranked LocatorStrategy lists, bind $params, drop session refs."
        )
