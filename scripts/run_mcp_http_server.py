#!/usr/bin/env python
"""§Q aiia-mcp を streamable-http で起動（OpenClaw ⟷ メール境界のトランスポート）。

なぜ HTTP か：OpenClaw の stdio MCP は子プロセス同居＝外殻のIAM/ネットワークを共有し
「外殻はトークン/Gmailに直接触れない」不変条件を破る。よって本番は別コンテナ＋
streamable-http（私設ネットワーク・bearer・内部のみ）で接続する（teamagent と同型）。

本番 resolver(Slack bot token)＋本番 digest(DynamoDB token store) を組み立てる。
環境変数:
- AIIA_MCP_BEARER : 必須。無いと **fail-closed 起動拒否**（無認証公開禁止）。
- AIIA_MCP_HOST/PORT/PATH : 既定 127.0.0.1 / 8788 / /mcp（OpenClaw url と一致させる）。
- SLACK_BOT_TOKEN : resolver(users.info)用。AIIA_DDB_TABLE / OAUTH_KMS_KEY_ID : token store用。
"""
from __future__ import annotations

import contextlib
import hmac
import os
import sys
from collections.abc import AsyncIterator
from typing import Any

import structlog
import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

logger = structlog.get_logger(__name__)


class BearerAuthMiddleware:
    """純 ASGI bearer 認証。保護パス配下は一致しなければ 401（/healthz は対象外）。"""

    def __init__(self, app: Any, *, token: str, protect_prefix: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}"
        self.protect_prefix = protect_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and str(scope.get("path", "")).startswith(self.protect_prefix):
            headers = dict(scope.get("headers") or [])
            presented = headers.get(b"authorization", b"").decode("latin-1")
            if not (presented and hmac.compare_digest(presented, self._expected)):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def _build_production_server() -> Any:
    """本番 resolver(Slack bot)＋本番 digest(DynamoDB token store) で aiia-mcp Server を組む。"""
    from aiia.auth.token_store import DynamoDbTokenStore, KmsCipher
    from aiia.mcp_server.digest import digest_for_email
    from aiia.mcp_server.resolver import slack_resolver_from_bot_token
    from aiia.mcp_server.server import build_aiia_server

    cipher = KmsCipher(os.environ["OAUTH_KMS_KEY_ID"])
    store = DynamoDbTokenStore(os.environ["AIIA_DDB_TABLE"], cipher)
    resolver = slack_resolver_from_bot_token()  # SLACK_BOT_TOKEN

    def _digest(email: str) -> Any:
        return digest_for_email(email, store=store)

    return build_aiia_server(resolver=resolver, digest_fn=_digest)


def build_app(*, bearer: str, path: str) -> Starlette:
    server = _build_production_server()
    session_manager = StreamableHTTPSessionManager(app=server, json_response=False, stateless=False)

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            logger.info("aiia_mcp_http_started", path=path)
            yield

    return Starlette(
        routes=[Route("/healthz", healthz), Mount(path, app=handle_mcp)],
        middleware=[Middleware(BearerAuthMiddleware, token=bearer, protect_prefix=path)],
        lifespan=lifespan,
    )


def main() -> None:
    bearer = os.environ.get("AIIA_MCP_BEARER")
    if not bearer:
        logger.error("aiia_mcp_no_bearer", hint="set AIIA_MCP_BEARER")
        sys.exit(2)
    host = os.environ.get("AIIA_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("AIIA_MCP_PORT", "8788"))
    path = os.environ.get("AIIA_MCP_PATH", "/mcp")
    uvicorn.run(build_app(bearer=bearer, path=path), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
