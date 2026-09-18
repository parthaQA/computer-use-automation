from __future__ import annotations

from typing import Protocol, runtime_checkable

from cua.contracts.artifact import Capability


@runtime_checkable
class CapabilityStore(Protocol):
    def put(self, capability: Capability) -> None: ...

    def get(self, capability_id: str, version: str | None = None) -> Capability: ...

    def list(self) -> list[Capability]: ...
