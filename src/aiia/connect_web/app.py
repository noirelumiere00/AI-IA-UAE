"""連携 callback の FastAPI アプリ（薄いラッパ）。本番は ALB/API Gateway 背後で HTTPS 公開。

ロジックは callback.process_callback（純粋）に委譲。fastapi は遅延 import（依存任意）。
起動例: uvicorn で create_app(...) を提供する ASGI を立て、Google の redirect_uri を /oauth2/callback に。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from aiia.auth.oauth_flow import OAuthConsentFlow
from aiia.auth.token_store import TokenStore
from aiia.connect_web.callback import process_callback


def _html(title: str, detail: str) -> str:
    return (
        "<html><head><meta charset='utf-8'></head>"
        "<body style='font-family:sans-serif;max-width:560px;margin:48px auto;text-align:center'>"
        f"<h2>{title}</h2><p style='color:#555'>{detail}</p></body></html>"
    )


def create_app(
    *, redirect_uri: str, store: TokenStore, exchange: Optional[Callable[[str], Any]] = None
) -> Any:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    _exchange = exchange or OAuthConsentFlow(redirect_uri).exchange
    app = FastAPI(title="AI-IA-UAE Connect")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/oauth2/callback", response_class=HTMLResponse)
    def callback(
        code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None
    ) -> Any:
        r = process_callback(code=code, state=state, error=error, store=store, exchange=_exchange)
        return HTMLResponse(content=_html(r.title, r.detail), status_code=200 if r.ok else 400)

    return app
