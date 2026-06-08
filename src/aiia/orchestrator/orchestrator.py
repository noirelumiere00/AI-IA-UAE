"""朝メールAgent の起動入口。設定3層を読み、LLM と道具(MCPToolset)を組み、pipeline を回す。

Phase1 の道具は FakeMCPToolset（実 Gmail/Slack に触れない）。S9 で harness / per-user OAuth に差替。
配信:
 - dry_run=True: 何も送らない（CLI が render_digest_text を stdout に出す）。
 - dry_run=False: Slack に「下書き/プレビュー」として送付（send_draft のみ＝実送信しない）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from aiia.config import load_agent_config, load_platform, load_user
from aiia.delivery import render_digest_text, render_slack_blocks
from aiia.mcp.base import MCPToolset
from aiia.mcp.fake import FakeMCPToolset
from aiia.pipeline import PipelineDeps, run
from aiia.providers import build_llm
from aiia.safety.audit import AuditLog
from aiia.schemas import AgentResult


def run_morning_email(
    *,
    user: Optional[str] = None,
    dry_run: bool = True,
    profile: Optional[str] = None,
    config_dir: Optional[Path] = None,
    tools: Optional[MCPToolset] = None,
    audit_path: Optional[Path] = None,
) -> AgentResult:
    platform = load_platform(config_dir)
    if profile:
        platform.default_profile = profile
    agent = load_agent_config("morning_email", config_dir)
    ucfg = load_user(user, config_dir)

    llm = build_llm(platform)
    # Phase1 の道具はニセ受信箱（FakeMCPToolset）。S9 で実 Gmail(per-user OAuth) に差替。
    toolset: MCPToolset = tools if tools is not None else FakeMCPToolset()
    audit = AuditLog(ucfg.user_id, path=audit_path)

    res = run(
        PipelineDeps(llm=llm, tools=toolset, user=ucfg, agent=agent, audit=audit, dry_run=dry_run)
    )

    if not dry_run:
        # Slack へは「下書き/プレビュー」のみ（send_draft）。実送信メソッドは存在しない。
        toolset.slack.send_draft(
            channel=ucfg.slack_user_id or "me",
            blocks=render_slack_blocks(res.digest),
            text=render_digest_text(res.digest),
        )
    return res
