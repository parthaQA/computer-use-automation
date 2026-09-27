from cuaflow.discover.lookup import LookupOutput, explain_result, extra_inputs_from_prompt, result_summary
from cuaflow.orchestrator.models import RunResult
from cuaflow.replay.conditions import classify_runtime, page_business_line
from cuaflow.replay.engine import _to_result, parse_set
from cuaflow.replay.playwright_flow import classify_page


def test_classify_runtime_business_and_validation() -> None:
    not_found = classify_runtime("Record not found for membership 99999.")
    assert not_found.kind == "business"
    assert not_found.code == "MEMBER_NOT_FOUND"

    denied = classify_runtime("Operator JLEE does not have SERVICING rights for membership 77777.")
    assert denied.kind == "business"
    assert denied.code == "PERMISSION_DENIED"

    locked = classify_runtime("Membership 88888 is in locked status. No maintenance permitted.")
    assert locked.kind == "business"
    assert locked.code == "MEMBER_LOCKED"

    nsf = classify_runtime("Insufficient available balance in source share.")
    assert nsf.kind == "business"
    assert nsf.code == "INSUFFICIENT_FUNDS"

    required = classify_runtime("Member ID is required — enter a 5 digit membership number.")
    assert required.kind == "validation"
    assert required.code == "VALIDATION_MEMBER_ID"

    line = page_business_line("Record not found for membership 99999.\nINQ01")
    assert line == "Record not found for membership 99999."


def test_classify_runtime_recoverable_and_takeover() -> None:
    timeout = classify_runtime("SESSION TIMEOUT Click Continue Session", "SES99")
    assert timeout.kind == "recoverable"
    assert timeout.code == "SESSION_TIMEOUT"
    assert timeout.button == "Continue Session"

    dialog = classify_runtime("MEMSYS — System Message Unexpected confirmation: core processing window is active")
    assert dialog.kind == "recoverable"
    assert dialog.button == "Dismiss"

    approval = classify_runtime("MEMSYS — Human approval required. Verify you are human.")
    assert approval.kind == "takeover"
    assert approval.code == "HUMAN_TAKEOVER"

    assert classify_runtime("Name: HERNANDEZ, MARIA").kind == "clear"


def test_classify_page_record_not_found_is_not_a_crash() -> None:
    result = classify_page("Record not found for membership 99999.", "INQ01")
    assert result.outcome == "business_outcome"
    assert result.outcome_code == "MEMBER_NOT_FOUND"


def test_to_result_maps_the_three_contract_statuses() -> None:
    ok = _to_result(
        LookupOutput(outcome="success", detail="done", observed={"savings_balance": "1,204.55"}),
        "12345",
        "workflows/x.json",
    )
    assert ok.status.value == "success"
    assert ok.outputs["savings_balance"] == "1,204.55"

    business = _to_result(
        LookupOutput(
            outcome="business_outcome",
            outcome_code="MEMBER_NOT_FOUND",
            detail="member record was not found",
            step="click:Search",
        ),
        "99999",
        "workflows/x.json",
    )
    assert business.status.value == "business_outcome"
    assert business.outcome_code == "MEMBER_NOT_FOUND"

    failed = _to_result(
        LookupOutput(
            outcome="failed",
            step="click:Search",
            expected="valid input accepted by the form",
            detail="Member ID is required",
        ),
        "12345",
        "workflows/x.json",
    )
    assert failed.status.value == "failed"
    assert failed.error is not None
    assert failed.error.step == "click:Search"
    assert failed.error.expected == "valid input accepted by the form"
    assert failed.error.observed == "Member ID is required"
    assert isinstance(failed, RunResult)
    assert "failed  step=click:Search" in result_summary(failed)
    assert "MEMBER_NOT_FOUND" in result_summary(business)

    extracted = ok.as_extracted()
    assert extracted["outcome"] == "success"
    assert extracted["observed"]["savings_balance"] == "1,204.55"
    assert extracted["outcome_code"] is None
    assert extracted["step"] is None

    biz = business.as_extracted()
    assert biz["outcome"] == "business_outcome"
    assert biz["outcome_code"] == "MEMBER_NOT_FOUND"
    assert biz["step"] == "click:Search"
    assert biz["detail"] == "member record was not found"

    fail = failed.as_extracted()
    assert fail["outcome"] == "failed"
    assert fail["step"] == "click:Search"
    assert fail["expected"] == "valid input accepted by the form"
    assert fail["detail"] == "Member ID is required"


def test_parse_set_and_extra_inputs() -> None:
    assert parse_set(["product=02", "opening_deposit=25.00"]) == {
        "product": "02",
        "opening_deposit": "25.00",
    }
    assert parse_set(["member_id=99999", "member_id=12345"])["member_id"] == "12345"
    try:
        parse_set(["nocolon"])
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "name=value" in str(exc)
    extras = extra_inputs_from_prompt(
        "open sub-account product type 02 Secondary Shares and opening deposit 25.00"
    )
    assert extras["product"].startswith("02")
    assert extras["opening_deposit"] == "25.00"


def test_explain_result_says_what_happened() -> None:
    ok = RunResult.success(
        {
            "member_id": "12345",
            "savings_balance": "1,204.55",
            "member_name": "HERNANDEZ, MARIA",
            "screen_id": "MEM02",
            "detail": "Member inquiry shows the savings balance",
            "workflow_path": "workflows/lookup_savings_balance.json",
        }
    )
    text = explain_result(ok, capability="lookup_savings_balance")
    assert "What happened" in text
    assert "capability: lookup_savings_balance" in text
    assert "status: success" in text
    assert "member_id: 12345" in text
    assert "savings_balance: 1,204.55" in text
    assert "password" not in text
    failed = RunResult.failed("click:Search", "valid input", "Member ID is required")
    fail_text = explain_result(failed, capability="lookup_savings_balance")
    assert "status: failed" in fail_text
    assert "step: click:Search" in fail_text
    assert "observed: Member ID is required" in fail_text
    failed.outputs["screenshot"] = "runs/x/failure/screenshot.png"
    assert "screenshot: runs/x/failure/screenshot.png" in explain_result(failed)


def test_classify_page_validation_is_a_hard_failure() -> None:
    result = classify_page("Member ID is required — enter a 5 digit membership number.", "INQ01")
    assert result.outcome == "failed"
    assert result.expected == "valid input accepted by the form"
    assert result.detail == "Member ID is required"
