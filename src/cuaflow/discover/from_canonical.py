"""Turn rewritten checklist lines into UserFlow steps with web + desktop locators."""

from __future__ import annotations

import re

from cua.enums import FieldId, FlowId, Placeholder, RecordedUrl, StepType

from ..discover.userflow import SuccessCriteria, UserFlow, UserFlowStep, finish_capability
from ..surface.locators import enrich_step

_OPEN_APP = re.compile(r"^open(?:\s+the)?(?:\s+memsys)?(?:\s+desktop)?\s+app\b", re.I)
_OPEN = re.compile(r"^open(?:\s+url)?\s+(\S+)", re.I)
_CLICK = re.compile(r"^click(?:\s+on)?\s+(.+?)(?:\s+button)?$", re.I)
_OPERATOR = re.compile(r"operator(?:\s+id)?\s+(\S+)", re.I)
_MEMBER = re.compile(r"member(?:ship)?(?:\s+id)?\s+(\{member_id\}|\d+)", re.I)
_SKIP = re.compile(r"^(verify|confirm|ensure|check)\b", re.I)


def steps_from_sentences(lines: list[str]) -> list[UserFlowStep]:
    """Turn rewritten sentences into UserFlowStep objects."""
    steps: list[UserFlowStep] = []
    for raw in lines:
        text = " ".join((raw or "").split()).rstrip(".")
        if not text or _SKIP.search(text):
            continue
        step = _one(text)
        if step is not None:
            enrich_step(step)
            steps.append(step)
    return steps


def flow_from_sentences(
    lines: list[str],
    *,
    title: str,
    success: SuccessCriteria | None = None,
    member_id: str | None = None,
    flow_id: str | None = None,
) -> UserFlow:
    """Build a UserFlow from canonical step sentences."""
    steps = steps_from_sentences(lines)
    if not steps:
        raise ValueError("rewritten steps did not map to any UI actions")
    flow = UserFlow(id=flow_id or FlowId.LOOKUP_SAVINGS_BALANCE, title=title, steps=steps, success=success)
    return finish_capability(flow, member_id=member_id)


def _one(text: str) -> UserFlowStep | None:
    """Parse one sentence into a navigate/click/change/keyDown step."""
    if _OPEN_APP.search(text):
        return UserFlowStep(type=StepType.NAVIGATE, url=RecordedUrl.SIGNON, desktop_url=RecordedUrl.APP)
    opened = _OPEN.search(text)
    if opened:
        url = opened.group(1).rstrip(".,);")
        desktop = url if url.lower().startswith("desktop:") else RecordedUrl.APP
        from ..settings import to_placeholder_url

        return UserFlowStep(type=StepType.NAVIGATE, url=to_placeholder_url(url), desktop_url=desktop)

    clicked = _CLICK.search(text)
    if clicked:
        label = clicked.group(1).strip().strip('"')
        if label == label.lower():
            label = label.title()
        return UserFlowStep(
            type=StepType.CLICK,
            assertText=label,
            selectors=[["text/" + label], ["name/" + label]],
        )

    lower = text.lower()
    if lower.startswith(("enter", "type", "fill", "set")):
        if "password" in lower:
            return UserFlowStep(
                type=StepType.CHANGE,
                value=Placeholder.PASSWORD,
                selectors=[[f"input[name='{FieldId.PASSWORD}'][type='password']"], [f"automationId/{FieldId.PASSWORD}"]],
            )
        operator = _OPERATOR.search(text)
        if operator or "operator" in lower:
            return UserFlowStep(
                type=StepType.CHANGE,
                value=Placeholder.OPERATOR,
                selectors=[[f"input[name='{FieldId.OPERATOR}'][type='text']"], [f"automationId/{FieldId.OPERATOR}"]],
            )
        member = _MEMBER.search(text)
        if member:
            return UserFlowStep(
                type=StepType.CHANGE,
                value=member.group(1),
                selectors=[[f"input[name='{FieldId.MEMBER_ID}'][type='text']"], [f"automationId/{FieldId.MEMBER_ID}"]],
            )
    return None
