"""連携 callback の FastAPI アプリ（薄いラッパ）。本番は ALB/API Gateway 背後で HTTPS 公開。

ロジックは callback.process_callback（純粋）に委譲。fastapi は遅延 import（依存任意）。
起動例: uvicorn で create_app(...) を提供する ASGI を立て、Google の redirect_uri を /oauth2/callback に。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from aiia.auth.oauth_flow import OAuthConsentFlow
from aiia.auth.token_store import TokenStore
from aiia.connect_web.callback import process_callback, process_reply


def _html(title: str, detail: str) -> str:
    return (
        "<html><head><meta charset='utf-8'></head>"
        "<body style='font-family:sans-serif;max-width:560px;margin:48px auto;text-align:center'>"
        f"<h2>{title}</h2><p style='color:#555'>{detail}</p></body></html>"
    )


def create_app(
    *,
    redirect_uri: str,
    store: TokenStore,
    exchange: Optional[Callable[[str], Any]] = None,
    reply_factory: Optional[Callable[[str, str], dict]] = None,
    slack_store: Optional[TokenStore] = None,
    slack_exchange: Optional[Callable[[str], Any]] = None,
    slack_redirect_uri: Optional[str] = None,
) -> Any:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, RedirectResponse

    _exchange = exchange or OAuthConsentFlow(redirect_uri).exchange
    if reply_factory is None:
        from aiia.handlers.reply_draft import make_reply_draft_factory

        reply_factory = make_reply_draft_factory(store)
    # Slack ユーザー認可(xoxp)を受ける場合のみ exchange を用意（slack_store が指定された時だけ /oauth2/slack/callback を生やす）。
    if slack_store is not None and slack_exchange is None:
        from aiia.auth.slack_oauth import SlackOAuthConsentFlow

        slack_exchange = SlackOAuthConsentFlow(slack_redirect_uri or redirect_uri).exchange
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

    @app.get("/reply")
    def reply(s: Optional[str] = None) -> Any:
        """[対応する]url-button：署名検証→全返信下書き作成→Gmailの該当スレッド(#all)へ302。"""
        r = process_reply(s=s, reply_factory=reply_factory)
        if not r.ok or not r.thread_id:
            return HTMLResponse(
                content=_html("開けませんでした", r.detail or "リンクが不正です"), status_code=400
            )
        url = f"https://mail.google.com/mail/u/0/#all/{r.thread_id}"
        return RedirectResponse(url=url, status_code=302)

    if slack_store is not None and slack_exchange is not None:
        _slack_store = slack_store
        _slack_exchange = slack_exchange

        @app.get("/oauth2/slack/callback", response_class=HTMLResponse)
        def slack_callback(
            code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None
        ) -> Any:
            """Slack ユーザー認可(xoxp)の callback。Google と同じ process_callback を slack_store で再利用。"""
            r = process_callback(
                code=code, state=state, error=error, store=_slack_store, exchange=_slack_exchange
            )
            return HTMLResponse(content=_html(r.title, r.detail), status_code=200 if r.ok else 400)

    return app
