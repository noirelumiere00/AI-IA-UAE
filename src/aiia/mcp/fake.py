"""テスト / dry-run 用のニセ MCP（実 Gmail/Slack に一切触れない）。

- 受信箱＝固定の fixture スレッド（VIP緊急 / プレス / ニュースレター / 社内 / 既存下書き有 / 長文）。
- create_draft / label_thread / send_draft は**実行せず** `.calls` に記録するだけ
  → 「dry-run で副作用ゼロ」「送信していない」をテストで構造的に証明できる。
- search_threads は max_results でページングする（ページング＆重複排除のテスト用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiia.schemas import CalendarEvent, EmailMessage, EmailThread

_FIXED = datetime(2026, 6, 8, 7, 0, tzinfo=timezone.utc)
_JST = timezone(timedelta(hours=9))


def _thread(
    thread_id: str,
    sender: str,
    domain: str,
    subject: str,
    body: str,
    *,
    headers: Optional[dict[str, str]] = None,
    extra_messages: int = 0,
) -> EmailThread:
    msgs = [
        EmailMessage(
            message_id=f"{thread_id}-m{i}",
            sender=sender,
            sender_domain=domain,
            subject=subject,
            date=_FIXED,
            snippet=body[:80],
            body_text=(f"（過去のやり取り{i}）\n{body}" if i else body),
            headers=headers or {},
        )
        # 最後のメッセージ（i=extra_messages）が最新 = thread.latest
        for i in range(extra_messages + 1)
    ]
    return EmailThread(thread_id=thread_id, subject=subject, messages=msgs)


def default_threads() -> list[EmailThread]:
    """代表的な受信箱（分類7カテゴリを一通り踏む）。"""
    return [
        _thread(
            "t_vip_urgent", "John Smith <ceo@bigclient.ae>", "bigclient.ae",
            "Q3クリエイティブのご確認（本日締切）",
            "お世話になります。Q3案について本日中にご確認いただけますか。",
        ),
        _thread(
            "t_press", "Sarah Chen <pr@gulfnews.com>", "gulfnews.com",
            "取材のご依頼 — マーケにおけるAI",
            "特集記事の取材を希望しております。来週30分ほどお時間いただけますか。",
        ),
        _thread(
            "t_newsletter", "News <news@saas.com>", "saas.com",
            "今月のニュースレター",
            "新機能のお知らせです。配信停止はこちら。",
            headers={"List-Unsubscribe": "<https://saas.com/unsub>"},
        ),
        _thread(
            "t_internal", "総務 <hr@newstv.co.jp>", "newstv.co.jp",
            "経費精算のお願い",
            "今月の経費精算を今週中にご提出ください。",
        ),
        _thread(
            "t_has_draft", "担当 <pm@normal.ae>", "normal.ae",
            "来月の定例MTG日程",
            "来月の定例の候補日をいくつかご相談できればと思います。",
        ),
        _thread(
            "t_long", "佐藤 <sato@client.co.jp>", "client.co.jp",
            "campaign 進行の件",
            "添付の見積をご確認のうえ、方針をご返信ください。",
            extra_messages=3,
        ),
    ]


@dataclass
class FakeGmail:
    threads: list[EmailThread] = field(default_factory=default_threads)
    existing_draft_thread_ids: set[str] = field(default_factory=lambda: {"t_has_draft"})
    calls: list[tuple] = field(default_factory=list)

    def _by_id(self, thread_id: str) -> EmailThread:
        for t in self.threads:
            if t.thread_id == thread_id:
                return t
        raise KeyError(thread_id)

    def search_threads(
        self, query: str, page_token: Optional[str] = None, max_results: int = 50
    ) -> dict:
        ids = [t.thread_id for t in self.threads]
        start = int(page_token) if page_token else 0
        page = ids[start : start + max_results]
        nxt = start + max_results
        next_token = str(nxt) if nxt < len(ids) else None
        return {"threads": [{"thread_id": tid} for tid in page], "next_page_token": next_token}

    def get_thread(self, thread_id: str) -> EmailThread:
        t = self._by_id(thread_id)
        return t.model_copy(update={"has_existing_draft": thread_id in self.existing_draft_thread_ids})

    def list_drafts(self, thread_id: Optional[str] = None) -> dict:
        ids = sorted(self.existing_draft_thread_ids)
        if thread_id is not None:
            ids = [t for t in ids if t == thread_id]
        return {"drafts": [{"thread_id": t} for t in ids]}

    def create_draft(self, *, thread_id: str, subject: str, body: str) -> str:
        # 副作用ゼロ: 実際には作らず記録のみ。
        self.calls.append(("create_draft", thread_id))
        return f"draft_{thread_id}"

    def label_thread(self, *, thread_id: str, label: str) -> None:
        self.calls.append(("label_thread", thread_id, label))


@dataclass
class FakeSlack:
    calls: list[tuple] = field(default_factory=list)

    def send_draft(self, *, channel: str, blocks: list, text: str) -> str:
        self.calls.append(("send_draft", channel, len(blocks)))
        return "slack_draft_1"


def default_events() -> list[CalendarEvent]:
    """今日の予定 fixture（時刻つき2件 + 終日1件 + 未応答1件）。"""
    d = datetime(2026, 6, 8, tzinfo=_JST)
    return [
        CalendarEvent(event_id="e1", title="タテガタ集客定例",
                      start=d.replace(hour=9, minute=30), end=d.replace(hour=10), response_status="accepted"),
        CalendarEvent(event_id="e2", title="商談 ◯◯社",
                      start=d.replace(hour=14), end=d.replace(hour=15), response_status="needsAction"),
        CalendarEvent(event_id="e3", title="健康診断", start=d, all_day=True, response_status="accepted"),
    ]


@dataclass
class FakeCalendar:
    events: list[CalendarEvent] = field(default_factory=default_events)

    def list_today_events(self, now: Optional[datetime] = None) -> list[CalendarEvent]:
        return list(self.events)


@dataclass
class FakeMCPToolset:
    gmail: FakeGmail = field(default_factory=FakeGmail)
    slack: FakeSlack = field(default_factory=FakeSlack)
    calendar: Optional[FakeCalendar] = field(default_factory=FakeCalendar)

    @property
    def calls(self) -> list[tuple]:
        """gmail/slack の副作用ログを合算（テストで「送信/作成が無い」を一括検証）。"""
        return self.gmail.calls + self.slack.calls
