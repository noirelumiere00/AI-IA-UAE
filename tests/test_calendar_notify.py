"""5分前カレンダー通知のテスト（外部I/O無し・課金0）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from aiia.auth.token_store import InMemoryTokenStore, OAuthToken
from aiia.mcp.workspace_calendar import JST
from aiia.notify.calendar_notifier import (
    format_mention,
    notify_one,
    run_calendar_notify,
)
from aiia.schemas import CalendarEvent
from aiia.state.notified_store import InMemoryNotifiedStore

NOW = datetime(2026, 6, 12, 10, 0, tzinfo=JST)


def _ev(event_id: str, start_min: int, *, all_day: bool = False, **kw) -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        title=kw.get("title", "MTG"),
        start=None if all_day else NOW + timedelta(minutes=start_min),
        all_day=all_day,
        conference_url=kw.get("conference_url"),
        location=kw.get("location"),
    )


class _FakeCal:
    """list_upcoming_events だけ持つカレンダー（WorkspaceCalendar の窓ロジックを模倣）。"""

    def __init__(self, events: list[CalendarEvent]) -> None:
        self._events = events

    def list_upcoming_events(self, now, *, lead_minutes=5):
        out = []
        for ev in self._events:
            if ev.all_day or ev.start is None:
                continue
            mins = (ev.start.astimezone(JST) - now.astimezone(JST)).total_seconds() / 60.0
            if 0 <= mins <= lead_minutes:
                out.append(ev)
        return out


class _FakeSlack:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_mention(self, *, email: str, text: str) -> bool:
        self.sent.append((email, text))
        return True


def _factory(events):
    return lambda _token: _FakeCal(events)


def test_notifies_event_within_window() -> None:
    slack, notified = _FakeSlack(), InMemoryNotifiedStore()
    events = [_ev("e1", 4), _ev("far", 30), _ev("past", -10)]  # 4分後だけ通知対象
    r = notify_one("a@x.com", token=OAuthToken("1//a"), slack=slack, notified=notified,
                   now=NOW, calendar_factory=_factory(events))
    assert r.ok and r.notified == 1
    assert len(slack.sent) == 1 and "MTG" in slack.sent[0][1]


def test_dedup_no_double_notify() -> None:
    slack, notified = _FakeSlack(), InMemoryNotifiedStore()
    events = [_ev("e1", 4)]
    # 毎分ポーリングを模して2回連続実行 → 通知は1回だけ（claim-before-send）
    notify_one("a@x.com", token=OAuthToken("1//a"), slack=slack, notified=notified, now=NOW,
               calendar_factory=_factory(events))
    r2 = notify_one("a@x.com", token=OAuthToken("1//a"), slack=slack, notified=notified, now=NOW,
                    calendar_factory=_factory(events))
    assert r2.notified == 0 and len(slack.sent) == 1


def test_all_day_and_past_excluded() -> None:
    slack, notified = _FakeSlack(), InMemoryNotifiedStore()
    events = [_ev("allday", 0, all_day=True), _ev("past", -1)]
    r = notify_one("a@x.com", token=OAuthToken("1//a"), slack=slack, notified=notified, now=NOW,
                   calendar_factory=_factory(events))
    assert r.notified == 0 and slack.sent == []


def test_slack_oauth_gate_skips_unlinked() -> None:
    """slack_token_store 指定時は Google+Slack 両連携済みのみ通知（§V 両連携必須）。"""
    slack, notified = _FakeSlack(), InMemoryNotifiedStore()
    store = InMemoryTokenStore({"a@x.com": OAuthToken("1//a"), "b@x.com": OAuthToken("1//b")})
    slack_store = InMemoryTokenStore({"a@x.com": OAuthToken("xoxp-a")})  # a だけSlack連携済み
    events = [_ev("e1", 4)]
    res = run_calendar_notify(store=store, slack=slack, notified=notified,
                              slack_token_store=slack_store, now=NOW,
                              calendar_factory=_factory(events))
    emails = {r.email for r in res}
    assert emails == {"a@x.com"}  # b はSlack未連携でスキップ
    assert all(e == "a@x.com" for e, _ in slack.sent)


def test_format_includes_link_and_location() -> None:
    ev = _ev("e", 4, title="商談", conference_url="https://meet.google.com/abc", location="13F-B")
    s = format_mention(ev)
    assert "商談" in s and "10:04" in s and "会議に参加" in s and "13F-B" in s
