"""orchestrator のE2E（設定読込→pipeline→配信）。dry-run安全 / 非dry-runは下書きのみ。"""
from __future__ import annotations

from conftest import CONFIG_DIR

from aiia.mcp.fake import FakeMCPToolset
from aiia.orchestrator import run_morning_email


def test_dry_run_no_side_effects() -> None:
    tools = FakeMCPToolset()
    res = run_morning_email(user="example_user", dry_run=True, config_dir=CONFIG_DIR, tools=tools)
    assert res.dry_run is True
    assert tools.calls == []  # Slack送付すらしない
    assert res.digest.processed == 6


def test_live_sends_slack_draft_only() -> None:
    tools = FakeMCPToolset()
    res = run_morning_email(user="example_user", dry_run=False, config_dir=CONFIG_DIR, tools=tools)
    kinds = [c[0] for c in tools.calls]
    assert "send_draft" in kinds  # Slack へは下書き/プレビューのみ
    assert "create_draft" in kinds and "label_thread" in kinds
    assert not any("send_message" in k for k in kinds)  # 実送信は皆無
    assert res.drafts_created == 2
