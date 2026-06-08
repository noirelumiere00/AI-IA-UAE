"""Bedrock モデルID 解決・env 上書き・doctor。"""
from __future__ import annotations

import pytest

from aiia.config import PlatformConfig
from aiia.providers import models as mr


def test_resolve_us_region_prefix() -> None:
    mid = mr.resolve_model_id("claude-haiku-4-5", "us-east-1")
    assert mid.startswith("us.anthropic.claude-haiku-4-5")


def test_resolve_eu_apac_prefix() -> None:
    assert mr.resolve_model_id("claude-sonnet-4-6", "eu-central-1").startswith("eu.anthropic.")
    assert mr.resolve_model_id("claude-sonnet-4-6", "ap-northeast-1").startswith("apac.anthropic.")


def test_resolve_no_region_bare() -> None:
    assert mr.resolve_model_id("claude-opus-4-8", None).startswith("anthropic.claude-opus-4-8")


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIIA_BEDROCK_MODEL_CLASSIFY", "custom.model.id")
    ids = mr.tier_ids(PlatformConfig(), "us-east-1")
    assert ids["classify"] == "custom.model.id"
    assert ids["summarize"].startswith("us.anthropic.")  # 他 tier は通常解決


def test_doctor_flags_missing_region() -> None:
    assert any("region" in p for p in mr.doctor(PlatformConfig(), None))


def test_doctor_ok_with_region() -> None:
    assert mr.doctor(PlatformConfig(), "us-east-1") == []
