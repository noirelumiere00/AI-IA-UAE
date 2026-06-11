"""Slack ユーザートークン(xoxp)での読み取り（未返信メンション検知用）。

`search.messages` / `conversations.replies` は **user token 専用**。bot の `SlackDelivery` とは用途分離。
slack_sdk は遅延 import・client DI 可（テスト課金ゼロ）。各人の xoxp は KMS 暗号化保管され、
本人のメンションだけを本人のトークンで参照する（per-user 束縛・投稿はしない＝読取専用）。
"""

from __future__ import annotations

import os
from typing import Any, Optional

# search.messages の「自分宛メンション」クエリ。**実機検証済み**（s-komataのxoxpで `<@{uid}>` が
# 自分宛メンションを正しく返すことを確認・total多数ヒット）。定数化＝将来調整は1箇所で済む。
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
        """メンション(after_ts)より後に、本人がそのチャンネル/スレッドで発言（=対応）したか。

        実データ上、営業チャンネルの返信はスレッド外（チャンネル直下）が多く、スレッドだけ見ると
        「返信済みなのに未返信」と誤検知する。そこで **conversations.history（チャンネル直下）を主判定**に、
        スレッド型は conversations.replies も併せて見て engagement を判定する。
        - True  = after_ts 以降に本人の発言あり（対応済み → 催促しない）
        - False = 取得できた上で本人の発言なし（真に未返信 → 催促候補）
        - None  = channel/thread の両取得が不能（scope不足/権限/不正ts）→ fail-closed で催促しない
        """
        try:
            after = float(after_ts)
        except (TypeError, ValueError):
            return None

        def _posted_after(messages: Any) -> bool:
            for m in messages or []:
                if m.get("user") != slack_user_id:
                    continue
                try:
                    if float(m.get("ts", "0")) > after:
                        return True
                except (TypeError, ValueError):
                    continue
            return False

        checked = False
        # 1) チャンネル直下：メンション以降に本人が何か投稿したか（非スレッド対応の主判定）
        try:
            h = self._wc().conversations_history(channel=channel, oldest=after_ts, limit=200)
            if h.get("ok"):
                checked = True
                if _posted_after(h.get("messages")):
                    return True
        except Exception:  # noqa: BLE001 — missing_scope/not_in_channel 等は判定不能扱い
            pass
        # 2) スレッド型メンションは返信がスレッドに入るので併せて確認
        if thread_ts and thread_ts != after_ts:
            try:
                r = self._wc().conversations_replies(channel=channel, ts=thread_ts, limit=200)
                if r.get("ok"):
                    checked = True
                    if _posted_after(r.get("messages")):
                        return True
            except Exception:  # noqa: BLE001
                pass
        return False if checked else None
