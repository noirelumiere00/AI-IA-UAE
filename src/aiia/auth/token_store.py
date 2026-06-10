"""per-user OAuth トークンストア（40-50人・各個人の refresh token を user_email 単位で保管）。

各人が OAuth 同意で得た refresh token を保管し、実行時に**本人の**トークンを選ぶ＝本人の
データにしか触れない（越権が原理的に起きない）。refresh token は機微シークレット：
ログ/プロンプト/例外に出さない（OAuthToken.__repr__ で伏せる）。

本番は **KMS 暗号化 + DynamoDB**（サーバーレス・40-50人の小トークンに最適）。
TeamAgent `adapters/oauth_token_store.py` からの移植（保存先を RDS→DynamoDB に変更）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True, repr=False)
class OAuthToken:
    """1ユーザー分の refresh token（＋認可済みスコープ）。repr で token を伏せる。

    email は共通1リンク(universal)連携時に **Google の id_token から確定した本人メール**。
    個別リンク連携では state からメールが分かるので未設定(None)でよい。保存(DynamoDB)は
    user_email を PK にするため email 欄自体は永続化しない（連携時の本人特定にのみ使う）。
    """

    refresh_token: str
    scopes: tuple[str, ...] = ()
    email: Optional[str] = None

    def __repr__(self) -> str:
        return f"OAuthToken(refresh_token=***, scopes={self.scopes!r}, email={self.email!r})"


def _norm(email: str) -> str:
    return email.strip().lower()


@runtime_checkable
class TokenStore(Protocol):
    """user_email → OAuthToken の差し替え可能な保管口。"""

    def get(self, user_email: str) -> Optional[OAuthToken]: ...
    def put(self, user_email: str, token: OAuthToken) -> None: ...
    def has(self, user_email: str) -> bool: ...
    def list_emails(self) -> list[str]: ...  # 連携済みユーザー列挙（多人数オーケストレーション用）


class InMemoryTokenStore:
    """dev/test 用メモリ実装。email は正規化（大小文字/前後空白）して引く。"""

    def __init__(self, initial: Optional[dict[str, OAuthToken]] = None) -> None:
        self._tokens: dict[str, OAuthToken] = {}
        for email, token in (initial or {}).items():
            self._tokens[_norm(email)] = token

    def get(self, user_email: str) -> Optional[OAuthToken]:
        return self._tokens.get(_norm(user_email))

    def put(self, user_email: str, token: OAuthToken) -> None:
        self._tokens[_norm(user_email)] = token

    def has(self, user_email: str) -> bool:
        return _norm(user_email) in self._tokens

    def list_emails(self) -> list[str]:
        return sorted(self._tokens)


@runtime_checkable
class TokenCipher(Protocol):
    """refresh token の暗号化/復号（at-rest 暗号化）。本番は KMS 実装を注入。"""

    def encrypt(self, plaintext: str, *, context: Optional[dict[str, str]] = None) -> bytes: ...
    def decrypt(self, ciphertext: bytes, *, context: Optional[dict[str, str]] = None) -> str: ...


class KmsCipher:
    """AWS KMS で refresh token を暗号化/復号（boto3 は遅延 import）。

    復号には KMS Decrypt の IAM 権限が必要＝DynamoDB を読めても token は復号できない。
    EncryptionContext={user_email} を AAD にして per-user 束縛（他人の暗号文を流用不可）。
    """

    def __init__(self, key_id: str, client: Any = None, *, region: Optional[str] = None) -> None:
        self._key_id = key_id
        self._client = client
        self._region = region or os.environ.get("OAUTH_KMS_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-1"

    def _kms(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("kms", region_name=self._region)
        return self._client

    def encrypt(self, plaintext: str, *, context: Optional[dict[str, str]] = None) -> bytes:
        kwargs: dict[str, Any] = {"KeyId": self._key_id, "Plaintext": plaintext.encode("utf-8")}
        if context:
            kwargs["EncryptionContext"] = context
        return bytes(self._kms().encrypt(**kwargs)["CiphertextBlob"])

    def decrypt(self, ciphertext: bytes, *, context: Optional[dict[str, str]] = None) -> str:
        kwargs: dict[str, Any] = {"CiphertextBlob": ciphertext, "KeyId": self._key_id}
        if context:
            kwargs["EncryptionContext"] = context
        return str(self._kms().decrypt(**kwargs)["Plaintext"].decode("utf-8"))


class DynamoDbTokenStore:
    """DynamoDB(+KMS) に refresh token を保管する TokenStore。

    PK=user_email、`refresh_token_enc`=KMS CiphertextBlob（**平文は保存しない**）、scopes、updated_at。
    boto3 は遅延 import。テストは fake client を注入（課金ゼロ）。
    """

    def __init__(
        self, table_name: str, cipher: TokenCipher, *, client: Any = None, region: Optional[str] = None
    ) -> None:
        self._table = table_name
        self._cipher = cipher
        self._client = client
        self._region = region or os.environ.get("AWS_REGION") or "ap-northeast-1"

    def _ddb(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("dynamodb", region_name=self._region)
        return self._client

    def get(self, user_email: str) -> Optional[OAuthToken]:
        email = _norm(user_email)
        item = self._ddb().get_item(
            TableName=self._table, Key={"user_email": {"S": email}}
        ).get("Item")
        if not item:
            return None
        enc = bytes(item["refresh_token_enc"]["B"])
        scopes = tuple(s["S"] for s in item.get("scopes", {}).get("L", []))
        refresh = self._cipher.decrypt(enc, context={"user_email": email})
        return OAuthToken(refresh_token=refresh, scopes=scopes)

    def put(self, user_email: str, token: OAuthToken) -> None:
        email = _norm(user_email)
        enc = self._cipher.encrypt(token.refresh_token, context={"user_email": email})
        self._ddb().put_item(
            TableName=self._table,
            Item={
                "user_email": {"S": email},
                "refresh_token_enc": {"B": enc},
                "scopes": {"L": [{"S": s} for s in token.scopes]},
                "updated_at": {"S": datetime.now(timezone.utc).isoformat()},
            },
        )

    def has(self, user_email: str) -> bool:
        return self.get(user_email) is not None

    def list_emails(self) -> list[str]:
        out: list[str] = []
        paginator = self._ddb().get_paginator("scan")
        for page in paginator.paginate(TableName=self._table, ProjectionExpression="user_email"):
            out.extend(i["user_email"]["S"] for i in page.get("Items", []))
        return sorted(out)
