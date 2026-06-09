"""M3 本番 provider 配線：per-user の StyleProfile / grounding を pipeline の provider に変換。

multi.py が per-user(本人のgmail/slack)で生成し PipelineDeps(style_provider=, grounding_provider=) へ。
- 文体: 本人の送信メール(list_sent) ＋(任意)本人のSlack発言 → summarize(Bedrock haiku) → 文体記述。cacheで日次1回。
- grounding: 対象メール→関連Slackチャンネル特定→直近メッセージ。
1人分の失敗(API等)は None に握りつぶし、素のdraftにフォールバック（堅牢性）。
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Optional

from aiia.config import UserConfig
from aiia.profile.grounding import find_related_slack
from aiia.profile.style import build_style_profile
from aiia.schemas import EmailThread


def build_style_provider(
    *,
    gmail: Any,  # WorkspaceGmail（list_sent を持つ）
    summarize: Callable[[str, str], str],  # AnthropicLLM.summarize_text 等
    slack: Any = None,  # SlackDelivery（recent_messages・任意）
    slack_channel: Optional[str] = None,
    slack_user_id: Optional[str] = None,
    cache: Optional[dict[str, str]] = None,
    max_sent: int = 20,
    max_slack: int = 20,
) -> Callable[[UserConfig], Optional[str]]:
    store: dict[str, str] = cache if cache is not None else {}

    def provider(user: UserConfig) -> Optional[str]:
        if user.user_id in store:
            return store[user.user_id] or None
        try:
            sent = gmail.list_sent(max_results=max_sent)
        except Exception:  # noqa: BLE001 - 文体抽出失敗は素draftにフォールバック
            sent = []
        slack_msgs: Sequence[str] = []
        if slack is not None and slack_channel and slack_user_id:
            try:
                slack_msgs = slack.recent_messages(slack_channel, user_id=slack_user_id, limit=max_slack)
            except Exception:  # noqa: BLE001
                slack_msgs = []
        sp = build_style_profile(user.user_id, sent, slack_msgs, summarize=summarize)
        store[user.user_id] = sp.descriptor
        return sp.descriptor or None

    return provider


def build_grounding_provider(
    *, slack: Any, channel_map: dict[str, str], limit: int = 20
) -> Callable[[EmailThread], Optional[str]]:
    def provider(thread: EmailThread) -> Optional[str]:
        if not channel_map:
            return None
        try:
            return find_related_slack(thread, channel_map=channel_map, slack=slack, limit=limit)
        except Exception:  # noqa: BLE001 - grounding失敗は素draftにフォールバック
            return None

    return provider
