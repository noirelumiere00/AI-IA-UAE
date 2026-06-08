"""delivery 層: Digest を表示形式（テキスト / Slack Block Kit）に組み立てる純粋関数群。"""
from __future__ import annotations

from aiia.delivery.slack import render_digest_text, render_slack_blocks

__all__ = ["render_digest_text", "render_slack_blocks"]
