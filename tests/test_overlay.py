from pathlib import Path

from cuaflow.discover.userflow import UserFlow, UserFlowStep
from cuaflow.replay.engine import workflow_inputs
from cuaflow.settings import Settings
from cuaflow.tenant import Tenant, TenantOverlay, apply_overlay, load_tenants, make_tenant


def _flow() -> UserFlow:
    return UserFlow(
        id="lookup_savings_balance",
        title="MEMSYS Savings Balance Lookup",
        steps=[
            UserFlowStep(type="navigate", url="{base_url}/signon"),
            UserFlowStep(
                type="change",
                value="{member_id}",
                selectors=[["input[name='MEMID']"]],
            ),
            UserFlowStep(
                type="click",
                assertText="Search",
                selectors=[["text/Search"]],
            ),
        ],
    )


def test_make_tenant_needs_id_or_url() -> None:
    assert make_tenant(tenants={}) is None
    assert make_tenant("", "", tenants={}) is None
    only_url = make_tenant(url="https://memsys.cu-a.test", tenants={})
    assert only_url is not None and only_url.id == "default"
    assert only_url.origin() == "https://memsys.cu-a.test"


def test_tenant_id_maps_to_url() -> None:
    mapping = {"cu-a": "https://memsys.cu-a.test", "lab": "http://127.0.0.1:8000"}
    tenant = make_tenant("cu-a", tenants=mapping)
    assert tenant is not None
    assert tenant.id == "cu-a"
    assert tenant.origin() == "https://memsys.cu-a.test"
    lab = make_tenant("lab", tenants=mapping)
    assert lab is not None and lab.origin() == "http://127.0.0.1:8000"
    override = make_tenant("cu-a", url="https://other.test", tenants=mapping)
    assert override is not None and override.origin() == "https://other.test"
    settings = Settings(memsys_base_url="http://127.0.0.1:8000")
    assert workflow_inputs(settings, "12345", tenant=tenant)["base_url"] == "https://memsys.cu-a.test"


def test_unknown_tenant_id_is_rejected() -> None:
    try:
        make_tenant("missing", tenants={"lab": "http://127.0.0.1:8000"})
    except KeyError as exc:
        assert "missing" in str(exc)
        assert "lab" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_load_tenants_file(tmp_path: Path) -> None:
    path = tmp_path / "tenants.json"
    path.write_text('{"cu-a": "https://memsys.cu-a.test/"}\n', encoding="utf-8")
    assert load_tenants(path) == {"cu-a": {"url": "https://memsys.cu-a.test"}}
    tenant = make_tenant("cu-a", path=path)
    assert tenant is not None and tenant.origin() == "https://memsys.cu-a.test"
    assert tenant.app_target() == "desktop:memsys"


def test_tenant_id_maps_desktop_app() -> None:
    mapping = {
        "lab": {"url": "http://127.0.0.1:8000", "app": "desktop:memsys"},
        "cu-a": {"url": "https://memsys.cu-a.test", "app": "desktop:memsys-cu-a"},
    }
    lab = make_tenant("lab", tenants=mapping)
    cu = make_tenant("cu-a", tenants=mapping)
    assert lab is not None and lab.app_target() == "desktop:memsys"
    assert cu is not None and cu.app_target() == "desktop:memsys-cu-a"
    settings = Settings(memsys_base_url="http://127.0.0.1:8000")
    assert workflow_inputs(settings, "12345", tenant=cu)["app"] == "desktop:memsys-cu-a"
    assert workflow_inputs(settings, "12345")["app"] == "desktop:memsys"


def test_tenant_url_fills_base_url_without_editing_the_artifact() -> None:
    settings = Settings(memsys_base_url="http://127.0.0.1:8000")
    tenant = Tenant(id="cu-a", url="https://memsys.cu-a.test")
    params = workflow_inputs(settings, "12345", tenant=tenant)
    assert params["base_url"] == "https://memsys.cu-a.test"
    assert params["app"] == "desktop:memsys"
    assert workflow_inputs(settings, "12345")["base_url"] == "http://127.0.0.1:8000"
    flow = _flow()
    assert flow.steps[0].url == "{base_url}/signon"


def test_overlay_swaps_host_and_keeps_base_file_untouched() -> None:
    base = UserFlow(
        id="lookup_savings_balance",
        title="MEMSYS Savings Balance Lookup",
        steps=[
            UserFlowStep(type="navigate", url="http://127.0.0.1:8000/signon"),
            UserFlowStep(
                type="change",
                value="{member_id}",
                selectors=[["input[name='MEMID']"]],
            ),
            UserFlowStep(type="click", assertText="Search", selectors=[["text/Search"]]),
        ],
    )
    overlay = TenantOverlay(
        tenant="cu-a",
        base_id="lookup_savings_balance",
        host="memsys.cu-a.test:443",
        selectors={"MEMID": "input[name='MEMBER_NO']"},
    )
    specialized = apply_overlay(base, overlay)
    assert specialized.steps[0].url == "http://memsys.cu-a.test:443/signon"
    assert specialized.steps[1].selectors[0] == ["input[name='MEMBER_NO']"]
    assert specialized.steps[1].selectors[1] == ["input[name='MEMID']"]
    assert base.steps[0].url == "http://127.0.0.1:8000/signon"
    assert base.steps[1].selectors == [["input[name='MEMID']"]]


def test_no_tenant_keeps_env_url_and_default_desktop_app() -> None:
    settings = Settings(memsys_base_url="http://127.0.0.1:8000")
    params = workflow_inputs(settings, "12345")
    assert params["base_url"] == "http://127.0.0.1:8000"
    assert params["app"] == "desktop:memsys"
    assert params["password"] == "demo"
    assert params["operator"] == "JLEE"
    blocked = workflow_inputs(settings, "12345", extra={"operator": "HACK", "password": "hack"})
    assert blocked["operator"] == "JLEE"
    assert blocked["password"] == "demo"
    from cuaflow.replay.engine import open_target

    placeholder = UserFlowStep(type="navigate", url="{base_url}/signon", desktop_url="{app}")
    literal = UserFlowStep(type="navigate", url="{base_url}/signon", desktop_url="desktop:memsys")
    assert open_target(placeholder, "web", params) == "http://127.0.0.1:8000/signon"
    assert open_target(placeholder, "desktop", params) == "desktop:memsys"
    assert open_target(literal, "desktop", params) == "desktop:memsys"


def test_capability_names_surface_not_playwright() -> None:
    flow = _flow()
    assert flow.surface == "web"
    assert flow.app == "memsys"
    assert flow.vendor == "memsys"
