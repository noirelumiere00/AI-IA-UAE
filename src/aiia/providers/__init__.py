"""providers 層: LLM の抽象（再 export）と生成 factory。

Phase1 既定は HeuristicLLM（ネットワーク不要）。Bedrock 実装(anthropic_llm)は S8 で追加し、
factory がプロファイルに応じて切替（失敗時は HeuristicLLM フォールバック / strict で厳格）。
"""
from __future__ import annotations

from aiia.llm import LLM, HeuristicLLM
from aiia.providers.factory import build_llm

__all__ = ["LLM", "HeuristicLLM", "build_llm"]
