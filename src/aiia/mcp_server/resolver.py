"""§Q per-user 本人解決：Slack user_id → 本人email を **サーバ側で確定**（外殻申告は不信）。

OpenClaw(外殻)は `_user_context.slack_user_id` のみ渡す。email は外殻が言う値を**一切採らず**、
bot token の `users.info(user_id).profile.email` でサーバが確定する＝なりすまし防止の核。
（teamagent `identity.py` / AiLa `auth/slack_oauth._resolve_email` と同型）。

解決不能・例外・外部/ゲスト（email欠落）・bot/削除済みは **None（fail-closed）**。
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# slack_user_id の形式（U/W始まり英数字）。詐称・空・不正は弾く。
_SLACK_UID_RE = re.compile(r"^[UW][A-Z0-9]+$")

# slack_user_id → email を返す解決器（テストで Fake 注入）。
EmailResolver = Callable[[str], Optional[str]]


def normalize_email(raw: Optional[str]) -> Optional[str]:
    """`strip().lower()` ＋形式検証。`'unknown'`/空白/`@`無し/制御字は None。"""
    if not raw:
        return None
    e = raw.strip().lower()
    if not e or e == "unknown" or not _EMAIL_RE.match(e):
        return None
    return e


def slack_resolver_from_bot_token(
    *, bot_token: Optional[str] = None, client: Any = None
) -> EmailResolver:
    """bot token で `users.info` を叩く本番 resolver を構築（`SLACK_BOT_TOKEN` 既定）。

    外部社/ゲスト/別 workspace/bot/削除済みは email を採らず None（fail-closed）。
    """
    tok = bot_token or os.environ.get("SLACK_BOT_TOKEN")

    def _resolve(slack_user_id: str) -> Optional[str]:
        if not slack_user_id or not _SLACK_UID_RE.match(slack_user_id):
            return None
        wc = client
        if wc is None:
            if not tok:
                return None
            from slack_sdk import WebClient

            wc = WebClient(token=tok)
        try:
            r = wc.users_info(user=slack_user_id)
        except Exception:  # noqa: BLE001 — 解決失敗は本人未特定として弾く
            return None
        if not r.get("ok"):
            return None
        user: dict[str, Any] = r.get("user") or {}
        if user.get("is_bot") or user.get("deleted") or user.get("is_stranger"):
            return None
        if user.get("is_restricted") or user.get("is_ultra_restricted"):
            return None  # ゲスト/制限ユーザーは対象外
        prof: dict[str, Any] = user.get("profile") or {}
        return normalize_email(prof.get("email"))

    return _resolve


def resolve_requester_email(
    user_context: Optional[dict[str, Any]], *, resolver: EmailResolver
) -> Optional[str]:
    """`_user_context` から **slack_user_id だけ**を取り、resolver で本人emailを確定。

    外殻申告の `user_email` 等は**読まない**（STRICT）。slack_user_id 欠落/不正→None（fail-closed）。
    """
    if not isinstance(user_context, dict):
        return None
    uid = user_context.get("slack_user_id")
    if not isinstance(uid, str) or not uid:
        return None
    try:
        return resolver(uid)
    except Exception:  # noqa: BLE001 — resolver 例外も fail-closed（外殻申告にフォールバックしない）
        return None
