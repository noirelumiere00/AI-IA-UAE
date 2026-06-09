"""コスト集計(AnthropicLLM.cost_usd) ＋ pipeline 予算ガード(draft skip)。課金ゼロ。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aiia.config import PlatformConfig, ProviderProfile
from aiia.llm import HeuristicLLM
from aiia.mcp.fake import FakeMCPToolset
from aiia.pipeline import PipelineDeps, run
from aiia.providers.anthropic_llm import AnthropicLLM


class _Usage:
    def __init__(self, i: int, o: int) -> None:
        self.input_tokens = i
        self.output_tokens = o


class _Block:
    def __init__(self, type: str, text: str = "") -> None:
        self.type = type
        self.text = text


class _Msg:
    def __init__(self, content: list, usage: Any) -> None:
        self.content = content
        self.usage = usage


class FakeBedrockUsage:
    def __init__(self) -> None:
        self.messages = self._M()

    class _M:
        def create(self, **kw: Any) -> _Msg:
            return _Msg([_Block("text", "文体: です・ます調")], usage=_Usage(1000, 200))


def _platform() -> PlatformConfig:
    return PlatformConfig(
        default_profile="bedrock",
        profiles={"bedrock": ProviderProfile(name="bedrock", region="us-east-1")},
    )


def test_cost_accumulates_from_usage() -> None:
    llm = AnthropicLLM(_platform(), client=FakeBedrockUsage())
    assert llm.cost_usd == 0.0
    llm.summarize_text("system", "user")  # haiku 既定: 1/5 USD per 1M
    # (1000*1 + 200*5)/1e6 = 0.002
    assert abs(llm.cost_usd - 0.002) < 1e-9


class OverBudgetLLM(HeuristicLLM):
    cost_usd = 999.0  # 既に予算超過状態


def test_pipeline_skips_draft_when_over_budget(
    make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset
) -> None:
    base = make_deps(tools, dry_run=True)
    over = PipelineDeps(
        llm=OverBudgetLLM(), tools=tools, user=base.user, agent=base.agent,
        dry_run=True, now=base.now, max_budget_usd=1.0,
    )
    res = run(over)
    assert all(it.draft is None for it in res.digest.items)  # 予算超過→下書き作らない

    # 予算なし(control)では下書きが出る（要返信カテゴリ）
    ctrl = PipelineDeps(
        llm=OverBudgetLLM(), tools=tools, user=base.user, agent=base.agent,
        dry_run=True, now=base.now,  # max_budget_usd 未指定
    )
    res2 = run(ctrl)
    assert any(it.draft is not None for it in res2.digest.items)
