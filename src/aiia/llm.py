"""LLM 抽象（Protocol）と HeuristicLLM（ネットワーク不要の決定論的既定実装）。"""
from __future__ import annotations

import re
from typing import Optional, Protocol

from . import triage
from .config import UserConfig
from .schemas import (
    ActionItem,
    Category,
    ClassificationResult,
    DraftReply,
    EmailThread,
    ThreadSummary,
)

_SENT_SPLIT = re.compile(r"[。．\.!?！？\n]+")
_DEADLINE_HINT = re.compile(
    r"(本日中|今日中|本日まで|明日まで|今週中|今週金曜|来週|〆切|締切|deadline|by\s*eod|by\s*today|\d{1,2}/\d{1,2}|\d{1,2}月\d{1,2}日)",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


class LLM(Protocol):
    def classify(self, thread: EmailThread, user: UserConfig) -> ClassificationResult: ...
    def summarize(self, thread: EmailThread) -> ThreadSummary: ...
    def extract(self, thread: EmailThread) -> list[ActionItem]: ...
    def draft(
        self,
        thread: EmailThread,
        summary: ThreadSummary,
        user: UserConfig,
        *,
        style: Optional[str] = None,
        slack_context: Optional[str] = None,
    ) -> DraftReply: ...


class HeuristicLLM:
    """ルールベースの決定論的 LLM 代替。ネットワーク不要。"""

    name = "heuristic"

    def classify(self, thread: EmailThread, user: UserConfig) -> ClassificationResult:
        return triage.classify_from_hints(triage.heuristic_signals(thread, user))

    def summarize(self, thread: EmailThread) -> ThreadSummary:
        body = thread.body.strip()
        sents = _sentences(body)
        one_liner = sents[0][:80] if sents else (thread.subject or "(本文なし)")
        low = body.lower()
        tone = "neutral"
        if triage._any(low, triage.URGENT_KEYWORDS):
            tone = "urgent"
        elif any(w in body for w in ["申し訳", "残念", "困", "クレーム", "苦情"]):
            tone = "frustrated"
        elif any(w in body for w in ["ありがとう", "感謝", "嬉し"]):
            tone = "positive"
        return ThreadSummary(
            thread_id=thread.thread_id,
            one_liner=one_liner,
            tone=tone,  # type: ignore[arg-type]
            action_items=self.extract(thread),
        )

    def extract(self, thread: EmailThread) -> list[ActionItem]:
        items: list[ActionItem] = []
        for s in _sentences(thread.body):
            if _DEADLINE_HINT.search(s):
                items.append(ActionItem(text="締切が言及されています", source_quote=s, kind="deadline"))
            elif s.endswith("か") or "？" in s or "?" in s or s.endswith("ください"):
                items.append(ActionItem(text=s[:60], source_quote=s, kind="question"))
        return items[:5]

    def draft(
        self,
        thread: EmailThread,
        summary: ThreadSummary,
        user: UserConfig,
        *,
        style: Optional[str] = None,  # Heuristic は文体/文脈を使わない（テンプレ）。Protocol整合のため受理。
        slack_context: Optional[str] = None,
    ) -> DraftReply:
        cls = self.classify(thread, user)
        level = self._keigo_level(cls.category, user)
        sender_name = thread.sender or "ご担当者"
        subject = thread.subject or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        closing = (
            "ご確認のほど、何卒よろしくお願い申し上げます。"
            if level >= 4
            else "ご確認のほど、よろしくお願いいたします。"
        )
        body = (
            f"{sender_name} 様\n"
            f"いつも大変お世話になっております。{user.display_name}でございます。\n\n"
            f"ご連絡ありがとうございます。内容を確認のうえ、追ってご返信いたします。\n\n"
            f"{closing}\n\n"
            f"{user.display_name}"
        )
        return DraftReply(
            thread_id=thread.thread_id,
            subject=subject,
            body=body,
            rationale="中立・敬体の定型下書き（未送信・要人手確認）",
            keigo_level=level,
        )

    @staticmethod
    def _keigo_level(category: Category, user: UserConfig) -> int:
        if category in (Category.CLIENT_URGENT, Category.PRESS_MEDIA, Category.FINANCE_LEGAL):
            return 4
        if category == Category.INTERNAL:
            return 2
        return user.keigo_level_default
