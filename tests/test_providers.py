"""providers.build_llm のプロファイル切替とフォールバック/厳格。"""
from __future__ import annotations

import pytest

from aiia.config import PlatformConfig
from aiia.llm import HeuristicLLM
from aiia.providers import build_llm


def test_heuristic_profile() -> None:
    assert isinstance(build_llm(PlatformConfig(default_profile="heuristic")), HeuristicLLM)


def test_unimplemented_profile_falls_back_when_not_strict() -> None:
    # bedrock 実装(anthropic_llm)は未追加 → 非strict は HeuristicLLM フォールバック。
    assert isinstance(build_llm(PlatformConfig(default_profile="bedrock"), strict=False), HeuristicLLM)


def test_unimplemented_profile_strict_raises() -> None:
    with pytest.raises(Exception):
        build_llm(PlatformConfig(default_profile="bedrock"), strict=True)
