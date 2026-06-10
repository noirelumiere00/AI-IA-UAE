"""連携 callback の純粋ロジック（FastAPI 非依存・課金ゼロでテスト可能）。

Google の redirect(?code=&state=) を受け、state署名で本人を検証→code交換→トークン保管。
失敗（キャンセル/改竄/交換失敗/refresh無し/保存失敗）は全て安全に弾く（保存しない）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from aiia.auth.oauth_flow import is_universal_email, verify_reply_token, verify_state
from aiia.auth.token_store import OAuthToken, TokenStore


def _allowed_connect_domains() -> set[str]:
    """共通リンクで連携を許すメールドメイン集合。

    AIIA_CONNECT_ALLOWED_DOMAINS（カンマ/空白区切り）優先、無ければ AIIA_DEFAULT_INTERNAL_DOMAIN。
    どちらも未設定なら空集合＝制限なし（PoC）。本番は必ず設定する（公開URLの悪用防止）。
    """
    raw = os.environ.get("AIIA_CONNECT_ALLOWED_DOMAINS") or os.environ.get(
        "AIIA_DEFAULT_INTERNAL_DOMAIN", ""
    )
    return {d.strip().lower() for d in raw.replace(",", " ").split() if d.strip()}


@dataclass
class CallbackResult:
    ok: bool
    title: str
    detail: str
    email: Optional[str] = None


def process_callback(
    *,
    code: Optional[str],
    state: Optional[str],
    error: Optional[str],
    store: TokenStore,
    exchange: Callable[[str], Any],
    state_secret: Optional[bytes] = None,
    allowed_domains: Optional[set[str]] = None,
) -> CallbackResult:
    if error:
        return CallbackResult(False, "連携がキャンセルされました", str(error))
    if not code or not state:
        return CallbackResult(False, "不正なリクエスト", "code または state がありません")
    state_email = verify_state(state, secret=state_secret)
    if not state_email:
        return CallbackResult(
            False, "検証に失敗しました", "state が不正です（リンクを取り直してください）"
        )
    universal = is_universal_email(state_email)
    try:
        token = exchange(code)
    except Exception as exc:  # 交換失敗は保存しない
        return CallbackResult(False, "連携に失敗しました", type(exc).__name__)
    if not isinstance(token, OAuthToken) or not token.refresh_token:
        return CallbackResult(False, "連携に失敗しました", "refresh_token を取得できませんでした")
    # 本人メールの確定：共通リンクは Google ログイン結果(token.email)、個別リンクは state から。
    if universal:
        email = (token.email or "").strip().lower()
        if not email:
            return CallbackResult(
                False,
                "連携に失敗しました",
                "Googleアカウントのメールを取得できませんでした（再試行してください）",
            )
        allowed = allowed_domains if allowed_domains is not None else _allowed_connect_domains()
        if allowed and email.rsplit("@", 1)[-1].lower() not in allowed:
            return CallbackResult(
                False,
                "このアカウントは対象外です",
                f"{email} は連携を許可されたドメインではありません（社用アカウントでお試しください）",
                email,
            )
    else:
        email = state_email
    try:
        store.put(email, token)
    except Exception as exc:
        return CallbackResult(False, "保存に失敗しました", type(exc).__name__, email)
    return CallbackResult(
        True,
        "✅ 連携が完了しました",
        f"{email} のGoogleを連携しました。このタブは閉じてOKです。",
        email,
    )


@dataclass
class ReplyResult:
    ok: bool
    thread_id: Optional[str] = None
    email: Optional[str] = None
    detail: str = ""


def process_reply(
    *,
    s: Optional[str],
    reply_factory: Callable[[str, str], dict],
    state_secret: Optional[bytes] = None,
) -> ReplyResult:
    """[対応する]url-button の `/reply?s=` を処理：署名検証→本人の全返信下書きを作成。
    戻り値の thread_id でGmailへリダイレクトするのはWeb層。下書き作成失敗でも thread_id は返す
    （会話だけは開く=fail-safe）。"""
    if not s:
        return ReplyResult(False, detail="s がありません")
    parsed = verify_reply_token(s, secret=state_secret)
    if parsed is None:
        return ReplyResult(False, detail="署名が不正です（リンクを取り直してください）")
    email, thread_id = parsed
    try:
        reply_factory(email, thread_id)  # 全返信HTML+署名の下書きを作成
    except Exception as exc:  # noqa: BLE001 — 作成失敗でも会話は開く（リダイレクトは続行）
        return ReplyResult(
            True, thread_id=thread_id, email=email, detail=f"draft_failed:{type(exc).__name__}"
        )
    return ReplyResult(True, thread_id=thread_id, email=email)
