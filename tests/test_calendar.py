"""カレンダー：parse_event / today_bounds / list_today_events / 表示(_calendar_lines)。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiia.auth.token_store import OAuthToken
from aiia.delivery.slack import _calendar_full_lines, render_slack_blocks
from aiia.mcp.workspace_calendar import parse_event, today_bounds
from aiia.schemas import CalendarEvent, Digest

JST = timezone(timedelta(hours=9))


# ── parse_event ──────────────────────────────────────────────────────────────
def test_parse_event_timed_and_self_response() -> None:
    ev = parse_event({
        "id": "e1", "summary": "商談",
        "start": {"dateTime": "2026-06-09T14:00:00+09:00"},
        "end": {"dateTime": "2026-06-09T15:00:00+09:00"},
        "attendees": [{"self": True, "responseStatus": "needsAction"}],
    })
    assert ev.title == "商談" and ev.all_day is False
    assert ev.start.strftime("%H:%M") == "14:00" and ev.response_status == "needsAction"


def test_parse_event_all_day_and_no_title() -> None:
    ev = parse_event({"id": "e2", "start": {"date": "2026-06-09"}, "end": {"date": "2026-06-10"}})
    assert ev.all_day is True and ev.title == "(タイトルなし)"
    assert ev.start.tzinfo is not None  # JST 付与


def test_today_bounds_deep_night_uses_exec_day() -> None:
    lo, hi = today_bounds(datetime(2026, 6, 9, 23, 50, tzinfo=JST))
    assert lo.startswith("2026-06-09T00:00") and hi.startswith("2026-06-10T00:00")


# ── list_today_events（注入したFakeサービスで cancelled/declined を除外）─────────
class _FakeCalSvc:
    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def events(self):  # type: ignore[no-untyped-def]
        return self

    def list(self, **kw):  # type: ignore[no-untyped-def]
        return self

    def execute(self) -> dict:
        return {"items": self._items}


def test_list_today_events_filters_cancelled_and_declined() -> None:
    from aiia.mcp.workspace_calendar import WorkspaceCalendar
    svc = _FakeCalSvc([
        {"id": "ok", "summary": "出る", "start": {"dateTime": "2026-06-09T09:00:00+09:00"}},
        {"id": "cx", "status": "cancelled", "summary": "中止", "start": {"dateTime": "2026-06-09T10:00:00+09:00"}},
        {"id": "dc", "summary": "辞退", "start": {"dateTime": "2026-06-09T11:00:00+09:00"},
         "attendees": [{"self": True, "responseStatus": "declined"}]},
    ])
    evs = WorkspaceCalendar(OAuthToken("x"), service=svc).list_today_events()
    assert [e.title for e in evs] == ["出る"]  # cancelled/declined 除外


# ── 表示 _calendar_lines ──────────────────────────────────────────────────────
def _digest(events: list[CalendarEvent], *, failed: bool = False, gen=None) -> Digest:  # type: ignore[no-untyped-def]
    return Digest(generated_at=gen or datetime(2026, 6, 9, 8, 0, tzinfo=JST),
                  user_id="t", calendar_events=events, calendar_failed=failed)


def test_calendar_full_lines_failed_vs_empty_distinct() -> None:
    assert "取得できませんでした" in "\n".join(_calendar_full_lines(_digest([], failed=True)))
    assert "なし" in "\n".join(_calendar_full_lines(_digest([])))


def test_calendar_full_lines_vertical_time_left() -> None:
    # 時刻が左(等幅)・予定が右・1件ごとに改行（縦リスト）
    d = _digest([
        CalendarEvent(event_id="a", title="終日休暇", all_day=True, start=datetime(2026, 6, 9, tzinfo=JST)),
        CalendarEvent(event_id="b", title="商談", response_status="needsAction",
                      start=datetime(2026, 6, 9, 10, 0, tzinfo=JST)),
        CalendarEvent(event_id="c", title="定例", start=datetime(2026, 6, 9, 14, 0, tzinfo=JST)),
    ])
    out = _calendar_full_lines(d)
    assert "`10:00`　商談 （未応答）" in out      # 時刻左(コードスパン)＋全角space＋予定右
    assert "`14:00`　定例" in out               # 1件1行（縦並び）
    assert "終日： 終日休暇" in "\n".join(out)


def test_calendar_full_lines_folds_over_max() -> None:
    evs = [CalendarEvent(event_id=str(i), title=f"会議{i}",
                         start=datetime(2026, 6, 9, 9 + i, 0, tzinfo=JST)) for i in range(8)]
    txt = "\n".join(_calendar_full_lines(_digest(evs)))
    assert "ほか3件" in txt  # 8件 → 5件表示 + ほか3件


def test_calendar_section_present_in_blocks_within_limit() -> None:
    d = _digest([CalendarEvent(event_id="a", title="定例",
                               start=datetime(2026, 6, 9, 9, 0, tzinfo=JST))])
    blocks = render_slack_blocks(d)
    assert len(blocks) <= 49
    assert any("今日の予定" in str(b) for b in blocks)


def test_parse_event_conference_url_priority_and_location() -> None:
    # hangoutLink 優先
    e = parse_event({"id": "1", "summary": "MTG", "start": {"dateTime": "2026-06-09T14:00:00+09:00"},
                     "hangoutLink": "https://meet.google.com/abc-defg-hij", "location": "会議室E"})
    assert e.conference_url == "https://meet.google.com/abc-defg-hij" and e.location == "会議室E"
    # hangoutLink無→conferenceData
    e2 = parse_event({"id": "2", "summary": "Zoom", "start": {"dateTime": "2026-06-09T15:00:00+09:00"},
                      "conferenceData": {"entryPoints": [{"uri": "https://zoom.us/j/xyz"}]}})
    assert e2.conference_url == "https://zoom.us/j/xyz"
    # 両方無→description内URL
    e3 = parse_event({"id": "3", "summary": "定例", "start": {"dateTime": "2026-06-09T16:00:00+09:00"},
                      "description": "詳細は https://meet.google.com/desc-url まで"})
    assert "meet.google.com/desc-url" in (e3.conference_url or "")


def test_calendar_full_lines_shows_room_and_join() -> None:
    d = _digest([CalendarEvent(event_id="a", title="商談", start=datetime(2026, 6, 9, 14, 0, tzinfo=JST),
                               conference_url="https://meet.google.com/abc", location="会議室E")])
    txt = "\n".join(_calendar_full_lines(d))
    assert "会議室E" in txt and "meet.google.com/abc" in txt and "会議リンク" in txt
