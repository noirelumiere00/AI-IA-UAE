"""per-user OAuth: state署名/検証・スコープ・連携クライアント解決・creds builder のガード。

google ライブラリ不要の範囲（state/scopes/env解決/未設定エラー）を検証＝課金ゼロ・依存最小。
"""
from __future__ import annotations

import pytest

from aiia.auth import oauth_flow as of
from aiia.auth.google_creds import build_user_credentials
from aiia.auth.token_store import OAuthToken

_SECRET = b"unit-test-secret"


def test_state_roundtrip() -> None:
    state = of.make_state("  Alice@X.com ", secret=_SECRET)
    assert of.verify_state(state, secret=_SECRET) == "alice@x.com"  # 正規化される


def test_state_tamper_rejected() -> None:
    state = of.make_state("alice@x.com", secret=_SECRET)
    assert of.verify_state(state, secret=b"other-secret") is None  # 署名鍵違い→None
    assert of.verify_state("not-base64!!", secret=_SECRET) is None


def test_scopes_include_send_and_calendar() -> None:
    joined = " ".join(of.WORKSPACE_SCOPES)
    assert "gmail.readonly" in joined
    assert "gmail.compose" in joined
    assert "gmail.send" in joined  # 2回確認送信のため
    assert "calendar.readonly" in joined


def test_connect_client_prefers_connect_then_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONNECT_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CONNECT_GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-sec")
    assert of.connect_client_id_secret() == ("g-id", "g-sec")
    monkeypatch.setenv("CONNECT_GOOGLE_CLIENT_ID", "c-id")
    monkeypatch.setenv("CONNECT_GOOGLE_CLIENT_SECRET", "c-sec")
    assert of.connect_client_id_secret() == ("c-id", "c-sec")


def test_state_secret_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OAUTH_STATE_SECRET", raising=False)
    with pytest.raises(ValueError):
        of.make_state("a@x.com")


def test_build_user_credentials_requires_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONNECT_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    with pytest.raises(ValueError):
        build_user_credentials(OAuthToken("1//r"))


def test_build_user_credentials_requires_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    with pytest.raises(ValueError):
        build_user_credentials(OAuthToken(""))  # refresh_token 空→拒否
