"""JSON files on disk. Production swaps this for a registry without changing callers."""

from __future__ import annotations

from pathlib import Path

from cua.contracts.artifact import Capability


class FileCapabilityStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, capability_id: str, version: str) -> Path:
        safe = capability_id.replace("/", "_")
        return self.root / f"{safe}.v{version}.json"

    def put(self, capability: Capability) -> None:
        path = self._path(capability.id, capability.version)
        path.write_text(capability.model_dump_json(indent=2), encoding="utf-8")

    def get(self, capability_id: str, version: str | None = None) -> Capability:
        if version:
            path = self._path(capability_id, version)
            if not path.exists():
                raise FileNotFoundError(path)
            return Capability.model_validate_json(path.read_text(encoding="utf-8"))
        matches = sorted(self.root.glob(f"{capability_id.replace('/', '_')}.v*.json"))
        if not matches:
            raise FileNotFoundError(capability_id)
        return Capability.model_validate_json(matches[-1].read_text(encoding="utf-8"))

    def list(self) -> list[Capability]:
        out: list[Capability] = []
        for path in sorted(self.root.glob("*.json")):
            out.append(Capability.model_validate_json(path.read_text(encoding="utf-8")))
        return out
