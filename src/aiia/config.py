"""設定ローダ（platform / agent / user の3層）。yaml＋環境変数オーバーレイ。

優先順: 環境変数 > yaml > 既定。秘密情報(AWSキー/トークン)はenvのみ・yaml非保存。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class ProviderProfile(BaseModel):
    name: str
    region: Optional[str] = None
    base_url: Optional[str] = None


class ModelTier(BaseModel):
    classify: str = "claude-haiku-4-5"
    summarize: str = "claude-sonnet-4-6"
    draft: str = "claude-opus-4-8"


class PlatformConfig(BaseModel):
    default_profile: str = "heuristic"
    profiles: dict[str, ProviderProfile] = Field(default_factory=dict)
    models: ModelTier = Field(default_factory=ModelTier)


class DeliveryConfig(BaseModel):
    channel: str = "slack_dm"  # slack_dm | slack_channel | email_draft | canvas
    mode: str = "draft"        # draft（人間承認） | send（要承認フラグ）


class MorningEmailConfig(BaseModel):
    gmail_query: str = "is:unread newer_than:1d -category:promotions -category:social"
    categories: list[str] = Field(
        default_factory=lambda: [
            "CLIENT_URGENT", "CLIENT_NORMAL", "PRESS_MEDIA", "VENDOR_PARTNER",
            "INTERNAL", "FINANCE_LEGAL", "NEWSLETTER",
        ]
    )
    draft_categories: list[str] = Field(default_factory=lambda: ["CLIENT_URGENT", "PRESS_MEDIA"])
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    model_overrides: dict[str, str] = Field(default_factory=dict)
    schedule: dict = Field(default_factory=dict)
    max_threads: int = 50


class UserConfig(BaseModel):
    user_id: str = "example_user"
    display_name: str = "（ユーザー名）"
    timezone: str = "Asia/Tokyo"
    slack_user_id: str = ""
    vip_senders: list[str] = Field(default_factory=list)
    vip_domains: list[str] = Field(default_factory=list)
    quiet_categories: list[str] = Field(default_factory=list)
    client_domains: list[str] = Field(default_factory=list)
    partner_domains: list[str] = Field(default_factory=list)
    internal_domain: str = ""
    keigo_level_default: int = 3


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def config_dir() -> Path:
    return Path(os.environ.get("AIIA_CONFIG_DIR", "./config"))


def current_user() -> str:
    return os.environ.get("AIIA_USER", "example_user")


def load_platform(cdir: Optional[Path] = None) -> PlatformConfig:
    cdir = cdir or config_dir()
    data = _read_yaml(cdir / "platform.yaml")
    cfg = PlatformConfig(**data) if data else PlatformConfig()
    if os.environ.get("AIIA_PROFILE"):
        cfg.default_profile = os.environ["AIIA_PROFILE"]
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if region and "bedrock" in cfg.profiles:
        cfg.profiles["bedrock"].region = region
    return cfg


def load_agent_config(name: str = "morning_email", cdir: Optional[Path] = None) -> MorningEmailConfig:
    cdir = cdir or config_dir()
    data = _read_yaml(cdir / "agents" / f"{name}.yaml")
    return MorningEmailConfig(**data) if data else MorningEmailConfig()


def load_user(user: Optional[str] = None, cdir: Optional[Path] = None) -> UserConfig:
    cdir = cdir or config_dir()
    user = user or current_user()
    data = _read_yaml(cdir / "users" / f"{user}.yaml")
    cfg = UserConfig(**data) if data else UserConfig(user_id=user)
    if os.environ.get("SLACK_DELIVERY_USER_ID"):
        cfg.slack_user_id = os.environ["SLACK_DELIVERY_USER_ID"]
    return cfg
