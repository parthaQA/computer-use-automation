from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class EvidenceSink(Protocol):
    def write(self, run_id: str, event: dict[str, Any]) -> None: ...


class FileEvidenceSink:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, run_id: str, event: dict[str, Any]) -> None:
        path = self.root / f"{run_id}.jsonl"
        payload = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
