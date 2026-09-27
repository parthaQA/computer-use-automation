import asyncio

from cuaflow.dispatch import FlowOrchestrator
from cuaflow.discover.lookup import agent_task
from cuaflow.discover.userflow import SuccessCriteria, VerifyCheck
from cuaflow.rewrite import (
    CanonicalTask,
    CredentialPromptBlocked,
    PromptRewriter,
    credentials_in_prompt,
    stabilize,
)
from cuaflow.settings import Settings


def test_for_agent_fills_member_id_keeps_password() -> None:
    task = CanonicalTask(
        steps=[
            "Open http://127.0.0.1:8000/signon",
            "Enter operator id {operator}",
            "Enter password {password}",
            "Enter member id {member_id}",
        ],
        member_id="12345",
    )
    text = task.for_agent()
    assert "{password}" in text
    assert "{operator}" in text
    assert "12345" in text
    assert "{member_id}" not in text


def test_stabilize_signon_uses_env_placeholders() -> None:
    settings = Settings(memsys_operator="JLEE", memsys_password="demo")
    task = stabilize(
        CanonicalTask(steps=["Open base_url", "Click Search"]),
        "open base_url, enter operator id and password, click on sign on button. search member id 12345",
        settings,
    )
    assert "Enter operator id {operator}." in task.rewritten
    assert "Enter password {password}." in task.rewritten
    assert "JLEE" not in task.rewritten
    assert "demo" not in task.rewritten
    agent = task.for_agent(settings=settings)
    assert "JLEE" not in agent
    assert "demo" not in agent
    assert "{operator}" in agent
    assert "{password}" in agent


def test_stabilize_enter_operator_id_and_password_phrase() -> None:
    settings = Settings(memsys_operator="JLEE", memsys_password="demo")
    task = stabilize(
        CanonicalTask(steps=["Open base_url", "enter operator id and password", "Click Sign On", "Click Search"]),
        "open base_url, enter operator id and password, click on sign on button. search member id 99999. verify the member record",
        settings,
    )
    assert "Enter operator id {operator}." in task.rewritten
    assert "Enter password {password}." in task.rewritten
    assert "JLEE" not in task.rewritten
    assert "demo" not in task.rewritten


def test_agent_task_hides_operator_and_password() -> None:
    text = agent_task(
        "Open base_url. Enter operator id {operator}. Enter password {password}. Click Sign On.",
        {"demo": "{password}"},
        operator="JLEE",
    )
    assert "JLEE" not in text
    assert "demo" not in text
    assert "x_operator" in text
    assert "x_password" in text
    leaked = agent_task("sign on as JLEE password demo", {"demo": "{password}"}, operator="JLEE")
    assert "JLEE" not in leaked
    assert "demo" not in leaked


def test_credentials_in_prompt_allows_no_values() -> None:
    settings = Settings(memsys_operator="OPRID99", memsys_password="s3cretpw")
    assert (
        credentials_in_prompt(
            "open base_url, enter operator id and password, click on sign on button. search member id 12345",
            settings,
        )
        is None
    )
    assert credentials_in_prompt("Enter operator id {operator}. Enter password {password}.", settings) is None


def test_credentials_in_prompt_blocks_env_and_literal_values() -> None:
    settings = Settings(memsys_operator="OPRID99", memsys_password="s3cretpw")
    env_leak = credentials_in_prompt(
        "open base_url enter operator id OPRID99 password as s3cretpw click sign on",
        settings,
    )
    assert env_leak is not None
    assert "OPRID99" not in env_leak
    assert "s3cretpw" not in env_leak
    other = credentials_in_prompt("enter operator id BOB then click sign on", settings)
    assert other is not None
    typed_pw = credentials_in_prompt("enter password hunter2 then click sign on", settings)
    assert typed_pw is not None


def test_rewrite_does_not_call_llm_when_credentials_present() -> None:
    settings = Settings(memsys_operator="OPRID99", memsys_password="s3cretpw")
    called: list[str] = []

    async def boom(_self, hidden: str):
        called.append(hidden)
        raise AssertionError("rewrite LLM must not run")

    rewriter = PromptRewriter(settings)
    rewriter._invoke = boom  # type: ignore[method-assign]
    raised = False
    try:
        asyncio.run(rewriter.rewrite("enter operator id OPRID99 and password s3cretpw"))
    except CredentialPromptBlocked:
        raised = True
    assert raised
    assert called == []


def test_orchestrator_blocks_credentials_before_rewrite(tmp_path, monkeypatch) -> None:
    settings = Settings(
        memsys_operator="OPRID99",
        memsys_password="s3cretpw",
        cua_workflows_dir=str(tmp_path),
    )
    called: list[str] = []

    async def boom(self, prompt: str):
        called.append(prompt)
        raise AssertionError("rewrite must not run")

    monkeypatch.setattr(PromptRewriter, "rewrite", boom)
    code = asyncio.run(
        FlowOrchestrator(settings).run(
            "open base_url enter operator id OPRID99 password as s3cretpw",
            headed=True,
        )
    )
    assert code == 1
    assert called == []


def test_for_agent_expands_base_url_from_env() -> None:
    settings = Settings(memsys_base_url="http://127.0.0.1:8000", memsys_password="demo")
    task = CanonicalTask(steps=["Open base_url", "Enter member id {member_id}"], member_id="12345")
    text = task.for_agent(settings=settings, surface="web")
    assert "http://127.0.0.1:8000/signon" in text
    assert "base_url" not in text
    desktop = task.for_agent(settings=settings, surface="desktop")
    assert "Open app" in desktop
    assert "base_url" not in desktop
    assert "http://" not in desktop
    from cuaflow.tenant import Tenant

    tenant_text = task.for_agent(
        settings=settings, surface="web", tenant=Tenant(id="cu-a", url="https://memsys.cu-a.test")
    )
    assert "https://memsys.cu-a.test/signon" in tenant_text


def test_stabilize_folds_localhost_to_base_url() -> None:
    settings = Settings(memsys_password="demo")
    task = stabilize(
        CanonicalTask(steps=["Open http://127.0.0.1:8000/signon", "Click Search"]),
        "open base_url then click Search",
        settings,
    )
    assert "Open base_url." in task.rewritten
    assert "127.0.0.1" not in task.rewritten


def test_stabilize_keeps_open_app_for_desktop_prompt() -> None:
    settings = Settings(memsys_password="demo")
    task = stabilize(
        CanonicalTask(steps=["Open base_url", "Click Search"]),
        "open app then click Search",
        settings,
    )
    assert task.steps[0] == "Open app"
    assert "base_url" not in task.rewritten


def test_stabilize_swaps_member_and_password() -> None:
    settings = Settings(memsys_password="demo")
    task = stabilize(
        CanonicalTask(
            steps=["open the signon page", "log in as JLEE / demo", "look up member 12345"],
            member_id="12345",
        ),
        "open url http://127.0.0.1:8000/signon password as demo member id 12345",
        settings,
    )
    assert "{member_id}" in task.rewritten
    assert "12345" not in task.rewritten
    assert "{password}" in task.rewritten
    assert "demo" not in task.rewritten
    assert "{operator}" in task.rewritten
    assert "JLEE" not in task.rewritten
    assert task.member_id == "12345"


def test_extra_step_changes_canonical_text() -> None:
    base = CanonicalTask(
        steps=[
            "Open http://127.0.0.1:8000/signon",
            "Enter member id {member_id}",
            "Click Search",
        ]
    )
    extra = CanonicalTask(
        steps=[
            "Open http://127.0.0.1:8000/signon",
            "Enter member id {member_id}",
            "Click Search",
            "Click Open Sub-Account",
            "Verify the sub-account page opened",
        ]
    )
    assert extra.rewritten != base.rewritten
    assert "Open Sub-Account" in extra.rewritten


def test_for_agent_includes_query_success() -> None:
    task = CanonicalTask(
        steps=["Click Search"],
        success=SuccessCriteria(
            checks=[
                VerifyCheck(
                    after="Search",
                    description="savings balance is visible",
                    must_see=["Current savings balance"],
                )
            ]
        ),
        member_id="12345",
    )
    assert "After Search, verify on that page: Current savings balance." in task.for_agent()
    assert "Final page must contain" not in task.for_agent()


def test_stabilize_search_found_check_is_capture() -> None:
    settings = Settings(memsys_password="demo")
    task = stabilize(
        CanonicalTask(
            steps=["Click Search"],
            success=SuccessCriteria(
                description="Verify member record not found for member id 99999",
                checks=[
                    VerifyCheck(
                        after="Click Search",
                        description="Verify the member record was not found",
                        must_see=["not found"],
                    )
                ],
            ),
            member_id="99999",
        ),
        "search member id 99999 and verify record not found",
        settings,
    )
    check = task.success.checks[0]
    assert check.capture is True
    assert check.must_see == []
    assert "Capture whether the member record was found" == check.description
    agent = task.for_agent()
    assert "do not fail" in agent.lower()
    assert "not found" not in agent.lower() or "found and not found" in agent.lower()


def test_stabilize_verify_member_record_is_dynamic_capture() -> None:
    settings = Settings(memsys_password="demo")
    task = stabilize(
        CanonicalTask(steps=["Click Search"], success=SuccessCriteria(checks=[]), member_id="99999"),
        "search member id 99999 and click on search button. verify the member record",
        settings,
    )
    assert task.success.checks
    check = task.success.checks[0]
    assert check.capture is True
    assert check.after == "Click Search"
    assert check.must_see == []
    agent = task.for_agent()
    assert "found and not found are both valid" in agent.lower()


def test_click_sign_off_does_not_add_verify() -> None:
    task = CanonicalTask(
        steps=["Click Sign Off"],
        success=SuccessCriteria(checks=[]),
    )
    assert "verify" not in task.for_agent().lower()


def test_stabilize_keeps_right_click_as_blocked() -> None:
    settings = Settings(memsys_password="demo")
    task = stabilize(
        CanonicalTask(steps=["Open http://127.0.0.1:8000/signon", "Click Search"]),
        "open url http://127.0.0.1:8000/signon then right click on Search",
        settings,
    )
    assert "right click" in task.rewritten.lower()
    assert "blocked by policy" in task.rewritten.lower()


def test_stabilize_keeps_double_click_as_blocked() -> None:
    settings = Settings(memsys_password="demo")
    task = stabilize(
        CanonicalTask(steps=["Open http://127.0.0.1:8000/signon", "Click Search"]),
        "open url http://127.0.0.1:8000/signon then double click on Search",
        settings,
    )
    assert "double click" in task.rewritten.lower()
    assert "blocked by policy" in task.rewritten.lower()


def test_stabilize_keeps_blocked_host() -> None:
    settings = Settings(memsys_password="demo")
    task = stabilize(
        CanonicalTask(steps=["Open http://127.0.0.1:8000/signon", "Click Submit"]),
        "open url https://practicetestautomation.com/practice-test-login/ click Submit",
        settings,
    )
    assert "practicetestautomation.com" in task.rewritten
    assert "blocked by policy" in task.rewritten.lower()


def test_stabilize_keeps_blocked_path() -> None:
    settings = Settings(memsys_password="demo")
    task = stabilize(
        CanonicalTask(steps=["Open http://127.0.0.1:8000/signon"]),
        "open url http://127.0.0.1:8000/admin",
        settings,
    )
    assert "/admin" in task.rewritten
    assert "blocked by policy" in task.rewritten.lower()
