"""配信レンダラ（テキスト / Slack Block Kit）。WF-1/WF-2 + Slack制約。"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from aiia.delivery import render_digest_text, render_slack_blocks
from aiia.mcp.fake import FakeMCPToolset
from aiia.pipeline import PipelineDeps, run
from aiia.schemas import Digest


def test_text_has_header_categories_and_unsent(
    make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset
) -> None:
    txt = render_digest_text(run(make_deps(tools, dry_run=True)).digest)
    assert "朝のダイジェスト" in txt
    assert "🔴 CLIENT_URGENT" in txt
    assert "未送信" in txt  # 下書きは未送信である旨


def test_blocks_within_limit_and_header(
    make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset
) -> None:
    blocks = render_slack_blocks(run(make_deps(tools, dry_run=True)).digest)
    assert isinstance(blocks, list)
    assert len(blocks) <= 49  # Block Kit 制約
    assert blocks[0]["type"] == "header"


def test_empty_digest_quiet_morning() -> None:
    d = Digest(generated_at=datetime(2026, 6, 8, 7, 0, tzinfo=timezone.utc), user_id="t")
    assert "静かな朝" in render_digest_text(d)
    blocks = render_slack_blocks(d)
    assert any("静かな朝" in str(b) for b in blocks)
