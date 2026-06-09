"""リマインド×下書きの**多段操作シーケンス**を網羅（状態不整合・無反応の検出）。

ハンドラを直接順に呼ぶ harness（fake注入・課金ゼロ）。特に reply→delete→dismiss/snooze など
「下書きとリマインドが独立に正しく動くか」「二度押し/誤解除/undo」を検証する。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from aiia import reminder
from aiia.auth.token_store import InMemoryTokenStore, OAuthToken
from aiia.mcp.workspace_gmail import WorkspaceGmailSender
from aiia.runtime.slack_handlers import (
    HandlerDeps,
    handle_delete,
    handle_remind_dismiss,
    handle_remind_mute,
    handle_remind_reply,
    handle_remind_snooze,
    handle_remind_undo,
    handle_send_submit,
)
from aiia.safety.hitl import AutoSendError, SendConfirmationGate
from aiia.state.reminder_store import InMemoryReminderStore

EMAIL = "alice@x.com"
UID = "U1"
TID = "t1"


def _now() -> datetime:
    return datetime(2026, 6, 9, 9, tzinfo=timezone.utc)


class FakeSenderSvc:
    """Gmail drafts API のfake。create/update/delete/send を記録し、存在する下書きを追跡。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.existing: set[str] = set()

    def users(self) -> Any:
        svc = self

        class _D:
            def get(self, **kw: Any) -> Any:
                return type("E", (), {"execute": lambda s: {"id": kw["id"], "message": {"threadId": TID}}})()

            def update(self, **kw: Any) -> Any:
                svc.calls.append(("update", kw["id"]))
                return type("E", (), {"execute": lambda s: {"id": kw["id"]}})()

            def delete(self, **kw: Any) -> Any:
                svc.calls.append(("delete", kw["id"]))
                svc.existing.discard(kw["id"])
                return type("E", (), {"execute": lambda s: {}})()

            def send(self, **kw: Any) -> Any:
                svc.calls.append(("send",))
                return type("E", (), {"execute": lambda s: {"id": "sent_1"}})()

        class _U:
            def drafts(self) -> Any:
                return _D()

        return _U()


def _deps(nonce: str = "n") -> tuple[HandlerDeps, InMemoryReminderStore, FakeSenderSvc]:
    rstore = InMemoryReminderStore()
    reminder.track(rstore, EMAIL, TID, "CLIENT_NORMAL", _now())
    svc = FakeSenderSvc()

    def factory(email: str, thread_id: str) -> dict:
        did = "d_" + thread_id
        svc.existing.add(did)
        return {"draft_id": did, "subject": "Re: 件名", "to": "x@x", "body": "本文"}

    deps = HandlerDeps(
        store=InMemoryTokenStore({EMAIL: OAuthToken("1//a")}),
        gate=SendConfirmationGate(),
        email_for_slack_user=lambda u: EMAIL,
        sender_factory=lambda t: WorkspaceGmailSender(t, service=svc),
        now=lambda: 100.0,
        nonce=lambda: nonce,
        reminder_store=rstore,
        reply_draft_factory=factory,
    )
    return deps, rstore, svc


def _status(rstore: InMemoryReminderStore) -> str:
    r = rstore.get(EMAIL, TID)
    return r.status if r else "(none)"


# ── 疑われた経路：対応する → 削除 → 対応済み/後で が壊れないか ───────────────────
def test_reply_then_delete_then_dismiss() -> None:
    deps, rstore, svc = _deps()
    out = handle_remind_reply(deps, slack_user_id=UID, thread_id=TID)
    assert out["draft_id"] == "d_t1" and "d_t1" in svc.existing
    handle_delete(deps, slack_user_id=UID, draft_id="d_t1")
    assert "d_t1" not in svc.existing and ("delete", "d_t1") in svc.calls
    msg = handle_remind_dismiss(deps, slack_user_id=UID, thread_id=TID)  # ← ここが無反応にならないか
    assert "対応済み" in msg and _status(rstore) == "dismissed"


def test_reply_then_delete_then_snooze() -> None:
    deps, rstore, _ = _deps()
    handle_remind_reply(deps, slack_user_id=UID, thread_id=TID)
    handle_delete(deps, slack_user_id=UID, draft_id="d_t1")
    msg = handle_remind_snooze(deps, slack_user_id=UID, thread_id=TID)
    r = rstore.get(EMAIL, TID)
    assert "後" in msg or "再通知" in msg
    assert r.snooze_count == 1 and r.status == "active"


# ── 解除/undo/二度押し ────────────────────────────────────────────────────────
def test_dismiss_then_undo_then_dismiss_again() -> None:
    deps, rstore, _ = _deps()
    handle_remind_dismiss(deps, slack_user_id=UID, thread_id=TID)
    assert _status(rstore) == "dismissed"
    handle_remind_undo(deps, slack_user_id=UID, thread_id=TID)
    assert _status(rstore) == "active"
    handle_remind_dismiss(deps, slack_user_id=UID, thread_id=TID)
    assert _status(rstore) == "dismissed"  # 再解除できる


def test_double_dismiss_idempotent() -> None:
    deps, rstore, _ = _deps()
    handle_remind_dismiss(deps, slack_user_id=UID, thread_id=TID)
    handle_remind_dismiss(deps, slack_user_id=UID, thread_id=TID)  # 二度押しでも壊れない
    assert _status(rstore) == "dismissed"


def test_mute_then_undo() -> None:
    deps, rstore, _ = _deps()
    handle_remind_mute(deps, slack_user_id=UID, thread_id=TID)
    assert _status(rstore) == "muted"
    handle_remind_undo(deps, slack_user_id=UID, thread_id=TID)
    assert _status(rstore) == "active"


def test_snooze_then_dismiss_then_undo() -> None:
    deps, rstore, _ = _deps()
    handle_remind_snooze(deps, slack_user_id=UID, thread_id=TID)
    handle_remind_dismiss(deps, slack_user_id=UID, thread_id=TID)
    assert _status(rstore) == "dismissed"
    handle_remind_undo(deps, slack_user_id=UID, thread_id=TID)
    r = rstore.get(EMAIL, TID)
    assert r.status == "active" and r.snooze_until is None  # undoでスヌーズも解除


# ── dismiss 後でも 対応する は動く（独立性）────────────────────────────────────
def test_dismiss_then_reply_still_creates_draft() -> None:
    deps, rstore, svc = _deps()
    handle_remind_dismiss(deps, slack_user_id=UID, thread_id=TID)
    out = handle_remind_reply(deps, slack_user_id=UID, thread_id=TID)  # 解除済みでも下書きは作れる
    assert out["draft_id"] == "d_t1" and "d_t1" in svc.existing


# ── reply → send（2段）→ 二度押し拒否 ────────────────────────────────────────
def test_reply_then_send_then_double_send_blocked() -> None:
    deps, _, svc = _deps()
    handle_remind_reply(deps, slack_user_id=UID, thread_id=TID)
    msg = handle_send_submit(deps, slack_user_id=UID, draft_id="d_t1")
    assert "送信" in msg and ("send",) in svc.calls
    with pytest.raises(AutoSendError):
        handle_send_submit(deps, slack_user_id=UID, draft_id="d_t1")  # 同nonce二度押し→拒否
    assert sum(1 for c in svc.calls if c[0] == "send") == 1  # 送信は1回だけ


# ── メール解決不可（権限不足）は安全に弾く ───────────────────────────────────
def test_remind_action_blocked_when_email_unresolved() -> None:
    deps, _, _ = _deps()
    deps.email_for_slack_user = lambda u: None  # users:read.email 不可等
    for fn in (handle_remind_dismiss, handle_remind_snooze, handle_remind_undo):
        with pytest.raises(PermissionError):
            fn(deps, slack_user_id=UID, thread_id=TID)


# ── decide ベースの「期限切れ自動解除」シナリオ ────────────────────────────────
def test_expired_after_14_business_days_in_compute() -> None:
    from datetime import timedelta
    rstore = InMemoryReminderStore()
    old = _now() - timedelta(days=30)
    from aiia.state.reminder_store import ReminderRecord
    rstore.upsert(ReminderRecord(EMAIL, TID, "CLIENT_NORMAL", first_seen=old))

    class _G:
        def get_thread(self, tid: str) -> Any:
            from aiia.schemas import EmailMessage, EmailThread
            return EmailThread(thread_id=tid, subject="s",
                               messages=[EmailMessage(message_id="m", sender="x@y")])  # 未返信

    views = reminder.compute_reminders(rstore, _G(), EMAIL, [], {}, _now(), write=True)
    assert views == [] and rstore.get(EMAIL, TID).status == "dismissed"  # 14営業日超で自動解除
