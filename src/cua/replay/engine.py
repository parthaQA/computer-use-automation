"""Deterministic interpreter. No LLM in the decision loop."""

from __future__ import annotations

from uuid import uuid4

from cua.contracts.artifact import Capability
from cua.contracts.result import RunResult, RunStatus, StepError
from cua.policy.gate import PolicyDenied, PolicyGate
from cua.policy.redaction import redact
from cua.surface.base import ActionRequest, SurfaceDriver
from cua.tenant.overlay import apply_overlay


class ReplayEngine:
    def __init__(self, driver: SurfaceDriver, policy: PolicyGate | None = None):
        self.driver = driver
        self.policy = policy

    def run(
        self,
        capability: Capability,
        params: dict,
        *,
        tenant_id: str | None = None,
        human_approved: bool = False,
    ) -> RunResult:
        run_id = uuid4().hex[:12]
        bound = capability.bind(params)
        cap = apply_overlay(capability, tenant_id) if tenant_id else capability
        outputs: dict = {}

        if self.policy:
            try:
                for step in cap.steps:
                    self.policy.check_action(
                        step.action, cap.risk_class, human_approved=human_approved
                    )
            except PolicyDenied as e:
                return RunResult(
                    status=RunStatus.escalated,
                    capability_id=cap.id,
                    run_id=run_id,
                    error=StepError(step_id="policy", expected="allow", observed=str(e)),
                )

        for step in cap.steps:
            value = None
            if step.input_binding and step.input_binding.startswith("$params."):
                value = str(bound[step.input_binding.split(".", 1)[1]])
            try:
                result = self.driver.act(
                    ActionRequest(action=step.action, target=step.target, value=value)
                )
            except Exception as e:
                if step.on_error.value == "business_outcome":
                    return RunResult(
                        status=RunStatus.business_outcome,
                        capability_id=cap.id,
                        run_id=run_id,
                        outcome_code=step.outcome_code,  # type: ignore[arg-type]
                        error=StepError(
                            step_id=step.id,
                            expected=step.intent,
                            observed=redact(str(e)),
                        ),
                    )
                if step.on_error.value == "escalate":
                    return RunResult(
                        status=RunStatus.escalated,
                        capability_id=cap.id,
                        run_id=run_id,
                        error=StepError(step_id=step.id, expected=step.intent, observed=redact(str(e))),
                    )
                return RunResult(
                    status=RunStatus.failed,
                    capability_id=cap.id,
                    run_id=run_id,
                    error=StepError(step_id=step.id, expected=step.intent, observed=redact(str(e))),
                )
            if step.action.value == "extract" and result.extracted is not None:
                # map by step id until compiler names outputs
                outputs[step.id] = redact(result.extracted)
            for ck in step.postconditions:
                if not self.driver.checkpoint(ck.kind, ck.value):
                    return RunResult(
                        status=RunStatus.failed,
                        capability_id=cap.id,
                        run_id=run_id,
                        error=StepError(
                            step_id=step.id,
                            expected=f"{ck.kind}:{ck.value}",
                            observed="checkpoint missed",
                        ),
                    )

        ok = self.driver.checkpoint(
            cap.success_checkpoint.kind, cap.success_checkpoint.value
        )
        if not ok:
            return RunResult(
                status=RunStatus.failed,
                capability_id=cap.id,
                run_id=run_id,
                error=StepError(
                    step_id="success",
                    expected=cap.success_checkpoint.value,
                    observed="success checkpoint missed",
                ),
            )
        return RunResult(
            status=RunStatus.succeeded,
            capability_id=cap.id,
            run_id=run_id,
            outputs=outputs,
        )
