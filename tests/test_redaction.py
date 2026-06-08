"""PIIマスク（redaction）。NFR-06 / 下書き本文・ダイジェストに適用。"""
from __future__ import annotations

from aiia.safety.redaction import find_secrets, redact


def test_valid_card_masked() -> None:
    out = redact("カード番号は 4111 1111 1111 1111 です")
    assert "[REDACTED:card]" in out
    assert "4111" not in out


def test_luhn_invalid_card_untouched() -> None:
    # 末尾を変え Luhn を崩した番号はカードと判定しない（誤マスクしない）。
    out = redact("番号 4111 1111 1111 1112")
    assert "[REDACTED:card]" not in out


def test_ssn_awskey_apikey_bearer_masked() -> None:
    out = redact("ssn 123-45-6789 / AKIAIOSFODNN7EXAMPLE / api_key=abcd1234 / Bearer eyJhbGci.payload.sig")
    for tag in ("ssn", "aws_key", "secret", "bearer"):
        assert f"[REDACTED:{tag}]" in out


def test_find_secrets_counts_aws_key() -> None:
    found = find_secrets("AKIAIOSFODNN7EXAMPLE")
    assert len(found) == 1
    assert found[0].kind == "aws_key"
