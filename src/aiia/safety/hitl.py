"""誤送信防止ガード。

- 既定（pipeline/バッチ）: NeverSendGate＝送信系を**常に Deny**。読取/下書き/ラベルのみ。
- 例外（M2 Slack送信のみ）: SendConfirmationGate＝**2段人間確認**を経た時だけ drafts.send を許可。
  確認は user+draft 束縛・短命(TTL)・単回(nonce)。これ以外の経路では送信は構造的に不可能。

二重防御:
 - Claude Agent SDK 経路: can_use_tool として使用（送信系を Deny）。
 - pipeline / delivery 経路: assert_no_send() で送信系到達時に例外。
"""
from __future__ import annotations

from dataclasses import dataclass

# 送信遮断/許可セットは registry に集約（単一真実源）。ここは re-import して後方互換を保つ。
from aiia.mcp.registry import ALLOWED_TOOLS, SEND_TOOLS

__all__ = [
    "ALLOWED_TOOLS",
    "SEND_TOOLS",
    "GateDecision",
    "AutoSendError",
    "NeverSendGate",
    "assert_no_send",
    "SendConfirmation",
    "SendConfirmationGate",
]


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


@dataclass(frozen=True)
class SendConfirmation:
    """2段確認を経た送信許可の証。user+draft 束縛・issued_at(epoch秒)・単回 nonce。"""

    user_id: str
    draft_id: str
    issued_at: float
    nonce: str


class SendConfirmationGate:
    """2段人間確認を経た drafts.send だけを通すゲート（M2のSlack送信専用）。

    確認は本人・対象下書きに一致し、TTL内・未使用 nonce の時だけ有効。検証で nonce を消費（単回）。
    now / nonce は呼び出し側が注入（テスト容易・time/uuid非依存）。
    """

    def __init__(self, *, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._used: set[str] = set()

    def mint(self, *, user_id: str, draft_id: str, now: float, nonce: str) -> SendConfirmation:
        return SendConfirmation(user_id=user_id, draft_id=draft_id, issued_at=now, nonce=nonce)

    def check(
        self, confirmation: SendConfirmation | None, *, user_id: str, draft_id: str, now: float
    ) -> GateDecision:
        if confirmation is None:
            return GateDecision(False, "2段確認がありません（送信不可）")
        if confirmation.user_id != user_id or confirmation.draft_id != draft_id:
            return GateDecision(False, "確認が対象（本人/下書き）と一致しません")
        if now - confirmation.issued_at > self._ttl:
            return GateDecision(False, "確認の有効期限切れ（取り直してください）")
        if confirmation.nonce in self._used:
            return GateDecision(False, "確認は使用済み（単回のみ有効）")
        self._used.add(confirmation.nonce)  # 単回消費（リプレイ防止）
        return GateDecision(True, "2段確認済み")

    def authorize_send(
        self, confirmation: SendConfirmation | None, *, user_id: str, draft_id: str, now: float
    ) -> None:
        decision = self.check(confirmation, user_id=user_id, draft_id=draft_id, now=now)
        if not decision.allow:
            raise AutoSendError(f"送信ゲート拒否: {decision.reason}")
