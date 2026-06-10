"""連携 callback の純粋ロジック（FastAPI不要・課金ゼロ）。成功/キャンセル/改竄/交換失敗/refresh無しを検証。"""

from __future__ import annotations

from typing import Any

from aiia.auth.oauth_flow import make_state, make_universal_state
from aiia.auth.token_store import InMemoryTokenStore, OAuthToken
from aiia.connect_web.callback import process_callback

_S = b"unit-secret"


def _ok_exchange(code: str) -> OAuthToken:
    return OAuthToken("1//refresh", ("gmail.readonly", "gmail.send"))


def test_callback_success_stores_token() -> None:
    store = InMemoryTokenStore()
    state = make_state("Alice@x.com", secret=_S)
    r = process_callback(
        code="c", state=state, error=None, store=store, exchange=_ok_exchange, state_secret=_S
    )
    assert r.ok and r.email == "alice@x.com"
    assert store.has("alice@x.com")


def test_callback_user_denied() -> None:
    store = InMemoryTokenStore()
    r = process_callback(
        code=None,
        state=None,
        error="access_denied",
        store=store,
        exchange=_ok_exchange,
        state_secret=_S,
    )
    assert not r.ok and store.list_emails() == []


def test_callback_tampered_state_rejected() -> None:
    store = InMemoryTokenStore()
    r = process_callback(
        code="c",
        state="tampered!!",
        error=None,
        store=store,
        exchange=_ok_exchange,
        state_secret=_S,
    )
    assert not r.ok and store.list_emails() == []


def test_callback_no_refresh_token_not_stored() -> None:
    store = InMemoryTokenStore()
    state = make_state("a@x.com", secret=_S)
    r = process_callback(
        code="c",
        state=state,
        error=None,
        store=store,
        exchange=lambda c: OAuthToken(""),
        state_secret=_S,
    )
    assert not r.ok and not store.has("a@x.com")


def test_callback_exchange_failure_not_stored() -> None:
    store = InMemoryTokenStore()
    state = make_state("a@x.com", secret=_S)

    def boom(code: str) -> Any:
        raise RuntimeError("token exchange failed")

    r = process_callback(
        code="c", state=state, error=None, store=store, exchange=boom, state_secret=_S
    )
    assert not r.ok and not store.has("a@x.com")


def _universal_exchange(email: str | None) -> Any:
    def _ex(code: str) -> OAuthToken:
        return OAuthToken("1//refresh", ("gmail.modify",), email=email)

    return _ex


def test_callback_universal_stores_under_google_email() -> None:
    store = InMemoryTokenStore()
    state = make_universal_state(secret=_S)
    r = process_callback(
        code="c",
        state=state,
        error=None,
        store=store,
        exchange=_universal_exchange("Bob@Vector.co.jp"),
        state_secret=_S,
        allowed_domains={"vector.co.jp"},
    )
    assert r.ok and r.email == "bob@vector.co.jp"  # Googleログイン結果で確定・正規化
    assert store.has("bob@vector.co.jp")


def test_callback_universal_rejects_outside_domain() -> None:
    store = InMemoryTokenStore()
    state = make_universal_state(secret=_S)
    r = process_callback(
        code="c",
        state=state,
        error=None,
        store=store,
        exchange=_universal_exchange("stranger@gmail.com"),
        state_secret=_S,
        allowed_domains={"vector.co.jp"},
    )
    assert not r.ok and store.list_emails() == []  # 許可ドメイン外は保存しない


def test_callback_universal_no_email_fails() -> None:
    store = InMemoryTokenStore()
    state = make_universal_state(secret=_S)
    r = process_callback(
        code="c",
        state=state,
        error=None,
        store=store,
        exchange=_universal_exchange(None),
        state_secret=_S,
        allowed_domains=set(),
    )
    assert not r.ok and store.list_emails() == []  # メール取得不可は弾く


def test_callback_universal_no_restriction_allows_any() -> None:
    store = InMemoryTokenStore()
    state = make_universal_state(secret=_S)
    r = process_callback(
        code="c",
        state=state,
        error=None,
        store=store,
        exchange=_universal_exchange("a@anywhere.com"),
        state_secret=_S,
        allowed_domains=set(),
    )
    assert r.ok and store.has("a@anywhere.com")  # 制限なし(PoC)は誰でも可


def test_process_reply_creates_draft_and_returns_thread() -> None:
    from aiia.auth.oauth_flow import make_reply_token
    from aiia.connect_web.callback import process_reply

    calls: list = []

    def factory(email: str, tid: str) -> dict:
        calls.append((email, tid))
        return {"draft_id": "d1"}

    tok = make_reply_token("a@x.com", "thr9", secret=_S)
    r = process_reply(s=tok, reply_factory=factory, state_secret=_S)
    assert r.ok and r.thread_id == "thr9" and calls == [("a@x.com", "thr9")]


def test_process_reply_bad_token_rejected() -> None:
    from aiia.connect_web.callback import process_reply

    r = process_reply(s="garbage!!", reply_factory=lambda e, t: {}, state_secret=_S)
    assert not r.ok and r.thread_id is None


def test_process_reply_draft_fail_still_redirects() -> None:
    from aiia.auth.oauth_flow import make_reply_token
    from aiia.connect_web.callback import process_reply

    def factory(email: str, tid: str) -> dict:
        raise RuntimeError("boom")

    tok = make_reply_token("a@x.com", "thr9", secret=_S)
    r = process_reply(s=tok, reply_factory=factory, state_secret=_S)
    assert r.ok and r.thread_id == "thr9"  # fail-safe: 下書き失敗でも会話は開く
