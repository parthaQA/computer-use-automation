from pathlib import Path
from types import SimpleNamespace

from cuaflow.discover.from_canonical import flow_from_sentences, steps_from_sentences
from cuaflow.discover.userflow import UserFlow, export_user_flow, write_user_flow


class _Action:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, exclude_none: bool = True) -> dict:
        return self._payload


def _agent(actions: list[dict], elements: list[object | None] | None = None) -> SimpleNamespace:
    item = SimpleNamespace(
        model_output=SimpleNamespace(action=[_Action(a) for a in actions]),
        state=SimpleNamespace(interacted_element=elements or [None] * len(actions)),
    )
    return SimpleNamespace(task="lookup", history=SimpleNamespace(history=[item]))


def test_reference_file_matches_recorder_shape() -> None:
    path = Path("workflows/lookup_savings_balance.json")
    flow = UserFlow.model_validate_json(path.read_text(encoding="utf-8"))
    assert flow.title == "MEMSYS Savings Balance Lookup"
    assert flow.steps[0].type == "navigate"
    assert flow.steps[0].url.endswith("/signon")
    change = next(step for step in flow.steps if step.type == "change" and step.value == "{password}")
    assert ["input[name='PWD'][type='password']"] in (change.selectors or [])
    click = next(step for step in flow.steps if step.assertText == "Sign On")
    assert click.type == "click"
    dumped = flow.model_dump(mode="json", exclude_none=True)
    assert "offsetX" not in dumped["steps"][0]
    assert flow.schema_version == "capability.v1"
    assert flow.id == "lookup_savings_balance"
    assert [item.name for item in flow.inputs] == ["member_id", "password"]
    assert flow.inputs[1].secret is True
    assert "savings_balance" in [item.name for item in flow.outputs]
    assert flow.steps[4].value == "{member_id}"
    assert "form name" in (flow.steps[4].how or "")


def test_export_user_flow_maps_chrome_step_types() -> None:
    element = {
        "node_name": "input",
        "node_value": "",
        "attributes": {"name": "q", "type": "text"},
        "x_path": "/html/body/input",
        "ax_name": None,
    }
    agent = _agent(
        [
            {"go_to_url": {"url": "https://ycombinator.com"}},
            {"input": {"text": "browser-use repository", "index": 1}},
            {"send_keys": {"keys": "Enter"}},
            {"click": {"index": 2}},
        ],
        elements=[None, element, None, {"node_name": "a", "node_value": "new", "attributes": {"href": "newest"}, "x_path": "//a[text()='new']", "ax_name": "new"}],
    )
    flow = export_user_flow(agent, title="Hacker News Navigation Flow", secrets={"secret": "{password}"})
    assert [step.type for step in flow.steps] == ["navigate", "change", "keyDown", "click"]
    assert flow.steps[0].url == "https://ycombinator.com"
    assert flow.steps[1].value == "browser-use repository"
    assert ["input[name='q'][type='text']"] in (flow.steps[1].selectors or [])
    assert flow.steps[2].key == "Enter"
    assert flow.steps[3].assertText == "new"
    assert ["a[href='newest']"] in (flow.steps[3].selectors or [])
    assert ["text/new"] in (flow.steps[3].selectors or [])
    assert ["name/new"] in (flow.steps[3].selectors or [])
    assert ["automationId/q"] in (flow.steps[1].selectors or [])
    assert flow.steps[1].how == "form name then xpath then desktop name"
    assert flow.steps[0].how == "open this URL"


def test_export_turns_member_id_into_an_input() -> None:
    element = {
        "node_name": "input",
        "attributes": {"name": "MEMID", "type": "text"},
        "x_path": "/html/body/input",
    }
    agent = _agent([{"input": {"text": "12345", "index": 1}}], elements=[element])
    flow = export_user_flow(
        agent,
        title="lookup",
        success=None,
        member_id="12345",
        flow_id="lookup_savings_balance",
    )
    assert flow.steps[0].value == "{member_id}"
    assert [item.name for item in flow.inputs] == ["member_id"]


def test_export_turns_product_and_deposit_into_inputs() -> None:
    product = {
        "node_name": "select",
        "attributes": {"name": "PROD"},
        "x_path": "/html/body/select",
    }
    amount = {
        "node_name": "input",
        "attributes": {"name": "AMT", "type": "text"},
        "x_path": "/html/body/input",
    }
    agent = _agent(
        [
            {"select_dropdown": {"text": "02 Secondary Shares", "index": 1}},
            {"input": {"text": "25.00", "index": 2}},
        ],
        elements=[product, amount],
    )
    flow = export_user_flow(agent, title="open sub", success=None, member_id="12345")
    assert flow.steps[0].value == "{product}"
    assert flow.steps[1].value == "{opening_deposit}"
    assert [item.name for item in flow.inputs] == ["product", "opening_deposit"]


def test_export_scrubs_password(tmp_path: Path) -> None:
    agent = _agent([{"input": {"text": "demo"}}])
    flow = export_user_flow(agent, title="t", secrets={"demo": "{password}"})
    path = write_user_flow(tmp_path / "flow.json", flow)
    raw = path.read_text(encoding="utf-8")
    assert "demo" not in raw
    assert "{password}" in raw


def test_find_capability_by_id(tmp_path: Path) -> None:
    from cuaflow.discover.userflow import find_capability, list_capabilities

    (tmp_path / "catalog.json").write_text('{"recordings":[]}', encoding="utf-8")
    path = tmp_path / "lookup_savings_balance.json"
    path.write_text(
        '{"schema_version":"capability.v1","id":"lookup_savings_balance","version":1,'
        '"title":"lookup","steps":[{"type":"navigate","url":"http://x"}]}',
        encoding="utf-8",
    )
    other = tmp_path / "other.json"
    other.write_text(
        '{"id":"open_subaccount","version":2,"title":"sub","steps":[{"type":"navigate","url":"http://x"}]}',
        encoding="utf-8",
    )
    assert find_capability(tmp_path, "lookup_savings_balance") == path
    assert find_capability(tmp_path, "open_subaccount") == other
    assert find_capability(tmp_path, str(path)) == path
    assert find_capability(tmp_path, "missing") is None
    ids = [row["id"] for row in list_capabilities(tmp_path)]
    assert "lookup_savings_balance" in ids
    assert "open_subaccount" in ids
    assert "catalog" not in ids


def test_canonical_sentences_become_dual_locator_steps() -> None:
    steps = steps_from_sentences(
        [
            "Open base_url",
            "Enter operator id {operator}",
            "Enter password {password}",
            "Click Sign On",
            "Enter member id {member_id}",
            "Click Search",
            "Verify the savings balance is visible",
        ]
    )
    assert steps[1].value == "{operator}"
    assert [step.type for step in steps] == ["navigate", "change", "change", "click", "change", "click"]
    assert steps[0].url.endswith("/signon")
    assert steps[0].desktop_url == "{app}"
    app_steps = steps_from_sentences(["Open app", "Click Search"])
    assert app_steps[0].type == "navigate"
    assert app_steps[0].desktop_url == "{app}"
    assert app_steps[0].url == "{base_url}/signon"
    assert any("automationId/OPR" in group for group in steps[1].selectors or [])
    assert any("automationId/PWD" in group for group in steps[2].selectors or [])
    assert any("name/Sign On" in group for group in steps[3].selectors or [])
    assert any("automationId/MEMID" in group for group in steps[4].selectors or [])
    flow = flow_from_sentences(
        ["Open base_url", "Enter member id 12345", "Click Search"],
        title="lookup",
        member_id="12345",
    )
    assert flow.steps[1].value == "{member_id}"


def test_capture_check_adds_found_outputs() -> None:
    from cuaflow.discover.userflow import SuccessCriteria, UserFlow, UserFlowStep, VerifyCheck, finish_capability

    flow = finish_capability(
        UserFlow(
            title="member search",
            steps=[UserFlowStep(type="navigate", url="{base_url}/signon")],
            success=SuccessCriteria(
                checks=[
                    VerifyCheck(
                        after="Click Search",
                        description="Capture whether the member record was found",
                        capture=True,
                    )
                ]
            ),
        )
    )
    names = [item.name for item in flow.outputs]
    assert "member_found" in names
    assert "message" in names
    assert "member_name" in names
