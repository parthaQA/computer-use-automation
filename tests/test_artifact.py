from cua.contracts.actions import LocatorKind
from cua.contracts.artifact import ActionStep, Capability, Checkpoint, LocatorStrategy, ParamSpec


def _cap(**kwargs) -> Capability:
    base = dict(
        id="lookup_savings_balance",
        name="Look up member savings balance",
        description="demo",
        vendor_app="northstar.memsys",
        params=[ParamSpec(name="member_id", pii_class="identifier")],
        steps=[
            ActionStep(
                id="fill",
                intent="type member id",
                action="type",
                target=[LocatorStrategy(kind=LocatorKind.name_attr, value="MEMID")],
                input_binding="$params.member_id",
            )
        ],
        success_checkpoint=Checkpoint(kind="screen_id", value="MEM02"),
    )
    base.update(kwargs)
    return Capability.model_validate(base)


def test_bind_requires_params():
    cap = _cap()
    bound = cap.bind({"member_id": "12345"})
    assert bound["member_id"] == "12345"


def test_unknown_param_rejected():
    cap = _cap()
    try:
        cap.bind({"member_id": "12345", "ssn": "x"})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "unknown" in str(e)


def test_visual_locator_must_be_last():
    try:
        ActionStep(
            id="x",
            intent="bad",
            action="click",
            target=[
                LocatorStrategy(kind=LocatorKind.visual, value="12,40", unstable=True),
                LocatorStrategy(kind=LocatorKind.name_attr, value="MEMID"),
            ],
        )
        assert False, "expected validation error"
    except Exception:
        pass
