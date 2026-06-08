"""誤送信防止ガード（NeverSendGate）。外部送信系を遮断、読取/下書き/ラベルのみ許可。

二重防御:
 - Claude Agent SDK 経路: can_use_tool として使用（送信系を Deny）。
 - pipeline / delivery 経路: assert_no_send() で送信系到達時に例外。
"""
from __future__ import annotations

from dataclasses import dataclass

# 送信遮断/許可セットは registry に集約（単一真実源）。ここは re-import して後方互換を保つ。
from aiia.mcp.registry import ALLOWED_TOOLS, SEND_TOOLS

__all__ = ["ALLOWED_TOOLS", "SEND_TOOLS", "GateDecision", "AutoSendError", "NeverSendGate", "assert_no_send"]


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
