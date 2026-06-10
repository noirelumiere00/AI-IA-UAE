"""Slack未返信メンションリマインドの純粋ロジック（fake slack_user・課金ゼロ）。

誤検知最小化(fail-closed)・除外条件・thread_id名前空間・reply_url=permalink・
email経路がslackキーをskipすることを検証。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from aiia import reminder
from aiia.reminder import compute_slack_reminders
from aiia.schemas import Category
from aiia.state.reminder_store import InMemoryReminderStore, ReminderRecord

_UID = "U_ME"
_NOW = datetime(2026, 6, 10, 3, 0, tzinfo=timezone.utc)  # JST 2026-06-10(水) 12:00


def _mention(**kw) -> dict:
    d = {
        "channel_id": "C1",
        "channel_name": "general",
        "is_im": False,
        "ts": str(_NOW.timestamp() - 86400),  # 1日前（lookback内）
        "thread_ts": "",
        "text": f"<@{_UID}> 確認お願いします",
        "permalink": "https://slack/p1",
        "user": "U_OTHER",
        "username": "alice",
    }
    d.update(kw)
    if not d["thread_ts"]:
        d["thread_ts"] = d["ts"]
    return d


class FakeSlackUser:
    def __init__(self, mentions: list[dict], replied: Optional[dict] = None) -> None:
        self._mentions = mentions
        self._replied = replied or {}

    def search_mentions(self, uid: str, *, count: int = 100) -> list[dict]:
        return self._mentions

    def has_user_replied_after(self, channel, thread_ts, uid, after_ts) -> Optional[bool]:
        return self._replied.get((channel, thread_ts))  # 既定 None＝判定不能(fail-closed)


def _seed_aged(store, key: str, *, biz_days_ago_from: datetime, status: str = "active") -> None:
    """first_seen を月曜(=2営業日前)に置いた active レコードを仕込む（show 条件を満たすため）。"""
    first = datetime(2026, 6, 8, 0, 0, tzinfo=timezone.utc)  # JST 月曜
    rec = ReminderRecord("me@x.com", key, Category.CLIENT_NORMAL.value, first_seen=first)
    rec.status = status
    store.upsert(rec)


def test_aged_unreplied_mention_shows() -> None:
    store = InMemoryReminderStore()
    m = _mention()
    key = f"slack:C1:{m['ts']}"
    _seed_aged(store, key, biz_days_ago_from=_NOW)
    su = FakeSlackUser([m], replied={("C1", m["thread_ts"]): False})
    views = compute_slack_reminders(store, su, "me@x.com", _UID, _NOW, write=True)
    assert len(views) == 1
    v = views[0]
    assert v.source == "slack" and v.thread_id == key
    assert v.reply_url == "https://slack/p1" and v.permalink == "https://slack/p1"
    assert v.subject == "#general" and v.sender == "@alice"
    assert v.business_days >= 1  # 2営業日放置


def test_fresh_mention_waits_not_shown() -> None:
    store = InMemoryReminderStore()
    su = FakeSlackUser([_mention()], replied={("C1", _mention()["thread_ts"]): False})
    views = compute_slack_reminders(store, su, "me@x.com", _UID, _NOW, write=True)
    assert views == []  # 当日トラック＝0営業日＜閾値1


def test_replied_excluded() -> None:
    store = InMemoryReminderStore()
    m = _mention()
    _seed_aged(store, f"slack:C1:{m['ts']}", biz_days_ago_from=_NOW)
    su = FakeSlackUser([m], replied={("C1", m["thread_ts"]): True})
    assert compute_slack_reminders(store, su, "me@x.com", _UID, _NOW) == []


def test_unknown_reply_is_failclosed() -> None:
    store = InMemoryReminderStore()
    m = _mention()
    _seed_aged(store, f"slack:C1:{m['ts']}", biz_days_ago_from=_NOW)
    su = FakeSlackUser([m], replied={})  # None＝判定不能
    assert compute_slack_reminders(store, su, "me@x.com", _UID, _NOW) == []


def test_self_authored_excluded() -> None:
    store = InMemoryReminderStore()
    m = _mention(user=_UID)
    _seed_aged(store, f"slack:C1:{m['ts']}", biz_days_ago_from=_NOW)
    su = FakeSlackUser([m], replied={("C1", m["thread_ts"]): False})
    assert compute_slack_reminders(store, su, "me@x.com", _UID, _NOW) == []


def test_broadcast_only_excluded() -> None:
    store = InMemoryReminderStore()
    m = _mention(text="<!channel> 全員へ連絡")  # 個人宛<@UID>を含まない
    _seed_aged(store, f"slack:C1:{m['ts']}", biz_days_ago_from=_NOW)
    su = FakeSlackUser([m], replied={("C1", m["thread_ts"]): False})
    assert compute_slack_reminders(store, su, "me@x.com", _UID, _NOW) == []


def test_im_excluded() -> None:
    store = InMemoryReminderStore()
    m = _mention(is_im=True)
    _seed_aged(store, f"slack:C1:{m['ts']}", biz_days_ago_from=_NOW)
    su = FakeSlackUser([m], replied={("C1", m["thread_ts"]): False})
    assert compute_slack_reminders(store, su, "me@x.com", _UID, _NOW) == []


def test_lookback_excluded() -> None:
    store = InMemoryReminderStore()
    old = str(_NOW.timestamp() - 30 * 86400)  # 30日前
    m = _mention(ts=old)
    _seed_aged(store, f"slack:C1:{old}", biz_days_ago_from=_NOW)
    su = FakeSlackUser([m], replied={("C1", old): False})
    assert compute_slack_reminders(store, su, "me@x.com", _UID, _NOW, lookback_days=14) == []


def test_dismissed_record_not_shown() -> None:
    store = InMemoryReminderStore()
    m = _mention()
    _seed_aged(store, f"slack:C1:{m['ts']}", biz_days_ago_from=_NOW, status="dismissed")
    su = FakeSlackUser([m], replied={("C1", m["thread_ts"]): False})
    assert compute_slack_reminders(store, su, "me@x.com", _UID, _NOW) == []


def test_email_compute_skips_slack_keys() -> None:
    """email の compute_reminders が slack キーを gmail.get_thread に投げないこと。"""
    store = InMemoryReminderStore()
    _seed_aged(store, "slack:C1:111.222", biz_days_ago_from=_NOW)

    class BoomGmail:
        def get_thread(self, tid):  # 呼ばれたら失敗＝slackキーが漏れた証拠
            raise AssertionError(f"gmail.get_thread should not be called for {tid}")

    views = reminder.compute_reminders(
        store,
        gmail=BoomGmail(),
        user_email="me@x.com",
        today_items=[],
        threads_by_id={},
        now=_NOW,
        write=False,
    )
    assert views == []  # slackキーはskip・例外も出ない
