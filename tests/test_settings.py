from cuaflow.discover.lookup import first_url
from cuaflow.settings import (
    Settings,
    expand_base_url,
    fold_recorded_url,
    prompt_token_from_url,
    to_placeholder_url,
    web_start_url,
)


def test_web_start_url_adds_signon() -> None:
    settings = Settings(memsys_base_url="http://127.0.0.1:8000")
    assert web_start_url(settings) == "http://127.0.0.1:8000/signon"


def test_expand_open_base_url() -> None:
    settings = Settings(memsys_base_url="http://127.0.0.1:8000")
    assert "http://127.0.0.1:8000/signon" in expand_base_url("open base_url then click Search", settings)
    assert (
        expand_base_url("open base_url/main/inquiry?inject=timeout", settings)
        == "open http://127.0.0.1:8000/main/inquiry?inject=timeout"
    )


def test_fold_only_env_host() -> None:
    settings = Settings(memsys_base_url="http://127.0.0.1:8000")
    assert fold_recorded_url("http://127.0.0.1:8000/signon", settings) == "{base_url}/signon"
    assert fold_recorded_url("https://ycombinator.com", settings) == "https://ycombinator.com"
    assert (
        fold_recorded_url("https://memsys.cu-a.test/signon", settings, origin="https://memsys.cu-a.test")
        == "{base_url}/signon"
    )
    assert to_placeholder_url("base_url") == "{base_url}/signon"
    assert to_placeholder_url("base_url/main/inquiry?inject=slow") == "{base_url}/main/inquiry?inject=slow"
    assert prompt_token_from_url("http://127.0.0.1:8000/signon", settings) == "base_url"


def test_first_url_comes_from_env_not_the_prompt() -> None:
    settings = Settings(memsys_base_url="http://127.0.0.1:8000")
    assert first_url("open base_url, enter operator id and password, click on sign on button", settings) == "http://127.0.0.1:8000/signon"
    assert (
        first_url("open base_url/main/inquiry?inject=timeout", settings)
        == "http://127.0.0.1:8000/main/inquiry?inject=timeout"
    )
    assert first_url("https://example.com/login", settings) == "https://example.com/login"
    assert (
        first_url("open base_url", settings, origin="https://memsys.cu-a.test")
        == "https://memsys.cu-a.test/signon"
    )
    assert (
        expand_base_url("open base_url", settings, origin="https://memsys.cu-a.test")
        == "open https://memsys.cu-a.test/signon"
    )
