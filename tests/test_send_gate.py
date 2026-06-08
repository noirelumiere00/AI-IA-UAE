"""2段確認ゲート（SendConfirmationGate）＋ ゲート済み送信(WorkspaceGmailSender)。課金ゼロ。

『2段確認が無ければ drafts.send は呼べない』を構造的に検証（誤送信ゼロの担保）。
"""
from __future__ import annotations

from typing import Any

import pytest

from aiia.auth.token_store import OAuthToken
from aiia.mcp.workspace_gmail import WorkspaceGmailSender
from aiia.safety.hitl import AutoSendError, SendConfirmationGate


# ── ゲート ────────────────────────────────────────────────────────────────
def test_gate_denies_without_confirmation() -> None:
    g = SendConfirmationGate()
    with pytest.raises(AutoSendError):
        g.authorize_send(None, user_id="u", draft_id="d", now=100.0)


def test_gate_allows_valid_then_blocks_replay() -> None:
    g = SendConfirmationGate(ttl_seconds=300)
    c = g.mint(user_id="u", draft_id="d", now=100.0, nonce="n1")
    g.authorize_send(c, user_id="u", draft_id="d", now=120.0)  # 初回OK
    with pytest.raises(AutoSendError):
        g.authorize_send(c, user_id="u", draft_id="d", now=121.0)  # 単回→リプレイ拒否


def test_gate_rejects_wrong_user_or_draft() -> None:
    g = SendConfirmationGate()
    c1 = g.mint(user_id="u", draft_id="d", now=100.0, nonce="n1")
    with pytest.raises(AutoSendError):
        g.authorize_send(c1, user_id="other", draft_id="d", now=101.0)  # 別人
    c2 = g.mint(user_id="u", draft_id="d", now=100.0, nonce="n2")
    with pytest.raises(AutoSendError):
        g.authorize_send(c2, user_id="u", draft_id="other", now=101.0)  # 別下書き


def test_gate_rejects_expired() -> None:
    g = SendConfirmationGate(ttl_seconds=300)
    c = g.mint(user_id="u", draft_id="d", now=100.0, nonce="n")
    with pytest.raises(AutoSendError):
        g.authorize_send(c, user_id="u", draft_id="d", now=500.0)  # TTL超過


# ── ゲート済み送信/編集 ─────────────────────────────────────────────────────
class _Exec:
    def __init__(self, r: Any) -> None:
        self._r = r

    def execute(self) -> Any:
        return self._r


class _Drafts:
    def __init__(self, svc: "FakeSenderSvc") -> None:
        self.svc = svc

    def get(self, **kw: Any) -> _Exec:
        self.svc.calls.append(("get", kw))
        return _Exec({"id": kw["id"], "message": {"threadId": "t1"}})

    def update(self, **kw: Any) -> _Exec:
        self.svc.calls.append(("update", kw))
        return _Exec({"id": kw["id"]})

    def delete(self, **kw: Any) -> _Exec:
        self.svc.calls.append(("delete", kw))
        return _Exec({})

    def send(self, **kw: Any) -> _Exec:
        self.svc.calls.append(("send", kw))
        return _Exec({"id": "sent_msg_1"})


class _Users:
    def __init__(self, svc: "FakeSenderSvc") -> None:
        self.svc = svc

    def drafts(self) -> _Drafts:
        return _Drafts(self.svc)


class FakeSenderSvc:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def users(self) -> _Users:
        return _Users(self)


def test_sender_send_update_delete() -> None:
    svc = FakeSenderSvc()
    s = WorkspaceGmailSender(OAuthToken("1//r"), service=svc)
    assert s.send_draft("d1") == "sent_msg_1"
    assert s.update_draft(draft_id="d1", thread_id="t1", subject="Re: 件名", body="本文です")
    s.delete_draft("d1")
    assert [c[0] for c in svc.calls] == ["send", "update", "delete"]
    upd = next(c for c in svc.calls if c[0] == "update")
    assert "raw" in upd[1]["body"]["message"]  # 編集はMIME raw を送る


def test_gated_send_end_to_end() -> None:
    """2段確認→ゲート通過→送信、の正規フロー（確認無しなら送らない）。"""
    gate = SendConfirmationGate()
    svc = FakeSenderSvc()
    sender = WorkspaceGmailSender(OAuthToken("1//r"), service=svc)

    # 確認なしでは送らない
    with pytest.raises(AutoSendError):
        gate.authorize_send(None, user_id="u", draft_id="d1", now=10.0)
    assert svc.calls == []

    # 2段確認mint→authorize→send
    conf = gate.mint(user_id="u", draft_id="d1", now=10.0, nonce="x")
    gate.authorize_send(conf, user_id="u", draft_id="d1", now=11.0)
    sender.send_draft("d1")
    assert [c[0] for c in svc.calls] == ["send"]
