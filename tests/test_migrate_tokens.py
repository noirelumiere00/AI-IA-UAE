"""token 移行ロジック（RDS復号→DynamoDB再暗号化put）。fake KMS/DDB・課金ゼロ。"""
from __future__ import annotations

from typing import Any

from aiia.auth.token_store import DynamoDbTokenStore, KmsCipher
from aiia.scripts.migrate_tokens import migrate


class FakeKms:
    """暗号文に user_email を埋め平文を反転（test_token_store と同じ擬似暗号）。"""

    def encrypt(self, *, KeyId: str, Plaintext: bytes, EncryptionContext: dict | None = None) -> dict:
        email = (EncryptionContext or {}).get("user_email", "")
        return {"CiphertextBlob": b"ENC|" + email.encode() + b"|" + Plaintext[::-1]}

    def decrypt(self, *, CiphertextBlob: bytes, KeyId: str, EncryptionContext: dict | None = None) -> dict:
        assert CiphertextBlob.startswith(b"ENC|")
        email, _, ct = CiphertextBlob[4:].partition(b"|")
        assert (EncryptionContext or {}).get("user_email", "").encode() == email
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


def test_migrate_decrypts_and_reencrypts() -> None:
    src_kms = FakeKms()
    # RDS 行：refresh_token_enc は元KMSで暗号化済（context=user_email）
    def enc(email: str, rt: str) -> bytes:
        return KmsCipher("srcKey", client=src_kms).encrypt(rt, context={"user_email": email})

    rows = [
        {"user_email": "alice@vectorinc.co.jp", "refresh_token_enc": enc("alice@vectorinc.co.jp", "1//A"), "scopes": ["https://www.googleapis.com/auth/gmail.modify"]},
        {"user_email": "bob@vectorinc.co.jp", "refresh_token_enc": enc("bob@vectorinc.co.jp", "1//B"), "scopes": ["x"]},
    ]
    ddb = FakeDdb()
    store = DynamoDbTokenStore("aiia-oauth-tokens", KmsCipher("tgtKey", client=FakeKms()), client=ddb)

    n = migrate(rows, src_cipher=KmsCipher("srcKey", client=src_kms), store=store)
    assert n == 2
    assert sorted(store.list_emails()) == ["alice@vectorinc.co.jp", "bob@vectorinc.co.jp"]
    # 移行後、本人トークンが復号して一致（再暗号化されている＝平文非保存）
    got = store.get("alice@vectorinc.co.jp")
    assert got is not None and got.refresh_token == "1//A"
    assert "gmail.modify" in " ".join(got.scopes)
    assert b"1//A" not in ddb.items["alice@vectorinc.co.jp"]["refresh_token_enc"]["B"]
