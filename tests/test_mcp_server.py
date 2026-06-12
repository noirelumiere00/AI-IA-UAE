"""§Q aiia-mcp：per-user 本人解決(STRICT)＋ダイジェスト tool の単体テスト（外部I/O無し）。

核：外殻申告の email を**一切採らない**・slack_user_id だけで本人確定・他人のメールは出さない。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from aiia.mcp_server.resolver import (
    normalize_email,
    resolve_requester_email,
    slack_resolver_from_bot_token,
)
from aiia.mcp_server.server import build_digest_payload
from aiia.schemas import Digest


def _fake_resolver(mapping: dict[str, str]):
    return lambda uid: mapping.get(uid)


def _digest_for(email: str) -> Digest:
    # email を user_id に埋めた空ダイジェスト（どの本人のものか判別用）。
    return Digest(generated_at=datetime(2026, 6, 12, tzinfo=timezone.utc), user_id=email)


# ── resolver（STRICT・なりすまし防止） ──────────────────────────────────────────
def test_resolver_uses_only_slack_user_id_not_claimed_email() -> None:
    resolver = _fake_resolver({"U_ALICE": "alice@x.co"})
    # 外殻が user_email=他人 を詰めても**無視**され、slack_user_id の解決値だけ採る。
    ctx = {"slack_user_id": "U_ALICE", "user_email": "bob@x.co", "user_role": "admin"}
    assert resolve_requester_email(ctx, resolver=resolver) == "alice@x.co"


def test_resolver_fail_closed_on_missing_or_bad_uid() -> None:
    resolver = _fake_resolver({"U_ALICE": "alice@x.co"})
    assert resolve_requester_email({}, resolver=resolver) is None  # slack_user_id 欠落
    assert resolve_requester_email({"slack_user_id": ""}, resolver=resolver) is None
    assert resolve_requester_email({"user_email": "alice@x.co"}, resolver=resolver) is None  # emailだけ＝不可
    assert resolve_requester_email(None, resolver=resolver) is None


def test_resolver_exception_is_fail_closed() -> None:
    def _boom(uid: str):
        raise RuntimeError("slack down")

    # resolver 例外でも外殻申告にフォールバックせず None。
    assert resolve_requester_email({"slack_user_id": "U_X", "user_email": "x@x.co"}, resolver=_boom) is None


def test_slack_resolver_rejects_guest_bot_and_missing_email() -> None:
    class _WC:
        def __init__(self, user: dict):
            self._u = user

        def users_info(self, user: str) -> dict:
            return {"ok": True, "user": self._u}

    member = slack_resolver_from_bot_token(client=_WC({"profile": {"email": "a@x.co"}}))
    assert member("U01ALICE") == "a@x.co"
    guest = slack_resolver_from_bot_token(client=_WC({"is_restricted": True, "profile": {"email": "g@x.co"}}))
    assert guest("U02GUEST") is None  # ゲストは対象外
    bot = slack_resolver_from_bot_token(client=_WC({"is_bot": True, "profile": {"email": "b@x.co"}}))
    assert bot("U03BOT") is None
    noemail = slack_resolver_from_bot_token(client=_WC({"profile": {}}))
    assert noemail("U04NE") is None
    badform = slack_resolver_from_bot_token(client=_WC({"profile": {"email": "a@x.co"}}))
    assert badform("not-a-uid") is None  # slack_user_id 形式不正（下線/小文字を弾く）


def test_normalize_email() -> None:
    assert normalize_email("  Taro@X.Co ") == "taro@x.co"
    for bad in ["unknown", "", "noat", "@x.co", None]:
        assert normalize_email(bad) is None


# ── tool 本体（build_digest_payload） ──────────────────────────────────────────
def test_digest_payload_returns_blocks_for_resolved_user() -> None:
    resolver = _fake_resolver({"U_ALICE": "alice@x.co"})
    seen = {}

    def _dfn(email: str) -> Digest:
        seen["email"] = email
        return _digest_for(email)

    out = json.loads(build_digest_payload({"_user_context": {"slack_user_id": "U_ALICE"}}, resolver=resolver, digest_fn=_dfn))
    assert out["ok"] is True and "blocks" in out and "text" in out
    assert seen["email"] == "alice@x.co"  # 解決された本人のダイジェストだけ生成


def test_digest_payload_impersonation_blocked() -> None:
    # 🔴 A が B のメールを読もうと user_email=B を詰めても、slack_user_id=A の本人解決が優先＝Aのみ。
    resolver = _fake_resolver({"U_ALICE": "alice@x.co", "U_BOB": "bob@x.co"})
    called = []
    out = json.loads(build_digest_payload(
        {"_user_context": {"slack_user_id": "U_ALICE", "user_email": "bob@x.co"}},
        resolver=resolver, digest_fn=lambda e: called.append(e) or _digest_for(e),
    ))
    assert out["ok"] is True
    assert called == ["alice@x.co"]  # Bobのメールは絶対に生成されない


def test_digest_payload_unresolved_identity_no_side_effect() -> None:
    resolver = _fake_resolver({})
    called = []
    out = json.loads(build_digest_payload(
        {"_user_context": {"slack_user_id": "U_GHOST"}},
        resolver=resolver, digest_fn=lambda e: called.append(e) or _digest_for(e),
    ))
    assert out["ok"] is False and out["reason"] == "identity_unresolved"
    assert called == []  # 本人未確定＝ダイジェスト生成は走らない（他人のメールも出ない）


def test_digest_payload_token_missing_returns_connect_hint() -> None:
    resolver = _fake_resolver({"U_ALICE": "alice@x.co"})

    def _dfn(email: str) -> Digest:
        raise ValueError("token_missing")

    out = json.loads(build_digest_payload({"_user_context": {"slack_user_id": "U_ALICE"}}, resolver=resolver, digest_fn=_dfn))
    assert out["ok"] is False and out["reason"] == "token_missing" and "連携" in out["text"]
