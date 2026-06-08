"""多人数オーケストレーション（40-50人）。

連携済み全ユーザーで朝ダイジェストを**並行生成**し、各自の **Slack DM** へ配信。
- 1人の失敗（トークン失効/revoke/Gmail/Slack API）は**隔離**してログ＋スキップ、他は完走。
- pipeline はユーザー間で状態共有ゼロ＝安全に並列（max_workers で総量規制）。
- per-user 監査。配信は dry_run=False かつ slack 指定時のみ（既定は実行＝下書き作成＋DM配信）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from aiia.auth.token_store import TokenStore
from aiia.config import load_agent_config, load_platform, load_user
from aiia.delivery import render_digest_text, render_slack_blocks
from aiia.mcp.workspace_gmail import WorkspaceGmailToolset
from aiia.pipeline import PipelineDeps, run
from aiia.providers import build_llm
from aiia.safety.audit import AuditLog


@dataclass
class UserResult:
    email: str
    ok: bool
    error: Optional[str] = None
    processed: int = 0
    drafts_created: int = 0
    delivered: bool = False


def run_for_all_users(
    *,
    store: TokenStore,
    platform: Any = None,
    config_dir: Optional[Path] = None,
    slack: Any = None,  # SlackDelivery（None なら配信せず）
    dry_run: bool = False,
    max_workers: int = 4,
    gmail_service_factory: Optional[Callable[[str], Any]] = None,  # テスト用に Gmail service 注入
) -> list[UserResult]:
    platform = platform or load_platform(config_dir)
    agent = load_agent_config("morning_email", config_dir)
    emails = store.list_emails()

    def _one(email: str) -> UserResult:
        try:
            token = store.get(email)
            if token is None:
                return UserResult(email, ok=False, error="token_missing")
            ucfg = load_user(email, config_dir)  # config/users/<email>.yaml or 既定(user_id=email)
            service = gmail_service_factory(email) if gmail_service_factory else None
            tools = WorkspaceGmailToolset.from_token(token, service=service)
            llm = build_llm(platform)
            res = run(
                PipelineDeps(
                    llm=llm, tools=tools, user=ucfg, agent=agent,
                    audit=AuditLog(email), dry_run=dry_run,
                )
            )
            delivered = False
            if slack is not None and not dry_run:
                delivered = slack.send_digest(
                    email=email,
                    blocks=render_slack_blocks(res.digest),
                    text=render_digest_text(res.digest),
                )
            return UserResult(
                email, ok=True, processed=res.digest.processed,
                drafts_created=res.drafts_created, delivered=delivered,
            )
        except Exception as exc:  # 1人の失敗は隔離（他ユーザーは継続）
            return UserResult(email, ok=False, error=type(exc).__name__)

    results: list[UserResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_one, e) for e in emails]
        for fut in as_completed(futures):
            results.append(fut.result())
    return sorted(results, key=lambda r: r.email)
