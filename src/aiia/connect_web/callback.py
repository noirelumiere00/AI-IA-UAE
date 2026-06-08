"""連携 callback の純粋ロジック（FastAPI 非依存・課金ゼロでテスト可能）。

Google の redirect(?code=&state=) を受け、state署名で本人を検証→code交換→トークン保管。
失敗（キャンセル/改竄/交換失敗/refresh無し/保存失敗）は全て安全に弾く（保存しない）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from aiia.auth.oauth_flow import verify_state
from aiia.auth.token_store import OAuthToken, TokenStore


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
) -> CallbackResult:
    if error:
        return CallbackResult(False, "連携がキャンセルされました", str(error))
    if not code or not state:
        return CallbackResult(False, "不正なリクエスト", "code または state がありません")
    email = verify_state(state, secret=state_secret)
    if not email:
        return CallbackResult(False, "検証に失敗しました", "state が不正です（リンクを取り直してください）")
    try:
        token = exchange(code)
    except Exception as exc:  # 交換失敗は保存しない
        return CallbackResult(False, "連携に失敗しました", type(exc).__name__, email)
    if not isinstance(token, OAuthToken) or not token.refresh_token:
        return CallbackResult(False, "連携に失敗しました", "refresh_token を取得できませんでした", email)
    try:
        store.put(email, token)
    except Exception as exc:
        return CallbackResult(False, "保存に失敗しました", type(exc).__name__, email)
    return CallbackResult(
        True, "✅ 連携が完了しました", f"{email} のGoogleを連携しました。このタブは閉じてOKです。", email
    )
