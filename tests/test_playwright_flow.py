from cuaflow.discover.userflow import SuccessCriteria, UserFlowStep, VerifyCheck
from cuaflow.replay.playwright_flow import (
    apply_step_value,
    chrome_selector_to_playwright,
    classify_page,
    duplicate_click,
    extract_outputs,
    fill_placeholders,
    select_option_attempts,
    step_matches,
    with_env_credentials,
)
from cuaflow.settings import Settings


def test_chrome_selector_prefixes() -> None:
    assert chrome_selector_to_playwright("a[href='newest']") == "a[href='newest']"
    assert chrome_selector_to_playwright("xpath//a[text()='new']") == "xpath=//a[text()='new']"
    assert chrome_selector_to_playwright("text/new") == 'text="new"'
    assert chrome_selector_to_playwright("aria/Search") == '[aria-label="Search"]'


def test_fill_placeholders() -> None:
    assert fill_placeholders("{password}", {"password": "demo"}) == "demo"
    assert fill_placeholders("{operator}", {"operator": "ENVOP"}) == "ENVOP"
    assert fill_placeholders("{member_id}", {"member_id": "12345"}) == "12345"
    assert fill_placeholders("<secret>x_password</secret>", {"password": "demo"}) == "demo"
    assert fill_placeholders("12345", {"member_id": "99999"}) == "99999"
    assert fill_placeholders("{app}", {"app": "desktop:memsys-cu-a"}) == "desktop:memsys-cu-a"


def test_apply_step_value_overrides_baked_product_and_deposit() -> None:
    product = UserFlowStep(type="change", selectors=[["select[name='PROD']"]], value="01")
    assert apply_step_value(product, {"product": "02 Secondary Shares"}) == "02 Secondary Shares"
    amount = UserFlowStep(type="change", selectors=[["input[name='AMT']"]], value="25.00")
    assert apply_step_value(amount, {"opening_deposit": "99.00"}) == "99.00"
    assert apply_step_value(amount, {"member_id": "12345"}) == "25.00"
    opr = UserFlowStep(type="change", selectors=[["input[name='OPR']"]], value="JLEE")
    assert apply_step_value(opr, {"operator": "ENVOP"}) == "ENVOP"


def test_with_env_credentials_ignores_passed_operator_and_password() -> None:
    settings = Settings(memsys_operator="ENVOP", memsys_password="envpw")
    values = with_env_credentials({"operator": "prompt-op", "password": "prompt-pw", "member_id": "12345"}, settings)
    assert values["operator"] == "ENVOP"
    assert values["password"] == "envpw"
    assert values["member_id"] == "12345"


def test_extract_outputs_follows_capability_wanted_keys() -> None:
    body = (
        "Name: HERNANDEZ, MARIA\nStatus: Active\n"
        "Current savings balance (S00 SAVINGS): 1,204.55\n"
        "Confirmation NSC-20260924-1"
    )
    all_facts = extract_outputs(body, "MEM02")
    assert all_facts["savings_balance"] == "1,204.55"
    assert all_facts["member_name"] == "HERNANDEZ, MARIA"
    assert all_facts["confirmation_number"] == "NSC-20260924-1"
    assert all_facts["screen_id"] == "MEM02"
    only_balance = extract_outputs(body, "MEM02", wanted=["savings_balance"])
    assert only_balance == {"savings_balance": "1,204.55"}
    empty_wanted = extract_outputs(body, "MEM02", wanted=[])
    assert empty_wanted["member_name"] == "HERNANDEZ, MARIA"


def test_select_option_tries_label_then_code() -> None:
    attempts = select_option_attempts("02 Secondary Shares")
    assert {"label": "02 Secondary Shares"} in attempts
    assert {"value": "02"} in attempts


def test_click_prefix_still_matches_assert_text() -> None:
    post = UserFlowStep(type="click", assertText="Post New Share", selectors=[["text/Post New Share"]])
    assert step_matches(post, "Click Post New Share")


def test_duplicate_continue_clicks_are_skipped() -> None:
    first = UserFlowStep(type="click", assertText="Continue", selectors=[["text/Continue"]])
    second = UserFlowStep(type="click", assertText="Continue", selectors=[["text/Continue"]])
    post = UserFlowStep(type="click", assertText="Post New Share", selectors=[["text/Post New Share"]])
    assert duplicate_click(first, second)
    assert not duplicate_click(first, post)
    assert not duplicate_click(None, first)


_MEM02 = (
    "Name: HERNANDEZ, MARIA\nStatus: Active\n"
    "Current savings balance (S00 SAVINGS): 1,204.55\n"
    "Open Sub-Account"
)
_SUB03 = "Open Sub-Account — 12345 HERNANDEZ, MARIA\nProduct type:\nOpening deposit:"

_LOOKUP = SuccessCriteria(
    description="Member inquiry shows the savings balance",
    must_see=["Current savings balance"],
    must_not_see=["Record not found"],
    screen_id="MEM02",
)
_OPEN = SuccessCriteria(
    description="Open Sub-Account page is showing",
    must_see=["Open Sub-Account", "Product type"],
    must_not_see=["Record not found"],
    screen_id="SUB03",
)


def test_click_is_not_a_verify() -> None:
    sign_off = UserFlowStep(type="click", assertText="Sign Off", selectors=[["text/Sign Off"]])
    open_sub = UserFlowStep(type="click", assertText="Open Sub-Account", selectors=[["text/Open Sub-Account"]])
    check = VerifyCheck(after="Open Sub-Account", must_see=["Product type"], description="sub-account page opened")
    assert step_matches(open_sub, check.after)
    assert not step_matches(sign_off, check.after)
    assert not step_matches(sign_off, "Signed off")


def test_classify_uses_query_success_criteria() -> None:
    lookup = classify_page(_MEM02, "MEM02", _LOOKUP)
    assert lookup.outcome == "success"
    assert lookup.observed["savings_balance"] == "1,204.55"

    wrong_page = classify_page(_SUB03, "SUB03", _LOOKUP)
    assert wrong_page.outcome == "failed"

    opened = classify_page(_SUB03, "SUB03", _OPEN)
    assert opened.outcome == "success"
    assert opened.detail == "Open Sub-Account page is showing"

    not_found = classify_page("Record not found for membership 99999.", "INQ01", _LOOKUP)
    assert not_found.outcome == "business_outcome"
    assert not_found.outcome_code == "MEMBER_NOT_FOUND"
    assert not_found.observed["member_found"] == "false"
    assert "Record not found for membership 99999." in not_found.observed["message"]


def test_search_capture_records_found_or_not_found_without_failing() -> None:
    found = classify_page(_MEM02, "MEM02")
    assert found.outcome == "success"
    assert found.observed["member_found"] == "true"
    assert found.observed["member_name"] == "HERNANDEZ, MARIA"

    missing = classify_page("Record not found for membership 99999.", "INQ01")
    assert missing.outcome == "business_outcome"
    assert missing.outcome_code == "MEMBER_NOT_FOUND"
    assert missing.observed["member_found"] == "false"
    assert missing.observed["message"].startswith("Record not found")

    denied = classify_page(
        "Operator JLEE does not have SERVICING rights for membership 77777.",
        "INQ01",
    )
    assert denied.outcome == "business_outcome"
    assert denied.outcome_code == "PERMISSION_DENIED"
    assert denied.observed["member_found"] == "true"


def test_closed_browser_error_is_detected() -> None:
    from cuaflow.replay.playwright_flow import _is_closed_error

    assert _is_closed_error(RuntimeError("Target page, context or browser has been closed"))
    assert not _is_closed_error(LookupError("no selector matched"))
