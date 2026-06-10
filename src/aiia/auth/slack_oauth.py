"""Slack ユーザー認可(xoxp)フロー。Google の OAuthConsentFlow と同型・connect-web で再利用。

Slack OAuth v2: authorize は **user_scope**（bot scope ではない）指定が肝。code 交換は
`oauth.v2.access` → `authed_user.{id, access_token(xoxp), scope}`。本人メールは **bot token で
`users.info(id).profile.email`** を解決し OAuthToken.email に入れる＝既存 process_callback
（universal時 token.email でドメイン弾き→保存）をそのまま再利用できる。

xoxp は機微シークレット：ログ/例外に出さない。各人の xoxp は KMS 暗号化・per-user 束縛で保管。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from aiia.auth.oauth_flow import make_universal_state
from aiia.auth.token_store import OAuthToken

# ユーザー選択：公開+非公開ch+グループDM のメンション返信を検知するための最小 user_scope。
# search:read=検索, *_history=conversations.replies で「自分が返信したか」を読むため。
DEFAULT_USER_SCOPES: tuple[str, ...] = (
    "search:read",
    "channels:history",
    "groups:history",
    "mpim:history",
)
_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"


def slack_client_id_secret() -> tuple[Optional[str], Optional[str]]:
    return os.environ.get("SLACK_CLIENT_ID"), os.environ.get("SLACK_CLIENT_SECRET")


class SlackOAuthConsentFlow:
    """Slack OAuth v2 の薄いラッパ（同意URL生成 + code交換 + email解決）。"""

    def __init__(
        self,
        redirect_uri: str,
        user_scopes: tuple[str, ...] = DEFAULT_USER_SCOPES,
        *,
        client: Any = None,
        email_resolver: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self._redirect_uri = redirect_uri
        self._user_scopes = user_scopes
        self._client = client  # oauth.v2.access 用 WebClient（テストで注入）
        self._email_resolver = email_resolver  # uid->email（テストで注入・本番は bot users.info）

    def _wc(self) -> Any:
        if self._client is None:
            from slack_sdk import WebClient

            self._client = WebClient()  # oauth.v2.access は token 不要（client_id/secret で認証）
        return self._client

    def authorization_url_universal(self) -> tuple[str, str]:
        """共通1リンク：各人タップ→自分のSlackを user_scope で認可（本人は users.info で確定）。"""
        cid, _ = slack_client_id_secret()
        if not cid:
            raise ValueError("SLACK_CLIENT_ID が未設定です")
        state = make_universal_state()
        params = urlencode(
            {
                "client_id": cid,
                "user_scope": ",".join(self._user_scopes),
                "redirect_uri": self._redirect_uri,
                "state": state,
            }
        )
        return f"{_AUTHORIZE_URL}?{params}", state

    def _resolve_email(self, slack_user_id: str) -> Optional[str]:
        if self._email_resolver is not None:
            return self._email_resolver(slack_user_id)
        bot = os.environ.get("SLACK_BOT_TOKEN")
        if not bot:
            return None
        try:
            from slack_sdk import WebClient

            r = WebClient(token=bot).users_info(user=slack_user_id)
        except Exception:  # noqa: BLE001 — email解決失敗は連携失敗として上位で弾く
            return None
        if not r.get("ok"):
            return None
        user: dict[str, Any] = r.get("user") or {}
        prof: dict[str, Any] = user.get("profile") or {}
        email = prof.get("email")
        return str(email) if email else None

    def exchange(self, code: str) -> OAuthToken:
        cid, sec = slack_client_id_secret()
        if not (cid and sec):
            raise ValueError("SLACK_CLIENT_ID/SLACK_CLIENT_SECRET が未設定です")
        resp = self._wc().oauth_v2_access(
            client_id=cid, client_secret=sec, code=code, redirect_uri=self._redirect_uri
        )
        if not resp.get("ok"):
            raise ValueError("oauth.v2.access に失敗しました")
        au = resp.get("authed_user", {}) or {}
        xoxp = au.get("access_token")
        if not xoxp:
            raise ValueError("user access_token を取得できません（user_scope 認可がない）")
        scope = au.get("scope", "") or ""
        email = self._resolve_email(au.get("id", "") or "")
        return OAuthToken(
            refresh_token=str(xoxp),
            scopes=tuple(s for s in scope.split(",") if s),
            email=email,  # universal 経路で本人特定（process_callback が使う）
        )
