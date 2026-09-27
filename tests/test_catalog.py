from pathlib import Path

from cuaflow.dispatch import Catalog, normalize


LOOKUP = (
    "Open base_url. Enter operator id {operator}. Enter password {password}. "
    "Click Sign On. Enter member id {member_id}. Click Search."
)


def test_catalog_finds_same_canonical_steps(tmp_path: Path) -> None:
    json_file = tmp_path / "lookup_savings_balance.json"
    json_file.write_text("{}", encoding="utf-8")
    catalog = Catalog(tmp_path)
    prompt = (
        "open base_url, enter operator id and password, click on sign on button. "
        "search member id 12345 and click on search button"
    )
    catalog.add(prompt, json_file, rewritten=LOOKUP)
    assert catalog.find(LOOKUP) == json_file
    assert catalog.find(LOOKUP.lower()) == json_file
    extra = LOOKUP + " Click Open Sub-Account. Verify the sub-account page opened."
    assert catalog.find(extra) is None
    assert catalog.find("a different task") is None
    desktop = (
        "Open app. Enter operator id {operator}. Enter password {password}. "
        "Click Sign On. Enter member id {member_id}. Click Search."
    )
    assert catalog.find(desktop) == json_file
    assert normalize("  Hello   World ") == "hello world"
