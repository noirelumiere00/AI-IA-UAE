"""Slack 対話ハンドラの中核ロジック（slack_bolt 非依存・課金ゼロでテスト可能）。

編集/削除/送信(2段確認) を純粋関数化。`slack_app.py` が slack_bolt の @app.action/@app.view に配線する。
- 未連携ユーザーは PermissionError（送信は当然しない）。
- 送信は SendConfirmationGate を必ず通る（mint→authorize→send）。確認生成が無い経路では送れない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

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
