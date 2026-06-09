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
from aiia.profile.providers import build_grounding_provider, build_style_provider
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
    channel_map: Optional[dict[str, str]] = None,  # client名→Slack channel_id（grounding）
    max_budget_usd: Optional[float] = None,  # per-user/日 のコスト上限
    personalize: bool = True,  # M3: 文体/grounding を有効化（LLMがsummarize_text対応かつslackありの時）
    reminder_store: Any = None,  # Phase2: 返信リマインド state（指定時のみ計算）
) -> list[UserResult]:
    platform = platform or load_platform(config_dir)
    agent = load_agent_config("morning_email", config_dir)
    emails = store.list_emails()
    style_cache: dict[str, str] = {}  # run 内で per-user 文体を1回だけ構築

    def _one(email: str) -> UserResult:
        try:
            token = store.get(email)
            if token is None:
                return UserResult(email, ok=False, error="token_missing")
            ucfg = load_user(email, config_dir)  # config/users/<email>.yaml or 既定(user_id=email)
            # display_name を Slack 実名で補完（本人名指し昇格に使用）。best-effort・失敗時は既定のまま。
            if slack is not None and hasattr(slack, "display_name_for_email") and (
                not ucfg.display_name or ucfg.display_name == "（ユーザー名）"
            ):
                try:
                    dn = slack.display_name_for_email(email)
                    if dn:
                        ucfg.display_name = dn
                except Exception:  # noqa: BLE001 — 補完失敗で本処理は止めない
                    pass
            service = gmail_service_factory(email) if gmail_service_factory else None
            tools = WorkspaceGmailToolset.from_token(token, service=service)
            llm = build_llm(platform)

            # M3: 文体/grounding provider（LLMが要約対応かつslackありの時のみ）
            style_provider = None
            grounding_provider = None
            if personalize and slack is not None:
                summarize = getattr(llm, "summarize_text", None)
                if summarize is not None:
                    suid = slack.user_id_for_email(email) if hasattr(slack, "user_id_for_email") else None
                    style_provider = build_style_provider(
                        gmail=tools.gmail, summarize=summarize, slack=slack, slack_user_id=suid,
                        cache=style_cache,
                    )
                grounding_provider = build_grounding_provider(slack=slack, channel_map=channel_map or {})

            res = run(
                PipelineDeps(
                    llm=llm, tools=tools, user=ucfg, agent=agent,
                    audit=AuditLog(email), dry_run=dry_run,
                    style_provider=style_provider, grounding_provider=grounding_provider,
                    max_budget_usd=max_budget_usd, reminder_store=reminder_store,
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
