"""Slack ユーザートークン(xoxp)での読み取り（未返信メンション検知用）。

`search.messages` / `conversations.replies` は **user token 専用**。bot の `SlackDelivery` とは用途分離。
slack_sdk は遅延 import・client DI 可（テスト課金ゼロ）。各人の xoxp は KMS 暗号化保管され、
本人のメンションだけを本人のトークンで参照する（per-user 束縛・投稿はしない＝読取専用）。
"""

from __future__ import annotations

import os
from typing import Any, Optional

# search.messages の「自分宛メンション」クエリ。Slack の検索演算子は API ドキュメントで保証されないため
# bare token を第一候補とし、**実機検証後にここだけ差し替える**（定数化＝1箇所修正で済む）。
MENTION_QUERY = "<@{uid}>"


class SlackUserClient:
    """xoxp(user token) で本人宛メンションと未返信を読む読取専用クライアント。"""

    def __init__(self, *, token: Optional[str] = None, client: Any = None) -> None:
        self._token = token or os.environ.get("SLACK_USER_TOKEN")
        self._client = client

    def _wc(self) -> Any:
        if self._client is None:
            from slack_sdk import WebClient

            self._client = WebClient(token=self._token)
        return self._client

    def search_mentions(self, slack_user_id: str, *, count: int = 100) -> list[dict]:
        """本人宛メンションの直近メッセージ一覧。

        各 dict: channel_id/channel_name/is_im/ts/thread_ts/text/permalink/user/username。
        search 不可（無料プラン外/権限/API失敗）は空リスト＝Slackリマインドは丸ごと skip。
        """
        query = MENTION_QUERY.format(uid=slack_user_id)
        try:
            resp = self._wc().search_messages(
                query=query, sort="timestamp", sort_dir="desc", count=count
            )
        except Exception:  # noqa: BLE001 — search不可はSlackリマインド全体を諦める（メールは別経路）
            return []
        if not resp.get("ok"):
            return []
        matches = (resp.get("messages", {}) or {}).get("matches", []) or []
        out: list[dict] = []
        for m in matches:
            ch = m.get("channel", {}) or {}
            out.append(
                {
                    "channel_id": ch.get("id", "") or "",
                    "channel_name": ch.get("name", "") or "",
                    "is_im": bool(ch.get("is_im") or ch.get("is_mpim")),
                    "ts": m.get("ts", "") or "",
                    "thread_ts": m.get("thread_ts") or m.get("ts", "") or "",
                    "text": m.get("text", "") or "",
                    "permalink": m.get("permalink", "") or "",
                    "user": m.get("user", "") or "",
                    "username": m.get("username", "") or "",
                }
            )
        return out

    def has_user_replied_after(
        self, channel: str, thread_ts: str, slack_user_id: str, after_ts: str
    ) -> Optional[bool]:
        """thread_ts のスレッドで after_ts より後に本人の発言があるか。

        判定不能（スコープ不足/権限/API失敗/不正ts）は **None**（呼び出し側で fail-closed＝催促しない）。
        """
        try:
            resp = self._wc().conversations_replies(channel=channel, ts=thread_ts, limit=200)
        except Exception:  # noqa: BLE001 — missing_scope/not_in_channel 等は判定不能扱い
            return None
        if not resp.get("ok"):
            return None
        try:
            after = float(after_ts)
        except (TypeError, ValueError):
            return None
        for m in resp.get("messages", []) or []:
            if m.get("user") != slack_user_id:
                continue
            try:
                if float(m.get("ts", "0")) > after:
                    return True
            except (TypeError, ValueError):
                continue
        return False
