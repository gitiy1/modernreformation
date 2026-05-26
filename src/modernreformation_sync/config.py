from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


def expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default or "")

        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


class SiteConfig(BaseModel):
    title: str = "Modern Reformation Bilingual"
    description: str = "Bilingual Modern Reformation articles for e-readers."
    base_url: str = ""
    output_dir: Path = Path("public")
    state_file: Path = Path(".mr-sync/state.json")
    cache_dir: Path = Path(".mr-sync/cache")

    @field_validator("base_url")
    @classmethod
    def trim_base_url(cls, value: str) -> str:
        return value.rstrip("/")


class SourceConfig(BaseModel):
    sanity_project_id: str = "sxo7ym47"
    sanity_dataset: str = "production"
    sanity_api_version: str = "2023-07-20"
    limit: int = Field(default=10, ge=1)
    resource_types: list[str] = Field(default_factory=list)
    include_state_articles: bool = True


class TranslationConfig(BaseModel):
    enabled: bool = False
    provider: str = "openai"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    target_language: str = "Simplified Chinese"
    request_interval_seconds: float = Field(default=0, ge=0)
    rpm: int = Field(default=0, ge=0)
    max_requests_per_run: int = Field(default=0, ge=0)
    chunk_chars: int = Field(default=2500, ge=500)
    batch_enabled: bool = True
    batch_separator: str = "%%"
    max_batch_items: int = Field(default=6, ge=1)
    max_batch_chars: int = Field(default=12000, ge=1000)
    budget_exceeded: Literal["fail", "keep_original"] = "fail"
    max_retries: int = Field(default=3, ge=0)
    base_retry_delay_seconds: float = Field(default=2, ge=0)
    reasoning_effort: str | None = None
    max_completion_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = 0.2
    merge_system_prompt: bool = False

    @field_validator("base_url")
    @classmethod
    def trim_base_url(cls, value: str) -> str:
        return value.rstrip("/")


class ReadeckConfig(BaseModel):
    enabled: bool = False
    base_url: str = ""
    token: str = ""
    label: str = "modern-reformation"
    translated_label: str = "translated"
    collection_name: str = "Modern Reformation"
    keep: int = Field(default=30, ge=0)
    archive_before_delete: bool = False
    existing_policy: Literal["replace", "patch_metadata", "skip"] = "replace"
    image_mode: Literal["multipart", "remote"] = "multipart"
    allowed_image_hosts: list[str] = Field(default_factory=lambda: ["cdn.sanity.io"])
    max_image_count: int = Field(default=12, ge=0)
    max_image_bytes: int = Field(default=5_000_000, ge=1)
    max_total_image_bytes: int = Field(default=30_000_000, ge=1)
    request_timeout_seconds: float = Field(default=120, ge=1)

    @field_validator("base_url")
    @classmethod
    def trim_base_url(cls, value: str) -> str:
        return value.rstrip("/")


class BibleConfig(BaseModel):
    enabled: bool = True
    usfx_zip_path: Path = Path(".mr-sync/cache/cmn-cu89s_usfx.zip")


class AppConfig(BaseModel):
    site: SiteConfig = Field(default_factory=SiteConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    readeck: ReadeckConfig = Field(default_factory=ReadeckConfig)
    bible: BibleConfig = Field(default_factory=BibleConfig)


def load_config(path: Path) -> AppConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return AppConfig.model_validate(expand_env(data or {}))
