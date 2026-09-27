"""Allowed run states and which transitions are legal."""

from cua.enums import RunState


TERMINAL_STATES = {
    RunState.SUCCEEDED,
    RunState.BUSINESS_OUTCOME,
    RunState.FAILED,
    RunState.ABORTED,
}

# "From this state, you may only move to one of these states."
ALLOWED = {
    RunState.CREATED: {RunState.PREPARING},
    RunState.PREPARING: {RunState.RUNNING, RunState.FAILED},
    RunState.RUNNING: {
        RunState.PAUSED,
        RunState.SUCCEEDED,
        RunState.BUSINESS_OUTCOME,
        RunState.FAILED,
        RunState.ABORTED,
    },
    RunState.PAUSED: {RunState.HUMAN_CONTROL, RunState.RUNNING, RunState.ABORTED, RunState.FAILED},
    RunState.HUMAN_CONTROL: {RunState.PAUSED, RunState.ABORTED, RunState.FAILED},
    RunState.SUCCEEDED: set(),
    RunState.BUSINESS_OUTCOME: set(),
    RunState.FAILED: set(),
    RunState.ABORTED: set(),
}


class InvalidTransition(Exception):
    """Raised when a run is asked to move to a state that is not allowed from here."""


def check_transition(old: RunState, new: RunState) -> None:
    """Raise an error if moving from `old` to `new` is not allowed."""
    if new not in ALLOWED[old]:
        raise InvalidTransition(f"{old.value} -> {new.value} is not allowed")