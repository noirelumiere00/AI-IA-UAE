"""Slack 対話ハンドラの中核（編集/削除/送信2段確認）。fake注入・課金ゼロ。"""
from __future__ import annotations

from typing import Any

import pytest

from aiia.auth.token_store import InMemoryTokenStore, OAuthToken
from aiia.mcp.workspace_gmail import WorkspaceGmailSender
from aiia.runtime.slack_handlers import (
    HandlerDeps,
    handle_delete,
    handle_edit_submit,
    handle_remind_dismiss,
    handle_remind_reply,
    handle_remind_snooze,
    handle_remind_undo,
    handle_send_submit,
)
from aiia.safety.hitl import AutoSendError, SendConfirmationGate
from aiia.state.reminder_store import InMemoryReminderStore


class FakeSenderSvc:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def users(self) -> Any:
        svc = self

        class _D:
            def get(self, **kw: Any) -> Any:
                return type("E", (), {"execute": lambda s: {"id": kw["id"], "message": {"threadId": "t1"}}})()

            def update(self, **kw: Any) -> Any:
                svc.calls.append("update")
                return type("E", (), {"execute": lambda s: {"id": kw["id"]}})()

            def delete(self, **kw: Any) -> Any:
                svc.calls.append("delete")
                return type("E", (), {"execute": lambda s: {}})()

            def send(self, **kw: Any) -> Any:
                svc.calls.append("send")
                return type("E", (), {"execute": lambda s: {"id": "sent_1"}})()

        class _U:
            def drafts(self) -> Any:
                return _D()

        return _U()


def _deps(*, connected: bool = True, svc: FakeSenderSvc | None = None) -> tuple[HandlerDeps, FakeSenderSvc]:
    svc = svc or FakeSenderSvc()
    store = InMemoryTokenStore({"alice@x.com": OAuthToken("1//a")} if connected else None)
    deps = HandlerDeps(
        store=store,
        gate=SendConfirmationGate(),
        email_for_slack_user=lambda u: "alice@x.com",
        sender_factory=lambda t: WorkspaceGmailSender(t, service=svc),
        now=lambda: 100.0,
        nonce=lambda: "nonce-1",
    )
    return deps, svc


def test_delete_calls_delete_draft() -> None:
    deps, svc = _deps()
    msg = handle_delete(deps, slack_user_id="U1", draft_id="d1")
    assert "削除" in msg and svc.calls == ["delete"]


def test_edit_calls_update_draft() -> None:
    deps, svc = _deps()
    msg = handle_edit_submit(
        deps, slack_user_id="U1", draft_id="d1", thread_id="t1", subject="Re: x", body="新本文"
    )
    assert "更新" in msg and svc.calls == ["update"]


def test_send_goes_through_gate_and_sends() -> None:
    deps, svc = _deps()
    msg = handle_send_submit(deps, slack_user_id="U1", draft_id="d1")
    assert "送信" in msg and "sent_1" in msg and svc.calls == ["send"]


def test_unconnected_user_cannot_send() -> None:
    deps, svc = _deps(connected=False)
    with pytest.raises(PermissionError):
        handle_send_submit(deps, slack_user_id="U1", draft_id="d1")
    assert svc.calls == []  # 未連携→送信されない


def test_send_nonce_single_use_blocks_double() -> None:
    # 同一 nonce を返す deps で2回送信 → 2回目はゲートが単回で弾く
    deps, svc = _deps()
    handle_send_submit(deps, slack_user_id="U1", draft_id="d1")
    with pytest.raises(AutoSendError):
        handle_send_submit(deps, slack_user_id="U1", draft_id="d1")  # nonce 使い回し→拒否
    assert svc.calls == ["send"]  # 送信は1回だけ


def _remind_deps() -> tuple[HandlerDeps, InMemoryReminderStore]:
    from aiia import reminder
    from datetime import datetime, timezone
    rstore = InMemoryReminderStore()
    reminder.track(rstore, "alice@x.com", "t1", "CLIENT_NORMAL", datetime.now(timezone.utc))
    deps, _ = _deps()
    deps.reminder_store = rstore
    return deps, rstore


def test_remind_dismiss_then_undo() -> None:
    deps, rstore = _remind_deps()
    assert "対応済み" in handle_remind_dismiss(deps, slack_user_id="U1", thread_id="t1")
    assert rstore.get("alice@x.com", "t1").status == "dismissed"
    assert "取り消し" in handle_remind_undo(deps, slack_user_id="U1", thread_id="t1")
    assert rstore.get("alice@x.com", "t1").status == "active"  # 誤解除をundoで復活


def test_remind_snooze_increments() -> None:
    deps, rstore = _remind_deps()
    handle_remind_snooze(deps, slack_user_id="U1", thread_id="t1")
    r = rstore.get("alice@x.com", "t1")
    assert r.snooze_count == 1 and r.snooze_until is not None


def test_remind_reply_generates_draft_via_factory() -> None:
    deps, _ = _remind_deps()
    deps.reply_draft_factory = lambda email, tid: {
        "draft_id": "d9", "subject": "Re: 見積もりの件", "to": "sato@client.co.jp", "body": "下書き本文です"}
    out = handle_remind_reply(deps, slack_user_id="U1", thread_id="t1")
    assert out["draft_id"] == "d9" and out["body"] == "下書き本文です" and "gmail_link" in out


def test_remind_reply_fallback_link_without_factory() -> None:
    deps, _ = _remind_deps()
    deps.reply_draft_factory = None
    out = handle_remind_reply(deps, slack_user_id="U1", thread_id="t1")
    assert out["draft_id"] == "" and out["gmail_link"].endswith("t1")  # 下書き生成不可時はリンク
