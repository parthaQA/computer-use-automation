"""Classify MEMSYS runtime page states: business outcomes, recoverables, hard failures."""

from __future__ import annotations

from pydantic import BaseModel

from cua.enums import (
    ButtonLabel,
    ConditionKind,
    OutcomeCode,
    PageNeedle,
    RecoveryAction,
    ScreenId,
)

Kind = ConditionKind


class PageCondition(BaseModel):
    """What the current page means: clear, a business outcome, or a recoverable overlay."""
    kind: ConditionKind = ConditionKind.CLEAR
    code: str | None = None
    message: str = ""
    recovery: str | None = None
    button: str | None = None


_BUSINESS: list[tuple[str, str, str]] = [
    (PageNeedle.NOT_FOUND, OutcomeCode.MEMBER_NOT_FOUND, "member record was not found"),
    (PageNeedle.SERVICING_RIGHTS, OutcomeCode.PERMISSION_DENIED, "operator lacks servicing rights"),
    (PageNeedle.LOCKED, OutcomeCode.MEMBER_LOCKED, "membership is locked"),
    (PageNeedle.INSUFFICIENT_FUNDS, OutcomeCode.INSUFFICIENT_FUNDS, "opening deposit exceeds available balance"),
]

_VALIDATION: list[tuple[str, str, str]] = [
    (PageNeedle.MEMBER_ID_REQUIRED, OutcomeCode.VALIDATION_MEMBER_ID, "Member ID is required"),
    (PageNeedle.MEMBER_ID_NUMERIC, OutcomeCode.VALIDATION_MEMBER_ID, "Member ID must be numeric"),
    (PageNeedle.PRODUCT_REQUIRED, OutcomeCode.VALIDATION_PRODUCT, "Product type is required"),
    (PageNeedle.DEPOSIT_INVALID, OutcomeCode.VALIDATION_AMOUNT, "Opening deposit is invalid"),
]


def classify_runtime(body: str, screen: str | None = None) -> PageCondition:
    """Inspect page text. Layout is stable; these are runtime conditions."""
    text = (body or "").replace("\xa0", " ")
    folded = text.lower()
    screen = (screen or "").upper()

    if PageNeedle.HUMAN_APPROVAL in folded or PageNeedle.VERIFY_HUMAN in folded:
        return PageCondition(
            kind=ConditionKind.TAKEOVER,
            code=OutcomeCode.HUMAN_TAKEOVER,
            message="human approval popup is blocking the flow",
        )

    if screen == ScreenId.SESSION_TIMEOUT or PageNeedle.SESSION_TIMEOUT in folded or PageNeedle.CONTINUE_SESSION in folded:
        return PageCondition(
            kind=ConditionKind.RECOVERABLE,
            code=OutcomeCode.SESSION_TIMEOUT,
            message="session timeout interstitial",
            recovery=RecoveryAction.CONTINUE_SESSION,
            button=ButtonLabel.CONTINUE_SESSION,
        )

    if PageNeedle.SYSTEM_MESSAGE in folded or PageNeedle.CORE_WINDOW in folded:
        return PageCondition(
            kind=ConditionKind.RECOVERABLE,
            code=OutcomeCode.SYSTEM_DIALOG,
            message="unexpected system-message overlay",
            recovery=RecoveryAction.DISMISS,
            button=ButtonLabel.DISMISS,
        )

    for needle, code, message in _BUSINESS:
        if needle in folded:
            return PageCondition(kind=ConditionKind.BUSINESS, code=code, message=message)

    for needle, code, message in _VALIDATION:
        if needle in folded:
            return PageCondition(kind=ConditionKind.VALIDATION, code=code, message=message)

    return PageCondition(kind=ConditionKind.CLEAR)


def page_business_line(body: str) -> str:
    """The visible sentence that matched a business outcome, if any."""
    text = (body or "").replace("\xa0", " ")
    folded = text.lower()
    for needle, _code, _message in _BUSINESS:
        if needle not in folded:
            continue
        for line in text.splitlines():
            if needle in line.lower():
                return line.strip()
    return ""
