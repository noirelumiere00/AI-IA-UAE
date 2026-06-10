"""Slack ユーザー認可(xoxp)フロー＋connect-web再利用（fake注入・課金ゼロ）。"""

from __future__ import annotations

import pytest

from aiia.auth.oauth_flow import is_universal_email, make_universal_state, verify_state
from aiia.auth.slack_oauth import SlackOAuthConsentFlow
from aiia.auth.token_store import InMemoryTokenStore, OAuthToken
from aiia.connect_web.callback import process_callback

_S = b"unit-secret"


class _FakeOAuthWC:
    def __init__(self, resp: dict) -> None:
        self._resp = resp
        self.calls: list[dict] = []

    def oauth_v2_access(self, **kw) -> dict:
        self.calls.append(kw)
        return self._resp


def _flow(resp: dict, email: str | None) -> SlackOAuthConsentFlow:
    return SlackOAuthConsentFlow(
        "https://pub/oauth2/slack/callback",
        client=_FakeOAuthWC(resp),
        email_resolver=lambda uid: email,
    )


def test_exchange_returns_xoxp_and_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_CLIENT_ID", "111.222")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "sec")
    resp = {
        "ok": True,
        "authed_user": {
            "id": "U1",
            "access_token": "xoxp-abc",
            "scope": "search:read,channels:history",
        },
    }
    tok = _flow(resp, "Bob@Vector.co.jp").exchange("code123")
    assert tok.refresh_token == "xoxp-abc"
    assert tok.email == "Bob@Vector.co.jp"  # 正規化は process_callback 側
    assert "search:read" in tok.scopes and "channels:history" in tok.scopes


def test_exchange_without_user_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_CLIENT_ID", "111.222")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "sec")
    with pytest.raises(ValueError):
        _flow({"ok": True, "authed_user": {"id": "U1"}}, "x@y.com").exchange(
            "c"
        )  # access_token無し


def test_authorization_url_universal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_CLIENT_ID", "111.222")
    monkeypatch.setenv("OAUTH_STATE_SECRET", "x" * 64)
    url, state = SlackOAuthConsentFlow(
        "https://pub/oauth2/slack/callback"
    ).authorization_url_universal()
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "user_scope=search%3Aread" in url and "channels%3Ahistory" in url
    assert is_universal_email(verify_state(state))  # 共通リンク番兵


def test_process_callback_reused_for_slack() -> None:
    """Slack exchange が email を埋めるので、既存 process_callback(universal) がそのまま使える。"""
    slack_store = InMemoryTokenStore()
    state = make_universal_state(secret=_S)

    def slack_exchange(code: str) -> OAuthToken:
        return OAuthToken("xoxp-zzz", ("search:read",), email="carol@vector.co.jp")

    r = process_callback(
        code="c",
        state=state,
        error=None,
        store=slack_store,
        exchange=slack_exchange,
        state_secret=_S,
        allowed_domains={"vector.co.jp"},
    )
    assert r.ok and r.email == "carol@vector.co.jp"
    assert slack_store.get("carol@vector.co.jp").refresh_token == "xoxp-zzz"


def test_slack_callback_route_registered_only_with_slack_store() -> None:
    from aiia.connect_web.app import create_app

    store = InMemoryTokenStore()
    base = create_app(redirect_uri="https://pub/oauth2/callback", store=store)
    assert "/oauth2/slack/callback" not in {r.path for r in base.routes}  # 未指定なら生えない

    withslack = create_app(
        redirect_uri="https://pub/oauth2/callback",
        store=store,
        slack_store=InMemoryTokenStore(),
        slack_exchange=lambda c: OAuthToken("xoxp", (), email="a@x.com"),
    )
    assert "/oauth2/slack/callback" in {r.path for r in withslack.routes}
