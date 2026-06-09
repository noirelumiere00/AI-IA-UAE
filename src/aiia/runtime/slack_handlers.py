"""Slack 対話ハンドラの中核ロジック（slack_bolt 非依存・課金ゼロでテスト可能）。

編集/削除/送信(2段確認) を純粋関数化。`slack_app.py` が slack_bolt の @app.action/@app.view に配線する。
- 未連携ユーザーは PermissionError（送信は当然しない）。
- 送信は SendConfirmationGate を必ず通る（mint→authorize→send）。確認生成が無い経路では送れない。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from aiia import reminder as _reminder
from aiia.auth.token_store import OAuthToken, TokenStore
from aiia.mcp.workspace_gmail import WorkspaceGmailSender
from aiia.safety.hitl import SendConfirmationGate


@dataclass
class HandlerDeps:
    store: TokenStore
    gate: SendConfirmationGate
    email_for_slack_user: Callable[[str], Optional[str]]  # slack user_id → email
    sender_factory: Callable[[OAuthToken], WorkspaceGmailSender]  # token → 送信器
    now: Callable[[], float]
    nonce: Callable[[], str]
    audit: Any = None  # AuditLog | None
    reminder_store: Any = None  # ReminderStore | None（リマインド操作用）
    # (email, thread_id) → {draft_id, subject, to, body}：返信下書きを生成しGmailに作成。
    reply_draft_factory: Optional[Callable[[str, str], dict]] = None


def _resolve(deps: HandlerDeps, slack_user_id: str) -> tuple[str, OAuthToken]:
    email = deps.email_for_slack_user(slack_user_id)
    if not email:
        raise PermissionError("Slackユーザーのメール解決に失敗（users:read.email 権限が必要）")
    token = deps.store.get(email)
    if token is None:
        raise PermissionError(f"{email} は未連携です（/connect で連携してください）")
    return email, token


def handle_delete(deps: HandlerDeps, *, slack_user_id: str, draft_id: str) -> str:
    _, token = _resolve(deps, slack_user_id)
    deps.sender_factory(token).delete_draft(draft_id)
    if deps.audit:
        deps.audit.record("draft_deleted", draft_id=draft_id)
    return "🗑 下書きを削除しました。"


def handle_edit_submit(
    deps: HandlerDeps, *, slack_user_id: str, draft_id: str, thread_id: str, subject: str, body: str
) -> str:
    _, token = _resolve(deps, slack_user_id)
    deps.sender_factory(token).update_draft(
        draft_id=draft_id, thread_id=thread_id, subject=subject, body=body
    )
    if deps.audit:
        deps.audit.record("draft_edited", draft_id=draft_id)
    return "📝 下書きを更新しました。"


def handle_send_submit(deps: HandlerDeps, *, slack_user_id: str, draft_id: str) -> str:
    """2段目モーダル submit で呼ばれる。ゲートを通った場合のみ drafts.send。"""
    email, token = _resolve(deps, slack_user_id)
    now = deps.now()
    conf = deps.gate.mint(user_id=email, draft_id=draft_id, now=now, nonce=deps.nonce())
    deps.gate.authorize_send(conf, user_id=email, draft_id=draft_id, now=now)  # 無ければ AutoSendError
    message_id = deps.sender_factory(token).send_draft(draft_id)
    if deps.audit:
        deps.audit.record("mail_sent", draft_id=draft_id, message_id=message_id)
    return f"📤 送信しました（{message_id}）。"


# ── 返信リマインドの操作（state は論理削除＝undo可。解除に confirm を付けない）──────
def _remind_email(deps: HandlerDeps, slack_user_id: str) -> str:
    email = deps.email_for_slack_user(slack_user_id)
    if not email:
        raise PermissionError("Slackユーザーのメール解決に失敗（users:read.email 権限が必要）")
    if deps.reminder_store is None:
        raise RuntimeError("reminder_store 未設定")
    return email


def handle_remind_dismiss(deps: HandlerDeps, *, slack_user_id: str, thread_id: str) -> str:
    email = _remind_email(deps, slack_user_id)
    rec = deps.reminder_store.get(email, thread_id)
    _reminder.dismiss(deps.reminder_store, email, thread_id, datetime.now(timezone.utc))
    # 編集だけして送信/削除しなかった孤児下書きを片付ける（最終アクション=対応済みを実体に反映）
    if rec is not None and getattr(rec, "reply_draft_id", None):
        try:
            token = deps.store.get(email)
            if token:
                deps.sender_factory(token).delete_draft(rec.reply_draft_id)
        except Exception:  # noqa: BLE001 — 下書き削除失敗で解除は止めない
            pass
    if deps.audit:
        deps.audit.record("remind_dismissed", thread_id=thread_id)
    return "✅ 対応済みにしました（誤りなら↩取り消すで戻せます）。"


def handle_remind_mute(deps: HandlerDeps, *, slack_user_id: str, thread_id: str) -> str:
    email = _remind_email(deps, slack_user_id)
    _reminder.dismiss(deps.reminder_store, email, thread_id, datetime.now(timezone.utc), mute=True)
    return "🔕 このスレッドは今後通知しません（↩取り消すで戻せます）。"


def handle_remind_snooze(deps: HandlerDeps, *, slack_user_id: str, thread_id: str, days: int = 3) -> str:
    email = _remind_email(deps, slack_user_id)
    _reminder.snooze(deps.reminder_store, email, thread_id, datetime.now(timezone.utc), days=days)
    return f"⏰ {days}日後に再通知します。"


def handle_remind_undo(deps: HandlerDeps, *, slack_user_id: str, thread_id: str) -> str:
    email = _remind_email(deps, slack_user_id)
    _reminder.undo(deps.reminder_store, email, thread_id, datetime.now(timezone.utc))
    return "↩ 取り消しました（リマインドを再開します）。"


def handle_remind_reply(deps: HandlerDeps, *, slack_user_id: str, thread_id: str) -> dict:
    """『対応する』＝**返信下書きを生成しGmailに作成**して返す（Slackで確認→編集/送信2段へ）。
    戻り値 {draft_id, subject, to, body, gmail_link}。factory 未設定時は link のみ（後方互換）。"""
    email = _remind_email(deps, slack_user_id)
    link = _reminder.gmail_link(thread_id)
    if deps.reply_draft_factory is None:
        return {"draft_id": "", "gmail_link": link, "body": "", "subject": "", "to": ""}
    info = deps.reply_draft_factory(email, thread_id)
    # 作成した下書きIDをリマインドに記録（対応済み時に孤児を片付けられるように）
    rec = deps.reminder_store.get(email, thread_id) if deps.reminder_store else None
    if rec is not None and info.get("draft_id"):
        rec.reply_draft_id = info["draft_id"]
        deps.reminder_store.upsert(rec)
    if deps.audit:
        deps.audit.record("remind_reply_drafted", thread_id=thread_id, draft_id=info.get("draft_id", ""))
    return {**info, "gmail_link": link}
