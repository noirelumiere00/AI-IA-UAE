"""本人の refresh token → Google Credentials（per-user・本人のデータのみ）。

各アダプタ(WorkspaceGmailToolset 等)が `build_user_credentials(token)` で本人の認証情報を得る。
google-auth は遅延 import（未導入でも state/フロー以外のテストは動く）。
"""
from __future__ import annotations

from typing import Any

from aiia.auth.oauth_flow import connect_client_id_secret
from aiia.auth.token_store import OAuthToken

_TOKEN_URI = "https://oauth2.googleapis.com/token"


def build_user_credentials(token: OAuthToken) -> Any:
    cid, sec = connect_client_id_secret()
    if not (cid and sec):
        raise ValueError(
            "連携用 OAuth クライアント未設定（CONNECT_GOOGLE_CLIENT_ID/SECRET または GOOGLE_*）"
        )
    if not token.refresh_token:
        raise ValueError("OAuthToken.refresh_token が空です（本人が未認可）")

    from google.oauth2.credentials import Credentials

    # scopes は **渡さない**。refresh 時に scope= を送ると「付与外のscope要求」で invalid_scope に
    # なり得る（保存メタと実際の付与がズレた場合）。refresh_token の付与内容を正とする＝堅牢。
    return Credentials(
        token=None,
        refresh_token=token.refresh_token,
        token_uri=_TOKEN_URI,
        client_id=cid,
        client_secret=sec,
    )
