import asyncio

from cuaflow.discover.userflow import UserFlowStep
from cuaflow.replay.playwright_flow import PlaywrightFlowRunner
from cuaflow.safety.policy import Policy, load_policy
from cuaflow.safety.redact import Redactor


def test_allowlist_hosts_and_paths() -> None:
    policy = load_policy()
    assert policy.check_url("http://127.0.0.1:8000/signon") is None
    assert policy.check_url("http://127.0.0.1:8000/main/inquiry") is None
    assert policy.check_url("https://example.com/signon") is not None
    assert policy.check_url("http://127.0.0.1:8000/admin") is not None


def test_desktop_launch_target_is_not_a_web_host() -> None:
    policy = load_policy()
    assert policy.check_url("desktop:memsys") is None
    assert policy.check_url("memsys-desktop") is None
    step = UserFlowStep(type="navigate", url="desktop:memsys")
    assert policy.check_step(step) is None


def test_allowlist_actions() -> None:
    policy = load_policy()
    assert policy.check_action("click") is None
    assert policy.check_action("go_to_url") is None
    assert policy.check_action("ask_human") is None
    assert policy.check_action("evaluate") is not None
    assert policy.check_action("right_click") is not None
    assert policy.check_action("contextClick") is not None


def test_empty_allowlist_denies() -> None:
    policy = Policy({"allowed_hosts": [], "allowed_paths": [], "allowed_actions": []})
    assert policy.check_url("http://127.0.0.1:8000/") is not None
    assert policy.check_action("click") is not None


def test_irreversible_is_post_new_share_not_search() -> None:
    policy = load_policy()
    post = UserFlowStep(type="click", assertText="Post New Share", selectors=[["text/Post New Share"]])
    search = UserFlowStep(type="click", assertText="Search", selectors=[["text/Search"]])
    assert policy.is_risky_step(post)
    assert not policy.is_risky_step(search)


def test_headless_blocks_irreversible_click() -> None:
    runner = PlaywrightFlowRunner(headed=False, policy=load_policy())
    step = UserFlowStep(type="click", assertText="Post New Share")
    result = asyncio.run(runner._policy_gate(step, {}))
    assert result is not None
    assert result.outcome == "failed"
    assert "irreversible" in (result.detail or "").lower()


def test_search_click_is_allowed() -> None:
    runner = PlaywrightFlowRunner(headed=False, policy=load_policy())
    step = UserFlowStep(type="click", assertText="Search")
    assert asyncio.run(runner._policy_gate(step, {})) is None


def test_off_allowlist_navigate_is_denied() -> None:
    runner = PlaywrightFlowRunner(headed=False, policy=load_policy())
    step = UserFlowStep(type="navigate", url="https://example.com/login")
    result = asyncio.run(runner._policy_gate(step, {}))
    assert result is not None
    assert result.outcome == "failed"
    assert "allowlist" in (result.detail or "")


def test_context_click_is_denied() -> None:
    runner = PlaywrightFlowRunner(headed=False, policy=load_policy())
    step = UserFlowStep(type="click", button="secondary", assertText="Sign On")
    result = asyncio.run(runner._policy_gate(step, {}))
    assert result is not None
    assert result.outcome == "failed"
    assert "contextclick" in (result.detail or "").lower()


def test_discover_prompt_blocks_right_click() -> None:
    policy = load_policy()
    assert policy.check_prompt("left click on Search") is None
    reason = policy.check_prompt(
        "open url http://127.0.0.1:8000/signon then right click on Search"
    )
    assert reason is not None
    assert "contextclick" in reason


def test_prompt_blocks_any_action_not_on_allowlist() -> None:
    policy = load_policy()
    assert policy.check_prompt("click on Search") is None
    double = policy.check_prompt("double click on Search")
    assert double is not None
    assert "doubleclick" in double
    drag = policy.check_prompt("drag the member row")
    assert drag is not None
    assert "drag" in drag
    allowed = Policy(
        {
            "allowed_hosts": ["127.0.0.1"],
            "allowed_paths": ["/"],
            "allowed_actions": ["click", "contextclick", "navigate"],
        }
    )
    assert allowed.check_prompt("right click on Search") is None
    assert allowed.check_prompt("double click on Search") is not None


def test_each_allowed_action_passes_prompt_check() -> None:
    policy = load_policy()
    samples = [
        ("navigate", "open base_url"),
        ("click", "open base_url then click Search"),
        ("change", "open base_url then enter operator id {operator}"),
        ("keydown", "open base_url then press Enter"),
        ("wait", "open base_url then wait 2 seconds"),
        ("scroll", "open base_url then scroll down"),
        ("extract", "open base_url then extract the page text"),
        ("screenshot", "open base_url then take a screenshot"),
        ("done", "open base_url then mark the task done"),
    ]
    for kind, prompt in samples:
        assert kind in policy.allowed_actions, kind
        reason = policy.check_prompt(prompt)
        assert reason is None, f"{kind} should be allowed, got {reason}"


def test_discover_prompt_blocks_host_and_path() -> None:
    policy = load_policy()
    host = policy.check_prompt("open url https://practicetestautomation.com/practice-test-login/")
    assert host is not None
    assert "practicetestautomation.com" in host
    path = policy.check_prompt("open url http://127.0.0.1:8000/admin")
    assert path is not None
    assert "/admin" in path
    assert policy.check_prompt("open url http://127.0.0.1:8000/signon") is None
    assert policy.check_prompt("open base_url") is None
    assert policy.check_prompt("open app") is None
    assert policy.check_url("{base_url}/signon") is None
    blocked_placeholder = policy.check_prompt("open base_url/admin")
    assert blocked_placeholder is not None
    assert "/admin" in blocked_placeholder


def test_discover_disallows_headless() -> None:
    policy = load_policy()
    assert policy.check_discover(headed=True) is None
    reason = policy.check_discover(headed=False)
    assert reason is not None
    assert "headless" in reason


def test_redactor_hides_password_and_member_name() -> None:
    redactor = Redactor()
    redactor.register("demo", "param:password")
    redactor.register("HERNANDEZ, MARIA", "param:member_name")
    cleaned = redactor.scrub(
        {
            "password": "demo",
            "member_name": "HERNANDEZ, MARIA",
            "savings_balance": "1,204.55",
            "note": "password is demo for HERNANDEZ, MARIA",
        }
    )
    assert cleaned["password"] == "«redacted»"
    assert cleaned["member_name"] == "«redacted»"
    assert cleaned["savings_balance"] == "1,204.55"
    assert "demo" not in cleaned["note"]
    assert "HERNANDEZ" not in cleaned["note"]


def test_redactor_hides_operator_id() -> None:
    redactor = Redactor()
    redactor.register("OPRID99", "param:operator")
    redactor.register("s3cretpw", "param:password")
    cleaned = redactor.scrub(
        {
            "operator": "OPRID99",
            "note": "Enter operator id OPRID99. Enter password s3cretpw.",
        }
    )
    assert cleaned["operator"] == "«redacted»"
    assert "OPRID99" not in cleaned["note"]
    assert "s3cretpw" not in cleaned["note"]
    assert "«param:operator»" in cleaned["note"]
    assert "«param:password»" in cleaned["note"]


def test_redactor_masks_ssn() -> None:
    cleaned = Redactor().scrub("ssn 123-45-6789 on file")
    assert "123-45-6789" not in cleaned
    assert "«ssn»" in cleaned
