"""per-user トークンストア（OAuthToken/InMemory/KmsCipher/DynamoDbTokenStore）。

実 AWS は使わず fake KMS/DynamoDB を注入＝課金ゼロ・boto3 不要。
"""
from __future__ import annotations

from typing import Any

import pytest

from aiia.auth.token_store import (
    DynamoDbTokenStore,
    InMemoryTokenStore,
    KmsCipher,
    OAuthToken,
)


# ── fakes ─────────────────────────────────────────────────────────────────
class FakeKms:
    """暗号文に user_email(context) を埋め、復号時に context 一致を強制（KMSのAAD相当）。"""

    def encrypt(self, *, KeyId: str, Plaintext: bytes, EncryptionContext: dict | None = None) -> dict:
        email = (EncryptionContext or {}).get("user_email", "")
        # 平文をそのまま残さない（バイト反転で擬似暗号化＝実KMS同様 literal が残らない）
        return {"CiphertextBlob": b"ENC|" + email.encode() + b"|" + Plaintext[::-1]}

    def decrypt(self, *, CiphertextBlob: bytes, KeyId: str, EncryptionContext: dict | None = None) -> dict:
        assert CiphertextBlob.startswith(b"ENC|")
        email, _, ct = CiphertextBlob[4:].partition(b"|")
        assert (EncryptionContext or {}).get("user_email", "").encode() == email, "context不一致"
        return {"Plaintext": ct[::-1]}


class FakeDdb:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def put_item(self, *, TableName: str, Item: dict) -> None:
        self.items[Item["user_email"]["S"]] = Item

    def get_item(self, *, TableName: str, Key: dict) -> dict:
        it = self.items.get(Key["user_email"]["S"])
        return {"Item": it} if it else {}

    def get_paginator(self, name: str) -> Any:
        items = list(self.items.values())

        class _P:
            def paginate(self, **kw: Any) -> Any:
                yield {"Items": items}

        return _P()


# ── tests ─────────────────────────────────────────────────────────────────
def test_oauthtoken_repr_hides_secret() -> None:
    t = OAuthToken(refresh_token="1//SECRET", scopes=("gmail.readonly",))
    assert "SECRET" not in repr(t) and "***" in repr(t)


def test_inmemory_normalizes_and_lists() -> None:
    s = InMemoryTokenStore()
    s.put("  Alice@X.COM ", OAuthToken("r"))
    assert s.has("alice@x.com")
    assert s.get("ALICE@x.com").refresh_token == "r"  # type: ignore[union-attr]
    assert s.list_emails() == ["alice@x.com"]


def test_dynamo_roundtrip_and_no_plaintext() -> None:
    ddb = FakeDdb()
    store = DynamoDbTokenStore("tbl", KmsCipher("key", client=FakeKms()), client=ddb)
    store.put("Bob@x.com", OAuthToken("1//refresh", ("gmail.readonly", "gmail.send")))

    # DynamoDB に平文 refresh token が存在しない（KMS暗号文のみ）
    assert b"1//refresh" not in ddb.items["bob@x.com"]["refresh_token_enc"]["B"]

    got = store.get("bob@x.com")
    assert got is not None
    assert got.refresh_token == "1//refresh"
    assert got.scopes == ("gmail.readonly", "gmail.send")
    assert store.has("bob@x.com") is True
    assert store.get("missing@x.com") is None
    assert store.list_emails() == ["bob@x.com"]


def test_kms_context_binding_prevents_cross_user_decrypt() -> None:
    cipher = KmsCipher("key", client=FakeKms())
    enc = cipher.encrypt("x", context={"user_email": "a@x.com"})
    assert cipher.decrypt(enc, context={"user_email": "a@x.com"}) == "x"
    with pytest.raises(AssertionError):
        cipher.decrypt(enc, context={"user_email": "b@x.com"})  # 別人の context では復号不可
