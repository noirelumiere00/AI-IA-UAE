"""AnthropicLLM(Bedrock) を FakeBedrock 注入で検証（実 Bedrock を叩かない＝課金ゼロ）。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from aiia.config import PlatformConfig, ProviderProfile, UserConfig
from aiia.pipeline import PipelineDeps, run
from aiia.providers.anthropic_llm import AnthropicLLM
from aiia.schemas import Category, EmailMessage, EmailThread

# ── FakeBedrock（anthropic AnthropicBedrock 互換の最小スタブ） ──────────────


class _Block:
    def __init__(self, type: str, *, input: Any = None, text: str = "") -> None:
        self.type = type
        self.input = input or {}
        self.text = text


class _Msg:
    def __init__(self, content: list[_Block]) -> None:
        self.content = content


_DEFAULT_TOOL_INPUTS: dict[str, dict] = {
    "emit_classification": {"category": "CLIENT_NORMAL", "confidence": 0.6, "reasons": ["LLM"]},
    "emit_summary": {"thread_id": "WRONG", "one_liner": "LLM要約", "tone": "neutral", "action_items": []},
    "emit_actions": {"items": []},
}


class FakeBedrock:
    """messages.create を模倣。tool 呼びは canned input、draft は text を返す。"""

    def __init__(self, *, tool_inputs: dict | None = None, draft_text: str = "下書き本文です。",
                 never_tool: bool = False) -> None:
        self.tool_inputs = {**_DEFAULT_TOOL_INPUTS, **(tool_inputs or {})}
        self.draft_text = draft_text
        self.never_tool = never_tool
        self.calls: list[dict] = []
        self.messages = self._Messages(self)

    class _Messages:
        def __init__(self, outer: "FakeBedrock") -> None:
            self._outer = outer

        def create(self, **kw: Any) -> _Msg:
            o = self._outer
            o.calls.append(kw)
            tool_choice = kw.get("tool_choice")
            if tool_choice and tool_choice.get("type") == "tool":
                if o.never_tool:
                    return _Msg([_Block("text", text="(ツール呼ばず)")])
                name = tool_choice["name"]
                return _Msg([_Block("tool_use", input=o.tool_inputs.get(name, {}))])
            return _Msg([_Block("text", text=o.draft_text)])


def _platform() -> PlatformConfig:
    return PlatformConfig(
        default_profile="bedrock",
        profiles={"bedrock": ProviderProfile(name="bedrock", region="us-east-1")},
    )


def _thread() -> EmailThread:
    return EmailThread(
        thread_id="t1", subject="ご相談",
        messages=[EmailMessage(message_id="m", sender="a@b.example", subject="ご相談", body_text="本日中にご返信ください")],
    )


def _user() -> UserConfig:
    return UserConfig(display_name="小俣翔碁")


def test_classify_parses_tool_use_no_forbidden_params() -> None:
    fb = FakeBedrock(tool_inputs={"emit_classification": {
        "category": "CLIENT_URGENT", "confidence": 0.9, "reasons": ["VIP"], "is_vip": True}})
    c = AnthropicLLM(_platform(), client=fb).classify(_thread(), _user())
    assert c.category == Category.CLIENT_URGENT and c.is_vip
    kw = fb.calls[-1]
    assert "temperature" not in kw and "budget_tokens" not in kw  # Bedrock 制約
    assert kw["tool_choice"]["type"] == "tool"
    assert "thinking" not in kw  # 構造化呼びは thinking 無し（tool_choice と競合回避）


def test_summarize_overwrites_thread_id() -> None:
    s = AnthropicLLM(_platform(), client=FakeBedrock()).summarize(_thread())
    assert s.thread_id == "t1"  # LLM の "WRONG" を実 thread_id で上書き


def test_extract_drops_fabricated_deadline() -> None:
    fb = FakeBedrock(tool_inputs={"emit_actions": {"items": [
        {"text": "締切", "source_quote": "", "kind": "deadline"},      # 原文引用なし→棄却
        {"text": "返信", "source_quote": "ご返信ください", "kind": "action"},
    ]}})
    items = AnthropicLLM(_platform(), client=fb).extract(_thread())
    assert len(items) == 1 and items[0].kind == "action"


def test_draft_is_text_with_thinking_no_forced_tool() -> None:
    fb = FakeBedrock(draft_text="お世話になっております。承知しました。")
    from aiia.schemas import ThreadSummary

    d = AnthropicLLM(_platform(), client=fb).draft(_thread(), ThreadSummary(thread_id="t1"), _user())
    assert "お世話に" in d.body and d.subject.startswith("Re:")
    kw = fb.calls[-1]
    assert kw["thinking"]["type"] == "adaptive"
    assert kw["output_config"]["effort"] == "high"
    assert "tools" not in kw  # draft は強制ツールなし


def test_tool_use_retry_then_error() -> None:
    fb = FakeBedrock(never_tool=True)
    with pytest.raises(ValueError):
        AnthropicLLM(_platform(), client=fb).classify(_thread(), _user())
    assert len(fb.calls) == 2  # 1回リトライ


def test_init_doctor_raises_without_region() -> None:
    bad = PlatformConfig(default_profile="bedrock")  # profiles 空・region なし
    import os

    if os.environ.get("AWS_REGION"):
        pytest.skip("AWS_REGION が環境にあるため region 欠落を再現できない")
    with pytest.raises(ValueError):
        AnthropicLLM(bad, client=FakeBedrock())


def test_pipeline_two_stage_calls_llm_only_for_gray(
    make_deps: Callable[..., PipelineDeps], tools: Any
) -> None:
    # ヒューリスティック自信<0.8 のスレッドだけ LLM.classify が走る（2段分類）。
    llm = AnthropicLLM(_platform(), client=FakeBedrock())
    deps = make_deps(tools, dry_run=True)
    deps = PipelineDeps(llm=llm, tools=tools, user=deps.user, agent=deps.agent, dry_run=True, now=deps.now)
    run(deps)
    classify_calls = [c for c in llm._client.calls
                      if (c.get("tool_choice") or {}).get("name") == "emit_classification"]
    # default fixture(6通)中、灰色は t_has_draft の1通のみ
    assert len(classify_calls) == 1
