"""Bedrock(AnthropicBedrock) 経由の実LLM。LLM Protocol を満たす。

- classify / summarize / extract: **強制 tool_use**（tools+tool_choice）で構造化出力 → pydantic 検証。
- draft: text 生成（redaction は pipeline 側）。
Bedrock 制約: temperature / budget_tokens は送らない。強制 tool_choice と thinking は競合し得るため
構造化呼びは thinking 無し、draft のみ adaptive thinking + effort=high。
client は DI 可能（未指定で AnthropicBedrock を遅延構築）＝テストは課金ゼロ。
"""
from __future__ import annotations

import os
from typing import Any, Optional

from pydantic import ValidationError

from aiia import triage
from aiia.config import PlatformConfig, UserConfig
from aiia.providers import models as model_resolver
from aiia.providers import prompts, schema_tool
from aiia.schemas import (
    ActionItem,
    ClassificationResult,
    DraftReply,
    EmailThread,
    ThreadSummary,
)


class AnthropicLLM:
    """Bedrock 経由 Claude。`build_llm(platform)` から factory 経由で生成される。"""

    name = "bedrock"

    def __init__(
        self, platform: PlatformConfig, *, client: Any = None, region: Optional[str] = None
    ) -> None:
        prof = platform.profiles.get(platform.default_profile)
        self._region = region or (prof.region if prof else None) or os.environ.get("AWS_REGION")
        problems = model_resolver.doctor(platform, self._region)
        if problems:
            raise ValueError("Bedrock 設定不備: " + "; ".join(problems))
        self._models = model_resolver.tier_ids(platform, self._region)
        if client is not None:
            self._client = client
        else:
            from anthropic import AnthropicBedrock  # 遅延 import（未導入でも本番以外は動く）

            self._client = AnthropicBedrock(aws_region=self._region)

    # ── 構造化出力（強制 tool_use・1回リトライ） ─────────────────────────────
    def _emit(self, *, model: str, system: str, user: str, tool: dict[str, Any]) -> dict[str, Any]:
        prompt = user
        for _ in range(2):
            msg = self._client.messages.create(
                model=model,
                max_tokens=2048,
                system=system,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": prompt}],
            )
            for block in msg.content:
                if getattr(block, "type", None) == "tool_use":
                    return dict(block.input)
            prompt = user + "\n\n必ず指定のツールを1回呼び出し、JSONで出力してください。"
        raise ValueError(f"tool_use 応答が得られませんでした (tool={tool['name']})")

    def classify(self, thread: EmailThread, user: UserConfig) -> ClassificationResult:
        heur = triage.classify_from_hints(triage.heuristic_signals(thread, user))
        tool = schema_tool.tool_from(
            ClassificationResult, name="emit_classification", description="メールの分類結果を出力"
        )
        data = self._emit(
            model=self._models["classify"],
            system=prompts.CLASSIFY_SYSTEM,
            user=prompts.classify_user_prompt(thread, heur),
            tool=tool,
        )
        return ClassificationResult.model_validate(data)

    def summarize(self, thread: EmailThread) -> ThreadSummary:
        tool = schema_tool.tool_from(
            ThreadSummary, name="emit_summary", description="スレッド要約を出力"
        )
        data = self._emit(
            model=self._models["summarize"],
            system=prompts.SUMMARIZE_SYSTEM,
            user=prompts.summarize_user_prompt(thread),
            tool=tool,
        )
        data["thread_id"] = thread.thread_id  # LLM の値で上書き（正しい thread_id を強制）
        return ThreadSummary.model_validate(data)

    def extract(self, thread: EmailThread) -> list[ActionItem]:
        tool = schema_tool.list_tool_from(
            ActionItem, name="emit_actions", description="アクション/質問/締切を抽出"
        )
        data = self._emit(
            model=self._models["summarize"],
            system=prompts.EXTRACT_SYSTEM,
            user=prompts.extract_user_prompt(thread),
            tool=tool,
        )
        out: list[ActionItem] = []
        for item in data.get("items", []):
            try:
                out.append(ActionItem.model_validate(item))
            except ValidationError:
                continue  # 締切に source_quote が無い等（捏造）は構造的に弾く
        return out

    def draft(self, thread: EmailThread, summary: ThreadSummary, user: UserConfig) -> DraftReply:
        msg = self._client.messages.create(
            model=self._models["draft"],
            max_tokens=2048,
            system=prompts.DRAFT_SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            messages=[{"role": "user", "content": prompts.draft_user_prompt(thread, summary, user)}],
        )
        body = "".join(
            getattr(b, "text", "") for b in msg.content if getattr(b, "type", None) == "text"
        ).strip()
        subj = thread.subject if thread.subject.lower().startswith("re:") else f"Re: {thread.subject}"
        return DraftReply(
            thread_id=thread.thread_id,
            subject=subj,
            body=body,
            rationale="Bedrock(Claude)による返信下書き",
            keigo_level=user.keigo_level_default,
        )
