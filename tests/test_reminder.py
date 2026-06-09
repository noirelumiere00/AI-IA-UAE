"""返信リマインド：未返信判定(誤検知排除)・候補・営業日・decide・compute・store。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiia import reminder as R
from aiia.schemas import (
    Category, ClassificationResult, DigestItem, EmailMessage, EmailThread, ThreadSummary,
)
from aiia.state.reminder_store import InMemoryReminderStore, ReminderRecord

JST = timezone(timedelta(hours=9))


def _thread(thread_id="t", *, sender="x@a.example", labels=None, headers=None) -> EmailThread:
    return EmailThread(thread_id=thread_id, subject="件名", messages=[
        EmailMessage(message_id="m", sender=sender, subject="件名", snippet="本文の手がかり",
                     labels=labels or [], headers=headers or {})])


def _item(cat=Category.CLIENT_NORMAL, *, rk="to", actionable=True, tid="t") -> DigestItem:
    return DigestItem(thread_id=tid, category=cat, priority=30, subject="件名", sender="x@a.example",
                      summary=ThreadSummary(thread_id=tid),
                      classification=ClassificationResult(category=cat, recipient_kind=rk, is_actionable=actionable))


# ── 未返信判定（誤検知排除）─────────────────────────────────────────────────
def test_is_unreplied_true_on_incoming() -> None:
    assert R.is_unreplied(_thread()) is True


def test_is_unreplied_false_when_self_sent_last() -> None:
    assert R.is_unreplied(_thread(labels=["SENT"])) is False  # 本人が最後に送った


def test_is_unreplied_false_on_auto_submitted_and_bulk_and_noreply() -> None:
    assert R.is_unreplied(_thread(headers={"Auto-Submitted": "auto-replied"})) is False  # OOO
    assert R.is_unreplied(_thread(headers={"List-Id": "<l>"})) is False                   # メルマガ
    assert R.is_unreplied(_thread(sender="noreply@a.example")) is False                   # noreply


# ── 候補（重要×To×要返信）─────────────────────────────────────────────────
def test_candidate_only_important_to_actionable() -> None:
    assert R.is_reminder_candidate(_item()) is True
    assert R.is_reminder_candidate(_item(rk="cc")) is False           # 他人の球
    assert R.is_reminder_candidate(_item(actionable=False)) is False  # FYI
    assert R.is_reminder_candidate(_item(cat=Category.NEWSLETTER)) is False


# ── 営業日 ───────────────────────────────────────────────────────────────
def test_business_days_skip_weekend() -> None:
    fri = datetime(2026, 6, 5, 9, tzinfo=JST)   # 金
    mon = datetime(2026, 6, 8, 9, tzinfo=JST)   # 月
    assert R.business_days_between(fri, mon) == 1   # 土日を数えない
    assert R.business_days_between(fri, fri) == 0


# ── decide ─────────────────────────────────────────────────────────────────
def _rec(cat="CLIENT_NORMAL", *, first_seen, snooze_until=None, snooze_count=0) -> ReminderRecord:
    return ReminderRecord("u@x", "t", cat, first_seen=first_seen,
                          snooze_until=snooze_until, snooze_count=snooze_count)


def test_decide_show_when_over_threshold_unreplied() -> None:
    now = datetime(2026, 6, 11, 9, tzinfo=JST)            # 木
    rec = _rec(first_seen=datetime(2026, 6, 8, 9, tzinfo=JST))  # 月（3営業日経過）
    assert R.decide(rec, _thread(), now).action == "show"


def test_decide_wait_under_threshold_and_dismiss_on_reply() -> None:
    now = datetime(2026, 6, 9, 9, tzinfo=JST)
    rec = _rec(first_seen=datetime(2026, 6, 8, 9, tzinfo=JST))  # 1営業日 < 3
    assert R.decide(rec, _thread(), now).action == "wait"
    assert R.decide(rec, _thread(labels=["SENT"]), now).action == "dismiss_replied"


def test_decide_expired_after_snooze_cap() -> None:
    now = datetime(2026, 6, 12, 9, tzinfo=JST)
    rec = _rec(first_seen=datetime(2026, 6, 1, 9, tzinfo=JST), snooze_count=3)
    assert R.decide(rec, _thread(), now).action == "dismiss_expired"


# ── compute + store ──────────────────────────────────────────────────────────
def test_compute_tracks_and_shows_and_autodismiss() -> None:
    store = InMemoryReminderStore()
    now0 = datetime(2026, 6, 8, 9, tzinfo=JST)
    # day0: 重要×To×要返信を追跡（まだ閾値未満→表示なし）
    item = _item(tid="t1")
    threads = {"t1": _thread("t1")}
    v0 = R.compute_reminders(store, gmail=None, user_email="u@x", today_items=[item],
                             threads_by_id=threads, now=now0)
    assert v0 == [] and store.get("u@x", "t1").status == "active"
    # day+3営業日: まだ未返信→表示
    now3 = datetime(2026, 6, 11, 9, tzinfo=JST)
    v3 = R.compute_reminders(store, gmail=None, user_email="u@x", today_items=[],
                             threads_by_id={"t1": _thread("t1")}, now=now3)
    assert len(v3) == 1 and v3[0].thread_id == "t1" and v3[0].business_days >= 3
    # 返信済み（SENT）→ 自動解除
    R.compute_reminders(store, gmail=None, user_email="u@x", today_items=[],
                        threads_by_id={"t1": _thread("t1", labels=["SENT"])}, now=now3)
    assert store.get("u@x", "t1").status == "dismissed"


def test_compute_write_false_does_not_mutate() -> None:
    store = InMemoryReminderStore()
    now = datetime(2026, 6, 8, 9, tzinfo=JST)
    R.compute_reminders(store, gmail=None, user_email="u@x", today_items=[_item(tid="t1")],
                        threads_by_id={"t1": _thread("t1")}, now=now, write=False)
    assert store.get("u@x", "t1") is None  # dry-run は状態を変えない


def test_snooze_dismiss_undo() -> None:
    store = InMemoryReminderStore()
    now = datetime(2026, 6, 8, 9, tzinfo=JST)
    R.track(store, "u@x", "t1", "CLIENT_NORMAL", now)
    R.snooze(store, "u@x", "t1", now)
    r = store.get("u@x", "t1")
    assert r.snooze_count == 1 and r.snooze_until is not None
    R.dismiss(store, "u@x", "t1", now)
    assert store.get("u@x", "t1").status == "dismissed" and store.list_active("u@x") == []
    R.undo(store, "u@x", "t1", now)
    assert store.get("u@x", "t1").status == "active"  # 誤解除をundo
