"""グラウンディング：対象メールに関連する Slack チャンネルを特定し、直近の文脈を下書きへ。

`channel_map`＝{キーワード(小文字): channel_id}（クライアント名/ドメイン→社内チャンネル）。
件名＋送信元にキーワードが含まれれば、そのチャンネルの直近メッセージを redaction して文脈文字列に。
一致が無ければ None（grounding 無しで素のdraft）。`slack` は recent_messages(channel, limit) を持つ。
"""
from __future__ import annotations

from typing import Any, Optional

from aiia.safety.redaction import redact
from aiia.schemas import EmailThread


def find_related_slack(
    thread: EmailThread,
    *,
    channel_map: dict[str, str],
    slack: Any,
    limit: int = 20,
    max_chars: int = 280,
) -> Optional[str]:
    haystack = f"{thread.subject} {thread.sender}".lower()
    for keyword, channel in channel_map.items():
        kw = keyword.strip().lower()
        if not kw or kw not in haystack:
            continue
        msgs = slack.recent_messages(channel, limit=limit)
        if not msgs:
            continue
        body = "\n".join(f"- {redact(m)[:max_chars]}" for m in msgs[:limit])  # redaction後に文脈化
        return f"#{channel}（関連チャンネル）:\n{body}"
    return None
