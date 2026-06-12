"""connect_web FastAPI 層のセキュリティ regression テスト（反射型XSS・セキュリティヘッダ）。

実 Google/Slack には触れない（InMemoryTokenStore + fake exchange のみ・課金ゼロ）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aiia.auth.token_store import InMemoryTokenStore, OAuthToken
from aiia.connect_web.app import create_app

_SECRET = "unit-secret"


def _ok_exchange(code: str) -> OAuthToken:
    return OAuthToken("1//refresh", ("gmail.modify",))


def _client() -> TestClient:
    app = create_app(
        redirect_uri="https://example.test/oauth2/callback",
        store=InMemoryTokenStore(),
        exchange=_ok_exchange,
        reply_factory=lambda email, tid: {"draft_id": "d1"},
    )
    return TestClient(app)


def test_callback_error_param_is_escaped_not_reflected() -> None:
    """GET /oauth2/callback?error=<script>... が生HTMLで反射しない（反射型XSS regression）。"""
    c = _client()
    r = c.get("/oauth2/callback", params={"error": "<script>alert(1)</script>"})
    assert r.status_code == 400
    assert "<script>" not in r.text  # 生タグが本文に現れない
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text  # エスケープ済みで表示される


def test_callback_missing_params_detail_is_plain_text() -> None:
    """正常系の見た目（タイトル/詳細の文言）が維持されている。"""
    c = _client()
    r = c.get("/oauth2/callback")
    assert r.status_code == 400
    assert "不正なリクエスト" in r.text
    assert "code または state がありません" in r.text


def test_callback_success_renders_email(monkeypatch: Any) -> None:
    """成功パスの挙動維持：連携完了メッセージとメールが表示される。"""
    from aiia.auth.oauth_flow import make_state

    monkeypatch.setenv("OAUTH_STATE_SECRET", _SECRET)
    c = _client()
    state = make_state("alice@x.com")
    r = c.get("/oauth2/callback", params={"code": "c", "state": state})
    assert r.status_code == 200
    assert "連携が完了しました" in r.text
    assert "alice@x.com" in r.text


def test_reply_error_page_is_escaped(monkeypatch: Any) -> None:
    """/reply の失敗ページも同じ出口(_html)を通りエスケープされる。"""
    monkeypatch.setenv("OAUTH_STATE_SECRET", _SECRET)
    c = _client()
    r = c.get("/reply", params={"s": "<img src=x onerror=alert(1)>"})
    assert r.status_code == 400
    assert "<img" not in r.text


@pytest.mark.parametrize(
    "path,params",
    [
        ("/healthz", {}),
        ("/oauth2/callback", {"error": "x"}),
        ("/reply", {}),
    ],
)
def test_security_headers_on_all_responses(path: str, params: dict) -> None:
    """全レスポンスに CSP / nosniff が付く（middleware 一括付与）。"""
    c = _client()
    r = c.get(path, params=params)
    assert r.headers["Content-Security-Policy"] == "default-src 'none'; style-src 'unsafe-inline'"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
