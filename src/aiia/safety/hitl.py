"""誤送信防止ガード（NeverSendGate）。外部送信系を遮断、読取/下書き/ラベルのみ許可。

二重防御:
 - Claude Agent SDK 経路: can_use_tool として使用（送信系を Deny）。
 - pipeline / delivery 経路: assert_no_send() で送信系到達時に例外。
"""
from __future__ import annotations

from dataclasses import dataclass

# 外部送信系（常に遮断）
SEND_TOOLS = {
    "mcp__Gmail__send_message",
    "mcp__Gmail__send_draft",
    "mcp__Slack__slack_send_message",
    "mcp__Slack__slack_schedule_message",
}

# 許可（読取・下書き・ラベル）
ALLOWED_TOOLS = {
    "mcp__Gmail__search_threads",
    "mcp__Gmail__get_thread",
    "mcp__Gmail__list_drafts",
    "mcp__Gmail__list_labels",
    "mcp__Gmail__create_label",
    "mcp__Gmail__create_draft",
    "mcp__Gmail__label_thread",
    "mcp__Gmail__label_message",
    "mcp__Slack__slack_send_message_draft",
}


@dataclass
class GateDecision:
    allow: bool
    reason: str = ""


class AutoSendError(RuntimeError):
    pass


class NeverSendGate:
    """送信系ツールを常に Deny。"""

    def __call__(self, tool_name: str, tool_input: dict | None = None) -> GateDecision:
        if tool_name in SEND_TOOLS:
            return GateDecision(False, f"外部送信は禁止（人間が最終送信）: {tool_name}")
        return GateDecision(True)


def assert_no_send(tool_name: str) -> None:
    if tool_name in SEND_TOOLS:
        raise AutoSendError(f"NeverSendGate: 送信系ツールは呼べません: {tool_name}")
