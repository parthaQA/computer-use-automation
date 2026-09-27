from cuaflow.checkpoint import Checkpointer
from cuaflow.replay.conditions import classify_runtime


def test_checkpointer_keeps_running_state() -> None:
    ck = Checkpointer()
    ck.record(step="navigate", screen_id="SON01", kind="clear", observed={})
    ck.record(
        step="click:Sign On",
        screen_id="INQ01",
        kind="clear",
        observed={},
    )
    ck.record(
        step="click:Search",
        screen_id="MEM02",
        kind="clear",
        observed={"savings_balance": "1,204.55", "member_name": "HERNANDEZ, MARIA"},
    )
    assert ck.screen_id == "MEM02"
    assert ck.observed["savings_balance"] == "1,204.55"
    assert len(ck.items) == 3
    trail = ck.snapshot()["trail"]
    assert trail[0] == "1:navigate:SON01:clear"
    assert trail[-1] == "3:click:Search:MEM02:clear"


def test_checkpointer_marks_business_not_ok() -> None:
    ck = Checkpointer()
    item = ck.record(
        step="click:Search",
        screen_id="INQ01",
        kind="business",
        detail="member record was not found",
        observed={},
    )
    assert item.ok is False
    assert item.kind == "business"


def test_record_page_uses_runtime_classifier() -> None:
    ck = Checkpointer()
    body = "Record not found for membership 99999."
    assert classify_runtime(body).kind == "business"
    item = ck.record_page(step="click:Search", body=body, screen="INQ01")
    assert item.kind == "business"
    assert item.ok is False
    assert item.screen_id == "INQ01"


def test_attach_puts_trail_on_outputs() -> None:
    ck = Checkpointer()
    ck.record(step="click:Search", screen_id="MEM02", kind="clear")
    outputs: dict = {}
    ck.attach(outputs)
    assert outputs["checkpoint_trail"] == ["1:click:Search:MEM02:clear"]
    assert outputs["screen_id"] == "MEM02"
