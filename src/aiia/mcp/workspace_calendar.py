"""本人カレンダー（Google Calendar）読み取り専用ツール。

`CalendarTools` Protocol を満たす。**書込メソッド（insert/delete/update）は意図的に持たせない**
（Gmail の「send を出さない」規律と同じ＝書込スコープは持つが構造的に呼べない）。
googleapiclient は遅延 import（未導入でも他テストは動く）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiia.auth.token_store import OAuthToken
from aiia.schemas import CalendarEvent

JST = timezone(timedelta(hours=9))


def _parse_dt(d: dict) -> Optional[datetime]:
    if not d:
        return None
    if d.get("dateTime"):
        return datetime.fromisoformat(d["dateTime"])
    if d.get("date"):  # 終日: 日付のみ → JST 0時として扱う
        return datetime.fromisoformat(d["date"]).replace(tzinfo=JST)
    return None


def _self_response(ev: dict) -> str:
    for a in ev.get("attendees", []) or []:
        if a.get("self"):
            return str(a.get("responseStatus", "accepted"))
    return "accepted"  # 招待者なし＝自分の予定


def parse_event(ev: dict) -> CalendarEvent:
    """Google Calendar events.list の1件 → CalendarEvent（純関数・テスト可能）。"""
    start = ev.get("start", {}) or {}
    return CalendarEvent(
        event_id=str(ev.get("id", "")),
        title=str(ev.get("summary") or "(タイトルなし)"),
        start=_parse_dt(start),
        end=_parse_dt(ev.get("end", {}) or {}),
        all_day="date" in start,
        response_status=_self_response(ev),
    )


def today_bounds(now: Optional[datetime] = None) -> tuple[str, str]:
    """今日(JST)の [00:00, 翌00:00) の RFC3339 文字列。深夜実行でも実行時刻のJST暦日で確定。"""
    n = (now or datetime.now(JST)).astimezone(JST)
    start = n.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


class WorkspaceCalendar:
    """Calendar API 実装（本人トークン）。読み取りのみ。"""

    def __init__(self, token: OAuthToken, *, service: Any = None) -> None:
        self._token = token
        self._service = service

    def _svc(self) -> Any:
        if self._service is None:
            from googleapiclient.discovery import build  # 遅延 import

            from aiia.auth.google_creds import build_user_credentials

            self._service = build(
                "calendar", "v3",
                credentials=build_user_credentials(self._token),
                cache_discovery=False,
            )
        return self._service

    def list_today_events(self, now: Optional[datetime] = None) -> list[CalendarEvent]:
        time_min, time_max = today_bounds(now)
        resp = (
            self._svc().events()
            .list(
                calendarId="primary", timeMin=time_min, timeMax=time_max,
                singleEvents=True, orderBy="startTime", maxResults=50,
            )
            .execute()
        )
        out: list[CalendarEvent] = []
        for ev in resp.get("items", []):
            if ev.get("status") == "cancelled":
                continue
            ce = parse_event(ev)
            if ce.response_status == "declined":  # 辞退済みは出さない
                continue
            out.append(ce)
        return out
