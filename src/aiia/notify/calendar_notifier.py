"""カレンダー予定の「5分前」Slackメンション通知。

連携済み(Google Workspace + Slack)ユーザーをループし、もうすぐ始まる予定を本人DMへ
`<@uid> まもなく…` で通知する。**claim-before-send**（NotifiedStore）で毎分ポーリングでも1回だけ。
高頻度（毎分 systemd timer / ループ）で `run_calendar_notify()` を回す想定。純関数寄りで DI 可能＝課金0テスト。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from aiia.auth.token_store import OAuthToken, TokenStore
from aiia.mcp.workspace_calendar import JST, WorkspaceCalendar
from aiia.schemas import CalendarEvent
from aiia.state.notified_store import NotifiedStore


@dataclass
class NotifyResult:
    email: str
    notified: int = 0
    ok: bool = True
    error: Optional[str] = None


def format_mention(ev: CalendarEvent) -> str:
    """通知文（メンションは送信側で付与）。開始時刻＋会議リンク＋場所。"""
    when = ev.start.astimezone(JST).strftime("%H:%M") if ev.start else ""
    parts = [f"まもなく *{ev.title}*（{when} 開始）"]
    if ev.conference_url:
        parts.append(f"<{ev.conference_url}|会議に参加>")
    if ev.location:
        parts.append(f"📍 {ev.location}")
    return "　·　".join(parts)


def notify_one(
    email: str,
    *,
    token: OAuthToken,
    slack: Any,
    notified: NotifiedStore,
    now: datetime,
    lead_minutes: int = 5,
    calendar_factory: Optional[Callable[[OAuthToken], Any]] = None,
) -> NotifyResult:
    """1ユーザー分：直近開始の予定を未通知のものだけ本人DMへメンション。1人の失敗は隔離。"""
    try:
        cal = calendar_factory(token) if calendar_factory else WorkspaceCalendar(token)
        upcoming = cal.list_upcoming_events(now, lead_minutes=lead_minutes)
        date_key = now.astimezone(JST).strftime("%Y-%m-%d")
        sent = 0
        for ev in upcoming:
            if not ev.event_id:
                continue
            if not notified.claim(email, f"{date_key}#{ev.event_id}"):
                continue  # 既に通知済み
            if slack.send_mention(email=email, text=format_mention(ev)):
                sent += 1
        return NotifyResult(email, notified=sent)
    except Exception as exc:  # noqa: BLE001 — 1人の失敗で他ユーザーを止めない
        return NotifyResult(email, ok=False, error=type(exc).__name__)


def run_calendar_notify(
    *,
    store: TokenStore,
    slack: Any,
    notified: NotifiedStore,
    slack_token_store: Optional[TokenStore] = None,  # 指定時=Slack連携必須ゲート
    now: Optional[datetime] = None,
    lead_minutes: int = 5,
    calendar_factory: Optional[Callable[[OAuthToken], Any]] = None,
) -> list[NotifyResult]:
    """連携済み全員ループ。slack_token_store 指定時は **Google+Slack 両連携済みのみ** active。"""
    n = now or datetime.now(JST)
    results: list[NotifyResult] = []
    for email in store.list_emails():
        if slack_token_store is not None and not slack_token_store.has(email):
            continue  # §V: Slack OAuth 未連携は通知対象外（両連携必須）
        token = store.get(email)
        if token is None:
            continue
        results.append(
            notify_one(
                email,
                token=token,
                slack=slack,
                notified=notified,
                now=n,
                lead_minutes=lead_minutes,
                calendar_factory=calendar_factory,
            )
        )
    return results
