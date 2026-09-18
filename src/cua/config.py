from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    memsys_base_url: str = "http://127.0.0.1:8000"
    capabilities_dir: Path = Path("capabilities")
    evidence_dir: Path = Path("evidence")
    policy_path: Path = Path("policies/default.json")
    max_discover_steps: int = 40
    discover_timeout_s: int = 180


def load_settings() -> Settings:
    return Settings()
