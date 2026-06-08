"""連携 callback の純粋ロジック（FastAPI不要・課金ゼロ）。成功/キャンセル/改竄/交換失敗/refresh無しを検証。"""
from __future__ import annotations

from typing import Any

from aiia.auth.oauth_flow import make_state
from aiia.auth.token_store import InMemoryTokenStore, OAuthToken
from aiia.connect_web.callback import process_callback

_S = b"unit-secret"


def _ok_exchange(code: str) -> OAuthToken:
    return OAuthToken("1//refresh", ("gmail.readonly", "gmail.send"))


def test_callback_success_stores_token() -> None:
    store = InMemoryTokenStore()
    state = make_state("Alice@x.com", secret=_S)
    r = process_callback(code="c", state=state, error=None, store=store, exchange=_ok_exchange, state_secret=_S)
    assert r.ok and r.email == "alice@x.com"
    assert store.has("alice@x.com")


def test_callback_user_denied() -> None:
    store = InMemoryTokenStore()
    r = process_callback(code=None, state=None, error="access_denied", store=store, exchange=_ok_exchange, state_secret=_S)
    assert not r.ok and store.list_emails() == []


def test_callback_tampered_state_rejected() -> None:
    store = InMemoryTokenStore()
    r = process_callback(code="c", state="tampered!!", error=None, store=store, exchange=_ok_exchange, state_secret=_S)
    assert not r.ok and store.list_emails() == []


def test_callback_no_refresh_token_not_stored() -> None:
    store = InMemoryTokenStore()
    state = make_state("a@x.com", secret=_S)
    r = process_callback(code="c", state=state, error=None, store=store,
                         exchange=lambda c: OAuthToken(""), state_secret=_S)
    assert not r.ok and not store.has("a@x.com")


def test_callback_exchange_failure_not_stored() -> None:
    store = InMemoryTokenStore()
    state = make_state("a@x.com", secret=_S)

    def boom(code: str) -> Any:
        raise RuntimeError("token exchange failed")

    r = process_callback(code="c", state=state, error=None, store=store, exchange=boom, state_secret=_S)
    assert not r.ok and not store.has("a@x.com")
