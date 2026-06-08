"""per-user OAuth: 個別認可フロー・トークンストア(DynamoDB+KMS)・本人credentials。

各社員が自分のGoogleを個別OAuth許可 → refresh token を user_email 単位で暗号化保管 →
実行時に本人のトークンで本人の受信箱のみ参照（越権が原理的に起きない）。
"""
from __future__ import annotations

from aiia.auth.token_store import (
    DynamoDbTokenStore,
    InMemoryTokenStore,
    KmsCipher,
    OAuthToken,
    TokenCipher,
    TokenStore,
)

__all__ = [
    "OAuthToken",
    "TokenStore",
    "TokenCipher",
    "InMemoryTokenStore",
    "KmsCipher",
    "DynamoDbTokenStore",
]
