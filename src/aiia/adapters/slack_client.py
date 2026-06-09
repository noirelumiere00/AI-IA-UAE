"""Slack DM 配信（朝ダイジェストを本人DMへ）。slack_sdk は遅延 import・client DI可（テスト課金ゼロ）。

email→user_id は `users.lookupByEmail`（要 users:read.email）。DM は conversations.open→chat.postMessage。
配信は bot token のAPI呼びだけ＝常駐不要（対話ボタンの常駐アプリは M2）。
"""
from __future__ import annotations

import os
from typing import Any, Optional


class SlackDelivery:
    def __init__(self, *, token: Optional[str] = None, client: Any = None) -> None:
        self._token = token or os.environ.get("SLACK_BOT_TOKEN")
        self._client = client

    def _wc(self) -> Any:
        if self._client is None:
            from slack_sdk import WebClient

            self._client = WebClient(token=self._token)
        return self._client

    def user_id_for_email(self, email: str) -> Optional[str]:
        resp = self._wc().users_lookupByEmail(email=email)
        if resp.get("ok"):
            return str(resp["user"]["id"])
        return None

    def display_name_for_email(self, email: str) -> Optional[str]:
        """Slack profile の実名（本人名指し昇格に使う）。取得不可なら None。"""
        resp = self._wc().users_lookupByEmail(email=email)
        if resp.get("ok"):
            u = resp["user"]
            prof = u.get("profile", {}) or {}
            name = prof.get("real_name_normalized") or prof.get("real_name") or u.get("real_name")
            return str(name) if name else None
        return None

    def open_dm(self, user_id: str) -> Optional[str]:
        resp = self._wc().conversations_open(users=user_id)
        if resp.get("ok"):
            return str(resp["channel"]["id"])
        return None

    def recent_messages(
        self, channel: str, *, user_id: Optional[str] = None, limit: int = 50
    ) -> list[str]:
        """チャンネル直近メッセージのテキスト。user_id 指定で本人発言のみ（文体学習用）・
        未指定で全員（grounding=チャンネル文脈用）。要 channels:history/groups:history。"""
        resp = self._wc().conversations_history(channel=channel, limit=limit)
        if not resp.get("ok"):
            return []
        out: list[str] = []
        for m in resp.get("messages", []):
            if user_id is not None and m.get("user") != user_id:
                continue
            text = m.get("text")
            if text:
                out.append(text)
        return out

    def send_digest(self, *, email: str, blocks: list, text: str) -> bool:
        """本人DMへ朝ダイジェストを送る。email解決/DM open/送信のいずれか失敗で False。"""
        uid = self.user_id_for_email(email)
        if not uid:
            return False
        channel = self.open_dm(uid)
        if not channel:
            return False
        resp = self._wc().chat_postMessage(channel=channel, blocks=blocks, text=text)
        return bool(resp.get("ok"))
