"""LLM の生成 factory。プロファイル（heuristic / bedrock / api）に応じて実装を選ぶ。

- heuristic: HeuristicLLM（ネットワーク不要・既定）。
- bedrock / api: anthropic_llm を遅延 import（S8 で実装）。失敗時は HeuristicLLM にフォールバック。
  本番で黙ってフォールバックすると気付けないため、AIIA_STRICT_PROVIDER=1（or strict=True）で例外化。
"""
from __future__ import annotations

import os

from aiia.config import PlatformConfig
from aiia.llm import LLM, HeuristicLLM


def build_llm(platform: PlatformConfig, *, strict: bool | None = None) -> LLM:
    profile = (platform.default_profile or "heuristic").lower()
    if profile == "heuristic":
        return HeuristicLLM()
    try:
        # S8 で providers/anthropic_llm.py を追加（Bedrock 経由 Claude）。
        from aiia.providers.anthropic_llm import AnthropicLLM  # type: ignore[attr-defined]

        return AnthropicLLM(platform)
    except Exception:
        is_strict = strict if strict is not None else os.environ.get("AIIA_STRICT_PROVIDER") == "1"
        if is_strict:
            raise
        return HeuristicLLM()
