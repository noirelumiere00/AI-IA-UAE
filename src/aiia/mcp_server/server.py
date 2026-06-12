"""§Q aiia-mcp サーバ本体：AiLaのメール能力を MCP tool として公開（OpenClaw が叩く境界）。

不変条件（teamagent mcp_gateway STRICT と同型）:
- 外殻は `_user_context.slack_user_id` のみ渡す。email はサーバが resolver で確定し、
  **外殻申告の email は一切採らない**（なりすまし防止）。
- 本人の Google token はサーバが store から取得＝外殻はトークン/Gmailに触れない。
- 解決不能/未連携は副作用なくメッセージで返す（他人のメールは絶対に出さない）。

DI: resolver(slack_user_id→email) と digest_fn(email→Digest) を注入＝Slack/Gmail無しでテスト可能。
セキュリティ中核(build_digest_payload/resolver)は mcp 非依存＝軽量テスト可。mcp は build_aiia_server で遅延import。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from aiia.delivery import render_digest_text, render_slack_blocks
from aiia.mcp_server.resolver import EmailResolver, resolve_requester_email
from aiia.schemas import Digest

logger = logging.getLogger(__name__)

# digest_fn(email) -> Digest（本人のメールダイジェスト生成）。
DigestFn = Callable[[str], Digest]

_DIGEST_TOOL_NAME = "aiia_mail_digest"
_DIGEST_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "_user_context": {
            "type": "object",
            "description": "呼び出し元コンテキスト。slack_user_id のみ有効＝サーバが本人emailを確定。",
            "properties": {"slack_user_id": {"type": "string"}},
        }
    },
}
_DIGEST_TOOL_DESC = (
    "本人の今朝のメールサマリー（要対応/未返信/今日の予定）を生成して返す。"
    "本人は呼び出し元のSlackユーザーから**サーバが確定**する（他人のメールは取得不可）。"
    "返り値は Slack Block Kit(blocks) と text。"
)


def _not_connected_payload(reason: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "reason": reason,
            "text": "メール連携がまだのようです。『連携して』と話しかけると連携リンクをお送りします。",
        },
        ensure_ascii=False,
    )


def build_digest_payload(
    arguments: dict[str, Any], *, resolver: EmailResolver, digest_fn: DigestFn
) -> str:
    """tool 本体（同期・テスト可能・mcp非依存）：slack_user_id→本人email確定→ダイジェスト→JSON。

    外殻申告の email は読まない（STRICT）。未連携/解決不能/token無は副作用なくメッセージ返却＝
    **他人のメールは絶対に出さない**。
    """
    email = resolve_requester_email(arguments.get("_user_context"), resolver=resolver)
    if not email:
        logger.info("aiia_digest_identity_unresolved")
        return _not_connected_payload("identity_unresolved")
    try:
        digest: Digest = digest_fn(email)
    except ValueError as exc:
        if str(exc) == "token_missing":
            return _not_connected_payload("token_missing")
        raise
    payload = {
        "ok": True,
        "blocks": render_slack_blocks(digest),
        "text": render_digest_text(digest),
        "processed": digest.processed,
    }
    logger.info("aiia_digest_done processed=%s", digest.processed)
    return json.dumps(payload, ensure_ascii=False)


def build_aiia_server(
    *, resolver: EmailResolver, digest_fn: DigestFn, name: str = "aiia-mcp"
) -> Any:
    """aiia-mcp の MCP Server を構築（resolver/digest_fn 注入）。mcp は遅延 import（本番のみ要）。"""
    import asyncio

    from mcp.server import Server
    from mcp.types import TextContent, Tool

    server: Server = Server(name)

    @server.list_tools()  # type: ignore[no-untyped-call,misc]
    async def _list_tools() -> list[Tool]:
        return [Tool(name=_DIGEST_TOOL_NAME, description=_DIGEST_TOOL_DESC, inputSchema=_DIGEST_TOOL_SCHEMA)]

    @server.call_tool()  # type: ignore[no-untyped-call,misc]
    async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if tool_name != _DIGEST_TOOL_NAME:
            return [TextContent(type="text", text=json.dumps({"ok": False, "reason": "unknown_tool"}))]
        # pipeline.run は同期I/O＝event loop を塞がぬよう別スレッドで本体ロジックを回す。
        text = await asyncio.to_thread(
            build_digest_payload, arguments, resolver=resolver, digest_fn=digest_fn
        )
        return [TextContent(type="text", text=text)]

    return server
