"""§Q 1ユーザー分のメールダイジェスト生成（配信せず Digest を返す）。

`orchestrator/multi._one` の中核（token→Gmail toolset→LLM→pipeline.run）を、配信抜き・1人分で
再利用する薄いヘルパ。MCP tool（aiia_mail_digest）と 朝配信(batch) の両方から呼べる。
本人の Google token は **store(email) からサーバが取得**＝外殻はトークンに触れない。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from aiia.auth.token_store import TokenStore
from aiia.config import load_agent_config, load_platform, load_user
from aiia.mcp.workspace_gmail import WorkspaceGmailToolset
from aiia.pipeline import PipelineDeps, run
from aiia.providers import build_llm
from aiia.safety.audit import AuditLog
from aiia.schemas import Digest


def digest_for_email(
    email: str,
    *,
    store: TokenStore,
    platform: Any = None,
    config_dir: Optional[Path] = None,
    dry_run: bool = True,
    create_drafts: bool = False,
    max_budget_usd: Optional[float] = 1.0,
    gmail_service: Any = None,  # テスト用に Gmail service 注入
) -> Digest:
    """連携済み本人のメールダイジェストを生成して返す（配信はしない）。

    既定 dry_run=True/create_drafts=False＝オンデマンド表示用（Gmailに副作用を作らない）。
    朝配信は create_drafts=True 等で呼び分け。token 無し＝例外（上位で隔離）。
    """
    token = store.get(email)
    if token is None:
        raise ValueError("token_missing")
    platform = platform or load_platform(config_dir)
    agent = load_agent_config("morning_email", config_dir)
    ucfg = load_user(email, config_dir)
    tools = WorkspaceGmailToolset.from_token(token, service=gmail_service)
    llm = build_llm(platform)
    res = run(
        PipelineDeps(
            llm=llm,
            tools=tools,
            user=ucfg,
            agent=agent,
            audit=AuditLog(email),
            dry_run=dry_run,
            create_drafts=create_drafts,
            max_budget_usd=max_budget_usd,
        )
    )
    return res.digest
