"""LLM observe → decide → act loop. Tools are semantic, not x,y."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cua.contracts.artifact import ActionStep
from cua.surface.base import Observation, SurfaceDriver


@dataclass
class ActionTrace:
    goal: str
    start_url: str
    observations: list[Observation] = field(default_factory=list)
    steps: list[ActionStep] = field(default_factory=list)
    raw_model_turns: list[dict[str, Any]] = field(default_factory=list)


class Discoverer:
    def __init__(self, driver: SurfaceDriver, *, max_steps: int = 40):
        self.driver = driver
        self.max_steps = max_steps

    def run(self, goal: str, start_url: str) -> ActionTrace:
        raise NotImplementedError(
            "Wire Anthropic tool-calling against SurfaceDriver.observe/act. "
            "Do not persist native CUA coordinates as locators."
        )
