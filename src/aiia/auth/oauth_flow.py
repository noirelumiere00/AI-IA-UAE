"""per-user OAuth 同意フロー（個別認可・`/connect` の中核）。TeamAgent google_oauth_flow.py 移植。

各社員が自分のGoogleを個別に許可する3-legged同意。state を HMAC署名して callback で
「誰の認可か」を改竄なく検証（CSRF/なりすまし対策）。google-auth-oauthlib は遅延 import。

スコープ（ユーザー確定・TeamAgent同じ）: gmail.modify(読取+下書き作成/更新/削除+送信(drafts.send)+
ラベルを1スコープでカバー) / calendar.readonly。送信は実行側で2段人間確認ゲートを通し、
送信/破壊系を toolset に出さないことで安全を担保（scopeは広いが操作はコードで封じる）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any, Optional

from aiia.auth.token_store import OAuthToken

# Workspace 全部入り（将来機能の再同意を回避・Internalなのでgoogle審査不要）。
# 実際の操作はコード側で限定（誤送信ゼロ等）。gmailは modify 止まり（恒久削除のmail全権は付けない）。
WORKSPACE_SCOPES: tuple[str, ...] = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",        # 読取+下書き+送信(drafts.send)+ラベル
    "https://www.googleapis.com/auth/calendar",            # 予定 読取+作成/更新
    "https://www.googleapis.com/auth/drive",               # Drive 読取+書込
    "https://www.googleapis.com/auth/documents",           # Docs
    "https://www.googleapis.com/auth/spreadsheets",        # Sheets（VSEO等）
    "https://www.googleapis.com/auth/presentations",       # Slides
    "https://www.googleapis.com/auth/contacts",            # People/連絡先
)
_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"


def connect_client_id_secret() -> tuple[Optional[str], Optional[str]]:
    """連携用 OAuth クライアント(ウェブ型)。CONNECT_GOOGLE_CLIENT_ID/SECRET 優先・無ければ GOOGLE_*。"""
    cid = os.environ.get("CONNECT_GOOGLE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID")
    sec = os.environ.get("CONNECT_GOOGLE_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET")
    return cid, sec


def _state_secret() -> bytes:
    secret = os.environ.get("OAUTH_STATE_SECRET")
    if not secret:
        raise ValueError("OAUTH_STATE_SECRET が未設定です（CSRF state 署名に必要）")
    return secret.encode("utf-8")


def make_state(user_email: str, *, secret: Optional[bytes] = None) -> str:
    """user_email を HMAC 署名して state に（callback で本人性検証）。"""
    sec = secret or _state_secret()
    email = user_email.strip().lower()
    sig = hmac.new(sec, email.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{email}.{sig}".encode()).decode("ascii")


def verify_state(state: str, *, secret: Optional[bytes] = None) -> Optional[str]:
    """state を検証し正しければ user_email を返す。改竄/CSRF/壊れた値は None。"""
    sec = secret or _state_secret()
    try:
        raw = base64.urlsafe_b64decode(state.encode("ascii")).decode("utf-8")
        email, sig = raw.rsplit(".", 1)
    except (ValueError, UnicodeDecodeError):
        return None
    expect = hmac.new(sec, email.encode("utf-8"), hashlib.sha256).hexdigest()
    return email if hmac.compare_digest(sig, expect) else None


def make_reply_token(user_email: str, thread_id: str, *, secret: Optional[bytes] = None) -> str:
    """{email}.{thread_id} を HMAC 署名（/reply の改竄防止）。email+thread_idのみ＝PII最小。"""
    sec = secret or _state_secret()
    email = user_email.strip().lower()
    msg = f"{email}.{thread_id}"
    sig = hmac.new(sec, msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{msg}.{sig}".encode()).decode("ascii")


def verify_reply_token(token: str, *, secret: Optional[bytes] = None) -> Optional[tuple[str, str]]:
    """/reply トークンを検証し (user_email, thread_id) を返す。改竄/壊れた値は None。"""
    sec = secret or _state_secret()
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        email, thread_id, sig = raw.rsplit(".", 2)
    except (ValueError, UnicodeDecodeError):
        return None
    expect = hmac.new(sec, f"{email}.{thread_id}".encode(), hashlib.sha256).hexdigest()
    return (email, thread_id) if hmac.compare_digest(sig, expect) else None


def make_reply_url(user_email: str, thread_id: str, *, base_url: Optional[str] = None) -> str:
    """[対応する]url-button用の完全URL。base_url 未指定は CONNECT_BASE_URL env（既定 localhost:8788）。"""
    base = (base_url or os.environ.get("CONNECT_BASE_URL") or "http://localhost:8788").rstrip("/")
    return f"{base}/reply?s={make_reply_token(user_email, thread_id)}"


class OAuthConsentFlow:
    """google-auth-oauthlib Flow の薄いラッパ（同意URL生成 + code交換）。"""

    def __init__(self, redirect_uri: str, scopes: tuple[str, ...] = WORKSPACE_SCOPES) -> None:
        self._redirect_uri = redirect_uri
        self._scopes = scopes

    def _flow(self) -> Any:
        from google_auth_oauthlib.flow import Flow

        cid, sec = connect_client_id_secret()
        if not (cid and sec):
            raise ValueError(
                "連携用 OAuth クライアント未設定（CONNECT_GOOGLE_CLIENT_ID/SECRET または GOOGLE_*）"
            )
        config = {
            "web": {
                "client_id": cid,
                "client_secret": sec,
                "auth_uri": _AUTH_URI,
                "token_uri": _TOKEN_URI,
                "redirect_uris": [self._redirect_uri],
            }
        }
        return Flow.from_client_config(
            config,
            scopes=list(self._scopes),
            redirect_uri=self._redirect_uri,
            autogenerate_code_verifier=False,  # URL生成と交換が別プロセス＝PKCE不可
        )

    def authorization_url(self, user_email: str) -> tuple[str, str]:
        state = make_state(user_email)
        url, _ = self._flow().authorization_url(
            access_type="offline", prompt="consent", state=state
        )
        return str(url), state

    def exchange(self, code: str) -> OAuthToken:
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
        flow = self._flow()
        flow.fetch_token(code=code)
        creds = flow.credentials
        if not creds.refresh_token:
            raise ValueError(
                "refresh_token を取得できません（access_type=offline / prompt=consent を確認）"
            )
        return OAuthToken(
            refresh_token=str(creds.refresh_token), scopes=tuple(creds.scopes or self._scopes)
        )
