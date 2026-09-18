from pathlib import Path

from cua.contracts.artifact import Capability
from cua.store.files import FileCapabilityStore


def test_file_store_roundtrip(tmp_path: Path):
    store = FileCapabilityStore(tmp_path)
    cap = Capability.model_validate_json(
        Path("capabilities/lookup_savings_balance.v1.json").read_text(encoding="utf-8")
    )
    store.put(cap)
    loaded = store.get("lookup_savings_balance")
    assert loaded.id == cap.id
    assert loaded.params[0].name == "member_id"
    assert [c.id for c in store.list()] == ["lookup_savings_balance"]
