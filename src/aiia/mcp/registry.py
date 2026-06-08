"""ツール名・送信遮断/許可セット・ラベル名の「単一真実源」(single source of truth)。

他モジュール（safety/hitl・mcp・pipeline）はここから import する。値をあちこちで二重定義すると
「送信禁止リストの片方だけ直し忘れ」のような事故が起きるため、ここ1か所に集約する。

最下層モジュール（他の aiia.* を import しない）＝循環 import が起きない安全な土台。
"""
from __future__ import annotations

# ── Gmail ツール名（mcp__<server>__<tool>） ───────────────────────────────
GMAIL_SEARCH_THREADS = "mcp__Gmail__search_threads"
GMAIL_GET_THREAD = "mcp__Gmail__get_thread"
GMAIL_LIST_DRAFTS = "mcp__Gmail__list_drafts"
GMAIL_LIST_LABELS = "mcp__Gmail__list_labels"
GMAIL_CREATE_LABEL = "mcp__Gmail__create_label"
GMAIL_CREATE_DRAFT = "mcp__Gmail__create_draft"
GMAIL_LABEL_THREAD = "mcp__Gmail__label_thread"
GMAIL_LABEL_MESSAGE = "mcp__Gmail__label_message"
GMAIL_SEND_MESSAGE = "mcp__Gmail__send_message"  # ← 送信系（遮断）
GMAIL_SEND_DRAFT = "mcp__Gmail__send_draft"      # ← 送信系（遮断）

# ── Slack ツール名 ────────────────────────────────────────────────────────
SLACK_SEND_DRAFT = "mcp__Slack__slack_send_message_draft"   # 下書き（許可）
SLACK_SEND_MESSAGE = "mcp__Slack__slack_send_message"       # 実送信（遮断）
SLACK_SCHEDULE_MESSAGE = "mcp__Slack__slack_schedule_message"  # 予約送信（遮断）

# ── 外部送信系（常に遮断＝NeverSendGate の対象） ──────────────────────────
SEND_TOOLS: frozenset[str] = frozenset(
    {GMAIL_SEND_MESSAGE, GMAIL_SEND_DRAFT, SLACK_SEND_MESSAGE, SLACK_SCHEDULE_MESSAGE}
)

# ── 許可（読取・下書き作成・ラベル付け のみ） ─────────────────────────────
ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        GMAIL_SEARCH_THREADS,
        GMAIL_GET_THREAD,
        GMAIL_LIST_DRAFTS,
        GMAIL_LIST_LABELS,
        GMAIL_CREATE_LABEL,
        GMAIL_CREATE_DRAFT,
        GMAIL_LABEL_THREAD,
        GMAIL_LABEL_MESSAGE,
        SLACK_SEND_DRAFT,
    }
)

# ── ラベル ────────────────────────────────────────────────────────────────
LABEL_PREFIX = "AIIA/"


def label_for(category: str) -> str:
    """カテゴリ名 → Gmail ラベル名（例: CLIENT_URGENT → AIIA/CLIENT_URGENT）。"""
    return f"{LABEL_PREFIX}{category}"
