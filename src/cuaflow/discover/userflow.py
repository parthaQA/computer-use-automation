"""Chrome DevTools Recorder / Puppeteer Replay JSON from a browser-use Agent run.

browser_use.recorder.export_user_flow was never shipped; this is that shape.
Replay runs this file with Playwright (navigate / click / change / keyDown).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..orchestrator.models import Model
from ..surface.locators import enrich_step

from cua.enums import AppId, FlowId, InputName, SCHEMA_VERSION, SurfaceKind

LOOKUP_FLOW_ID = FlowId.LOOKUP_SAVINGS_BALANCE
PASSWORD_INPUT = InputName.PASSWORD
OPERATOR_INPUT = InputName.OPERATOR
MEMBER_ID_INPUT = InputName.MEMBER_ID
PRODUCT_INPUT = InputName.PRODUCT
DEPOSIT_INPUT = InputName.OPENING_DEPOSIT

StepType = Literal["navigate", "click", "change", "keyDown"]

_NAV = {"navigate", "go_to_url", "open_tab"}
_CLICK = {"click", "click_element", "click_element_by_index"}
_CONTEXT = {"context_click", "right_click", "rightclick", "contextclick", "context_menu"}
_CHANGE = {"input", "input_text", "select_dropdown", "select_dropdown_option"}
_KEY = {"send_keys", "press"}
_SKIP = {
    "done",
    "extract",
    "extract_content",
    "extract_page_content",
    "screenshot",
    "wait",
    "scroll",
    "switch_tab",
    "go_back",
    "evaluate",
}

_TECHNICAL_ID = re.compile(r"(ctl\d+|aspnet|viewstate|__|guid|\$)", re.IGNORECASE)


class VerifyCheck(BaseModel):
    """A page check that runs only if the user asked to verify, after a given action."""

    model_config = ConfigDict(extra="ignore")

    after: str = Field(
        description="The action this verify follows, e.g. Open Sub-Account. Empty only if the user asked to verify the final page."
    )
    description: str = Field(default="", description="What the user asked to confirm")
    must_see: list[str] = Field(default_factory=list, description="Phrases that must be on screen at that moment")
    must_not_see: list[str] = Field(default_factory=list)
    screen_id: str | None = Field(default=None)
    capture: bool = Field(
        default=False,
        description=(
            "Record what the page showed (member found or not found) without failing the run. "
            "Use after Search when either result is a valid outcome."
        ),
    )


class SuccessCriteria(BaseModel):
    """Verify only what the user asked to verify. Clicks are not verifications."""

    model_config = ConfigDict(extra="ignore")

    description: str = Field(
        default="",
        description="Short summary of the task. Do not invent extra verifications.",
    )
    checks: list[VerifyCheck] = Field(
        default_factory=list,
        description=(
            "ONLY when the user said verify, confirm, ensure, or check that. "
            "A click (Sign Off, Cancel, Search) is not a verify. Leave empty if they only asked to click."
        ),
    )
    must_see: list[str] = Field(
        default_factory=list,
        description="Legacy final-page phrases. Leave empty for new recordings; use checks instead.",
    )
    must_not_see: list[str] = Field(default_factory=list)
    screen_id: str | None = Field(default=None)


class Param(Model):
    """One value a caller passes in, or one value the capability returns."""

    name: str
    type: str = "string"
    required: bool = True
    secret: bool = False
    description: str = ""


class UserFlowStep(Model):
    """One recorded step. Unused fields are omitted on dump.

    selectors can list web CSS/xpath and desktop name/automationId together.
    Replay keeps the locators for the surface that is running.
    """

    type: StepType
    url: str | None = None
    desktop_url: str | None = None
    selectors: list[list[str]] | None = None
    assertText: str | None = None
    offsetX: int | None = None
    offsetY: int | None = None
    value: str | None = None
    key: str | None = None
    button: str | None = None
    how: str | None = None


class UserFlow(Model):
    """A reviewable capability: what it does, what it needs, what it returns, how it clicks."""

    schema_version: str = SCHEMA_VERSION
    id: str = LOOKUP_FLOW_ID
    version: int = 1
    title: str
    surface: str = SurfaceKind.WEB  # default adapter; replay --surface can override without a second JSON
    app: str = AppId.MEMSYS
    vendor: str = AppId.MEMSYS
    inputs: list[Param] = Field(default_factory=list)
    outputs: list[Param] = Field(default_factory=list)
    success: SuccessCriteria | None = None
    steps: list[UserFlowStep] = Field(min_length=1)


def write_user_flow(path: str | Path, flow: UserFlow) -> Path:
    """Write capability JSON (no None fields) to disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not flow.id or flow.id == LOOKUP_FLOW_ID:
        flow.id = path.stem
    path.write_text(
        json.dumps(flow.model_dump(mode="json", exclude_none=True), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def find_capability(folder: str | Path, name: str) -> Path | None:
    """Resolve a capability id or a JSON path to the workflow file."""
    raw = (name or "").strip()
    if not raw:
        return None
    direct = Path(raw)
    if direct.is_file():
        return direct
    folder = Path(folder)
    named = folder / f"{raw}.json"
    if named.is_file():
        return named
    best: Path | None = None
    best_ver = -1
    for path in sorted(folder.glob("*.json")):
        if path.name == "catalog.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("id") or path.stem) != raw:
            continue
        ver = int(data.get("version") or 1)
        if ver >= best_ver:
            best = path
            best_ver = ver
    return best


def list_capabilities(folder: str | Path) -> list[dict[str, str]]:
    """Id, version, and title for each workflow JSON (skip catalog.json)."""
    rows: list[dict[str, str]] = []
    for path in sorted(Path(folder).glob("*.json")):
        if path.name == "catalog.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or "steps" not in data:
            continue
        rows.append(
            {
                "id": str(data.get("id") or path.stem),
                "version": str(data.get("version") or 1),
                "title": str(data.get("title") or path.name),
                "file": path.name,
            }
        )
    return rows


def export_user_flow(
    agent: Any,
    *,
    title: str | None = None,
    secrets: dict[str, str] | None = None,
    success: SuccessCriteria | None = None,
    member_id: str | None = None,
    flow_id: str | None = None,
    origin: str | None = None,
) -> UserFlow:
    """Convert a finished browser-use Agent history into a capability JSON."""
    history = getattr(agent, "history", None)
    if history is None or not getattr(history, "history", None):
        raise ValueError("agent has no history to export")

    steps: list[UserFlowStep] = []
    for item in history.history:
        output = item.model_output
        if output is None:
            continue
        interacted = list(getattr(item.state, "interacted_element", None) or [])
        for index, action in enumerate(output.action):
            action_type, params = _action_payload(action)
            if not action_type or action_type in _SKIP:
                continue
            element = interacted[index] if index < len(interacted) else None
            step = _to_step(action_type, params, element, origin=origin)
            if step is not None:
                steps.append(_scrub_step(step, secrets))

    if not steps:
        raise ValueError("agent history produced no recorder steps")

    title = title or (getattr(agent, "task", None) or "browser-use flow")
    if secrets:
        title = _replace_all(title, secrets)
        if success:
            success = _scrub_success(success, secrets)
    flow = UserFlow(id=flow_id or LOOKUP_FLOW_ID, title=title, steps=steps, success=success)
    return finish_capability(flow, member_id=member_id, origin=origin)


def _scrub_success(success: SuccessCriteria, secrets: dict[str, str]) -> SuccessCriteria:
    """Replace secrets inside success checks with placeholders."""
    data = success.model_dump()
    data["description"] = _replace_all(data.get("description") or "", secrets)
    data["must_see"] = [_replace_all(item, secrets) for item in data.get("must_see") or []]
    data["must_not_see"] = [_replace_all(item, secrets) for item in data.get("must_not_see") or []]
    checks = []
    for check in data.get("checks") or []:
        check["after"] = _replace_all(check.get("after") or "", secrets)
        check["description"] = _replace_all(check.get("description") or "", secrets)
        check["must_see"] = [_replace_all(item, secrets) for item in check.get("must_see") or []]
        check["must_not_see"] = [_replace_all(item, secrets) for item in check.get("must_not_see") or []]
        checks.append(check)
    data["checks"] = checks
    return SuccessCriteria(**data)


def _action_payload(action: Any) -> tuple[str, dict[str, Any]]:
    """Unpack a browser-use action into (type, params)."""
    data = action.model_dump(exclude_none=True) if hasattr(action, "model_dump") else dict(action)
    for key, value in data.items():
        if key != "type" and isinstance(value, dict):
            return key, value
    return str(data.get("type") or ""), data


def _to_step(action_type: str, params: dict[str, Any], element: Any, origin: str | None = None) -> UserFlowStep | None:
    """Convert one agent action into a UserFlowStep."""
    if action_type in _NAV:
        url = params.get("url") or params.get("value")
        if not url:
            return None
        from ..settings import fold_recorded_url

        return UserFlowStep(type="navigate", url=fold_recorded_url(str(url), origin=origin))

    if action_type in _CLICK:
        text = _assert_text(element, params)
        return UserFlowStep(
            type="click",
            selectors=_selectors(element, text),
            assertText=text,
        )

    if action_type in _CONTEXT:
        text = _assert_text(element, params)
        return UserFlowStep(
            type="click",
            button="secondary",
            selectors=_selectors(element, text),
            assertText=text,
        )

    if action_type in _CHANGE:
        value = params.get("text") or params.get("value") or params.get("selected_text") or ""
        text = _assert_text(element, params)
        return UserFlowStep(
            type="change",
            selectors=_selectors(element, text),
            value=str(value),
        )

    if action_type in _KEY:
        key = params.get("keys") or params.get("key")
        if not key:
            return None
        return UserFlowStep(type="keyDown", key=str(key))

    return None


def _element_dict(element: Any) -> dict[str, Any]:
    """Turn a browser-use DOM element into a plain dict."""
    if element is None:
        return {}
    if isinstance(element, dict):
        return element
    if hasattr(element, "to_dict"):
        return element.to_dict()
    return {
        "node_name": getattr(element, "node_name", "") or "",
        "node_value": getattr(element, "node_value", "") or "",
        "attributes": getattr(element, "attributes", None) or {},
        "x_path": getattr(element, "x_path", "") or "",
        "ax_name": getattr(element, "ax_name", None),
    }


def _assert_text(element: Any, params: dict[str, Any]) -> str | None:
    """Visible text to assert on a click, if any."""
    data = _element_dict(element)
    attrs = data.get("attributes") or {}
    for candidate in (
        data.get("ax_name"),
        attrs.get("aria-label"),
        attrs.get("placeholder"),
        attrs.get("value") if str(attrs.get("type", "")).lower() in {"submit", "button"} else None,
        data.get("node_value"),
        params.get("text") if not params.get("index") else None,
    ):
        text = str(candidate).strip() if candidate else ""
        if text and not text.lower().startswith("javascript:"):
            return text[:120]
    return None


def _selectors(element: Any, assert_text: str | None) -> list[list[str]] | None:
    """Build ordered locator groups (CSS, xpath, desktop) for a control."""
    data = _element_dict(element)
    tag = str(data.get("node_name") or "input").lower()
    attrs = data.get("attributes") or {}
    found: list[list[str]] = []

    css = _css_selector(tag, attrs)
    if css:
        found.append([css])

    xpath = str(data.get("x_path") or data.get("xpath") or "").strip()
    if xpath:
        found.append([_xpath_selector(xpath)])

    if assert_text:
        found.append([f"text/{assert_text}"])

    return found or None


def _css_selector(tag: str, attrs: dict[str, Any]) -> str | None:
    """Best CSS locator for an element (name, type, value)."""
    ident = str(attrs.get("id") or "").strip()
    if ident and not _TECHNICAL_ID.search(ident):
        return f"#{ident}"

    parts: list[str] = []
    name = str(attrs.get("name") or "").strip()
    if name:
        parts.append(f"[name='{_escape_attr(name)}']")
    type_ = str(attrs.get("type") or "").strip()
    if type_ and tag in {"input", "button"}:
        parts.append(f"[type='{_escape_attr(type_)}']")
    href = str(attrs.get("href") or "").strip()
    if href and tag == "a":
        parts.append(f"[href='{_escape_attr(href)}']")
    placeholder = str(attrs.get("placeholder") or "").strip()
    if placeholder:
        parts.append(f"[placeholder='{_escape_attr(placeholder)}']")
    value = str(attrs.get("value") or "").strip()
    if value and type_.lower() in {"submit", "button"}:
        parts.append(f"[value='{_escape_attr(value)}']")
    if parts:
        return f"{tag}{''.join(parts)}"
    return None


def _xpath_selector(xpath: str) -> str:
    """Normalize an xpath string for Playwright."""
    if xpath.startswith("xpath"):
        return xpath
    if xpath.startswith("//"):
        return f"xpath{xpath}"
    if xpath.startswith("/"):
        return f"xpath/{xpath}"
    return f"xpath//{xpath}"


def _escape_attr(value: str) -> str:
    """Escape quotes inside a CSS attribute selector."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _scrub_step(step: UserFlowStep, secrets: dict[str, str] | None) -> UserFlowStep:
    """Replace secret strings in a step with placeholders."""
    if not secrets:
        return step
    data = step.model_dump()
    for key, value in list(data.items()):
        if isinstance(value, str):
            data[key] = _replace_all(value, secrets)
        elif key == "selectors" and isinstance(value, list):
            data[key] = [
                [_replace_all(item, secrets) if isinstance(item, str) else item for item in group]
                for group in value
            ]
    return UserFlowStep(**data)


def _replace_all(text: str, secrets: dict[str, str]) -> str:
    """Replace each secret with its placeholder in text."""
    for secret, placeholder in secrets.items():
        if secret:
            text = text.replace(secret, placeholder)
    return text


def finish_capability(flow: UserFlow, *, member_id: str | None = None, origin: str | None = None) -> UserFlow:
    """Fill in the capability contract: parameters, outputs, and how each control is found."""
    from ..settings import to_placeholder_url

    steps = []
    for step in flow.steps:
        step = _parameterize_step(step, member_id)
        if step.type == "navigate" and step.url:
            step.url = to_placeholder_url(step.url, origin=origin)
        enrich_step(step)
        step.how = _how(step)
        steps.append(step)
    flow.steps = steps
    flow.schema_version = SCHEMA_VERSION
    flow.surface = flow.surface or SurfaceKind.WEB
    flow.app = flow.app or AppId.MEMSYS
    flow.vendor = flow.vendor or AppId.MEMSYS
    if not flow.version:
        flow.version = 1
    flow.inputs = _inputs_from(steps)
    flow.outputs = _outputs_from(flow)
    return flow


def _parameterize_step(step: UserFlowStep, member_id: str | None) -> UserFlowStep:
    """Turn baked OPR/MEMID/product/amount values into {placeholders}."""
    value = step.value or ""
    if step.type != "change":
        return step
    blob = " ".join(item for group in (step.selectors or []) for item in group).upper()
    if "OPR" in blob or "OPERATOR" in blob:
        step.value = "{" + OPERATOR_INPUT + "}"
        return step
    if ("MEMID" in blob or "MEMBER" in blob) and value != "{" + MEMBER_ID_INPUT + "}":
        if (member_id and value == member_id) or value.isdigit():
            step.value = "{" + MEMBER_ID_INPUT + "}"
        return step
    if ("PROD" in blob or "PRODUCT" in blob) and value and not value.startswith("{"):
        step.value = "{" + PRODUCT_INPUT + "}"
        return step
    if ("AMT" in blob or "DEPOSIT" in blob or "AMOUNT" in blob) and value and not value.startswith("{"):
        step.value = "{" + DEPOSIT_INPUT + "}"
    return step


def _how(step: UserFlowStep) -> str:
    """Short note of how this step finds its control."""
    if step.type == "navigate":
        return "open this URL"
    if step.type == "keyDown":
        return "press a key"
    labels: list[str] = []
    for group in step.selectors or []:
        if not group:
            continue
        sel = group[0]
        if sel.startswith("xpath"):
            labels.append("xpath")
        elif sel.lower().startswith("automationid/") or sel.startswith("name/") or sel.startswith("desktop:"):
            labels.append("desktop name")
        elif sel.startswith("text/"):
            labels.append("visible text")
        elif "[name=" in sel:
            labels.append("form name")
        elif sel.startswith("#"):
            labels.append("element id")
        else:
            labels.append("css")
    if not labels:
        return "no locator recorded"
    return " then ".join(labels)


def _inputs_from(steps: list[UserFlowStep]) -> list[Param]:
    """Declare capability inputs from placeholders used in steps."""
    found: list[Param] = []
    seen: set[str] = set()
    for step in steps:
        value = step.value or ""
        if "{" + MEMBER_ID_INPUT + "}" in value and MEMBER_ID_INPUT not in seen:
            seen.add(MEMBER_ID_INPUT)
            found.append(Param(name=MEMBER_ID_INPUT, description="Membership number to look up"))
        if ("{" + OPERATOR_INPUT + "}" in value) and OPERATOR_INPUT not in seen:
            seen.add(OPERATOR_INPUT)
            found.append(
                Param(
                    name=OPERATOR_INPUT,
                    required=False,
                    secret=False,
                    description="Operator id from MEMSYS_OPERATOR in .env; not taken from the prompt",
                )
            )
        if ("{password}" in value or "x_password" in value) and PASSWORD_INPUT not in seen:
            seen.add(PASSWORD_INPUT)
            found.append(
                Param(
                    name=PASSWORD_INPUT,
                    secret=True,
                    description="Operator password from the environment; not stored in the artifact",
                )
            )
        if "{" + PRODUCT_INPUT + "}" in value and PRODUCT_INPUT not in seen:
            seen.add(PRODUCT_INPUT)
            found.append(Param(name=PRODUCT_INPUT, description="Product type code or label, e.g. 02 Secondary Shares"))
        if "{" + DEPOSIT_INPUT + "}" in value and DEPOSIT_INPUT not in seen:
            seen.add(DEPOSIT_INPUT)
            found.append(Param(name=DEPOSIT_INPUT, description="Opening deposit amount"))
    return found


def _outputs_from(flow: UserFlow) -> list[Param]:
    """Declare capability outputs from success checks."""
    parts = [flow.title]
    if flow.success:
        parts.append(flow.success.description or "")
        parts.extend(flow.success.must_see)
        if flow.success.screen_id:
            parts.append(flow.success.screen_id)
        for check in flow.success.checks:
            parts.append(check.description or "")
            parts.extend(check.must_see)
            if check.screen_id:
                parts.append(check.screen_id)
    blob = " ".join(parts).lower()
    found: list[Param] = []
    seen: set[str] = set()

    def add(name: str, description: str, required: bool = True) -> None:
        """Append a unique input or output Param."""
        if name in seen:
            return
        seen.add(name)
        found.append(Param(name=name, required=required, description=description))

    if "balance" in blob or "savings" in blob or "mem02" in blob:
        add("savings_balance", "Current savings balance as shown on the page")
        add("member_name", "Member name as shown on the page", required=False)
    if "confirmation" in blob:
        add("confirmation_number", "Posted sub-account confirmation number")
    if "sub-account" in blob or "sub account" in blob or "sub03" in blob:
        add("member_name", "Member name as shown on the page", required=False)
    if any(check.capture for check in (flow.success.checks if flow.success else [])):
        add("member_found", "true if the member record was found", required=False)
        add("member_name", "Member name as shown on the page", required=False)
        add("message", "Core message when the record was not found or blocked", required=False)
    add("screen_id", "MEMSYS screen code at the end", required=False)
    return found
