"""朝メールAgent の心臓部: 取得→分類(2段)→要約/抽出→下書き→ラベル→ダイジェスト組立。

純粋オーケストレーション（Phase1 全同期・YAGNI）。依存は PipelineDeps で注入＝テスト容易。
安全:
 - 送信メソッドは MCPToolset に存在しない（構造的に送信不可能）。
 - dry_run=True のとき create_draft / label_thread を呼ばない＝副作用ゼロ（プレビューのみ作る）。
 - 1スレッドの失敗は隔離し needs_review に降格、全体は完走（隠さず提示）。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from aiia import triage
from aiia.config import MorningEmailConfig, UserConfig
from aiia.llm import LLM
from aiia.mcp.base import MCPToolset
from aiia.mcp.registry import label_for
from aiia.safety.audit import AuditLog
from aiia.safety.redaction import find_secrets, redact
from aiia.schemas import (
    AgentResult,
    CalendarEvent,
    Category,
    ClassificationResult,
    Digest,
    DigestItem,
    DraftReply,
    EmailThread,
    ThreadSummary,
)

# ヒューリスティックの自信がこの値以上なら LLM 再分類しない（コスト最小化＝2段分類）。
HEURISTIC_CONFIDENCE_THRESHOLD = 0.8

# 個別表示する「重要」カテゴリ（要返信でなくても出す＝顧客/VIP/プレス/金額）。
IMPORTANT_CATS = frozenset(
    {Category.CLIENT_URGENT, Category.CLIENT_NORMAL, Category.PRESS_MEDIA, Category.FINANCE_LEGAL}
)


def _should_show(cls: ClassificationResult) -> bool:
    """個別表示するか（要返信 or 重要cat or VIP）。それ以外は『一般メール』として件数畳み。"""
    return cls.is_actionable or cls.is_vip or cls.category in IMPORTANT_CATS


@dataclass
class PipelineDeps:
    llm: LLM
    tools: MCPToolset
    user: UserConfig
    agent: MorningEmailConfig
    audit: Optional[AuditLog] = None
    dry_run: bool = True
    now: Optional[datetime] = None  # テスト決定性用（未指定なら現在時刻）
    # M3: 文体プロファイル(本人)・関連Slack文脈(スレッド毎)。未指定なら従来どおり素のdraft。
    style_provider: Optional[Callable[[UserConfig], Optional[str]]] = None
    grounding_provider: Optional[Callable[[EmailThread], Optional[str]]] = None
    # M1 hardening: per-user/日 のコスト上限（LLMが cost_usd を持つ場合に超過で draft をスキップ）。
    max_budget_usd: Optional[float] = None
    # Phase2: 返信リマインド state（指定時のみ計算。dry_run は状態を変えない）。
    reminder_store: Optional[Any] = None


def _fetch_today_events(deps: PipelineDeps) -> tuple[list[CalendarEvent], bool]:
    """今日の予定を取得。戻り値 (events, failed)。calendar無効/未配線は ([], False)、
    取得失敗は ([], True)（0件＝予定なし と区別＝連携切れを『暇』と誤認させない）。fail-safe。"""
    cal_cfg = getattr(deps.agent, "calendar", None)
    cal = getattr(deps.tools, "calendar", None)
    if cal_cfg is None or not cal_cfg.enabled or cal is None:
        return [], False
    try:
        events = list(cal.list_today_events())
    except Exception:  # noqa: BLE001 — カレンダー失敗でメール本処理は止めない
        return [], True
    # 件名privacy：show_titles=False は件名/会議室/説明を伏せる。redactは秘密(鍵/カード)を消す保険。
    if not cal_cfg.show_titles:
        events = [e.model_copy(update={"title": "", "location": None, "description": None})
                  for e in events]
    else:
        events = [e.model_copy(update={
            "title": redact(e.title),
            "location": redact(e.location) if e.location else None,
            "description": redact(e.description) if e.description else None,
        }) for e in events]
    return events, False


def _compute_reminders(
    deps: PipelineDeps, items: list[DigestItem], threads: list[EmailThread], now: datetime
) -> list:
    """返信リマインドを計算（reminder_store 指定時のみ）。dry_run は状態を変えない。fail-safe。"""
    store = deps.reminder_store
    if store is None:
        return []
    from aiia import reminder as _reminder
    try:
        views = _reminder.compute_reminders(
            store, gmail=deps.tools.gmail, user_email=deps.user.user_id,
            today_items=items, threads_by_id={t.thread_id: t for t in threads},
            now=now, write=not deps.dry_run,
        )
    except Exception:  # noqa: BLE001 — リマインド失敗でメール本処理は止めない
        return []
    # [対応する]を1クリック自動リダイレクトにする reply_url を付与（OAUTH_STATE_SECRET未設定なら
    # 従来の action-button にフォールバック）。
    from aiia.auth.oauth_flow import make_reply_url
    for v in views:
        try:
            v.reply_url = make_reply_url(deps.user.user_id, v.thread_id)
        except Exception:  # noqa: BLE001
            pass
    return views


def _fetch_threads(deps: PipelineDeps) -> list[EmailThread]:
    """search_threads をページング全件取得 → thread_id で重複排除 → get_thread で本体取得。"""
    g = deps.tools.gmail
    seen: set[str] = set()
    ids: list[str] = []
    token: Optional[str] = None
    page_size = min(50, deps.agent.max_threads)
    while True:
        res = g.search_threads(deps.agent.gmail_query, page_token=token, max_results=page_size)
        for ref in res.get("threads", []):
            tid = ref["thread_id"]
            if tid not in seen:
                seen.add(tid)
                ids.append(tid)
        token = res.get("next_page_token")
        if not token or len(ids) >= deps.agent.max_threads:
            break
    ids = ids[: deps.agent.max_threads]
    return [g.get_thread(tid) for tid in ids]


def _classify(deps: PipelineDeps, thread: EmailThread) -> ClassificationResult:
    """2段分類: まずルール、自信が閾値未満のときだけ LLM に確定させる。"""
    heur = triage.classify_from_hints(triage.heuristic_signals(thread, deps.user))
    if heur.confidence >= HEURISTIC_CONFIDENCE_THRESHOLD:
        return heur
    return deps.llm.classify(thread, deps.user)


def _failed_item(thread: EmailThread) -> DigestItem:
    """処理失敗スレッドを「要確認」として隠さず提示（優先度は末尾）。"""
    return DigestItem(
        thread_id=thread.thread_id,
        category=Category.CLIENT_NORMAL,
        priority=999,
        subject=thread.subject or thread.thread_id,
        sender=thread.sender,
        summary=ThreadSummary(thread_id=thread.thread_id, one_liner="(処理に失敗・要確認)"),
        classification=ClassificationResult(
            category=Category.CLIENT_NORMAL, confidence=0.0, needs_review=True
        ),
        needs_review=True,
    )


def run(deps: PipelineDeps) -> AgentResult:
    start = time.monotonic()
    now = deps.now or datetime.now(timezone.utc)
    audit = deps.audit
    if audit:
        audit.record("run_started")

    threads = _fetch_threads(deps)
    calendar_events, calendar_failed = _fetch_today_events(deps)
    draft_cats = set(deps.agent.draft_categories)
    quiet_cats = set(deps.user.quiet_categories)

    items: list[DigestItem] = []
    counts: dict[Category, int] = {}
    quiet_counts: dict[Category, int] = {}
    drafts_created = 0
    labels_applied = 0
    redactions = 0

    for thread in threads:
        try:
            cls = _classify(deps, thread)
            summary = deps.llm.summarize(thread)
            counts[cls.category] = counts.get(cls.category, 0) + 1
            if audit:
                audit.record("thread_classified", thread_id=thread.thread_id, category=cls.category.value)

            # 表示ポリシー：要返信/重要だけ個別表示。一般メール(FYI/通知/NL)は件数だけ畳む。
            # quiet_categories(config)は「常に畳む」ハード上書きとして優先。
            if cls.category.value in quiet_cats or not _should_show(cls):
                quiet_counts[cls.category] = quiet_counts.get(cls.category, 0) + 1
                continue

            # 下書き（対象カテゴリ＆既存下書き無のみ）。プレビューは常に作り、Gmail保存は非dry_run時のみ。
            draft: Optional[DraftReply] = None
            draft_id: Optional[str] = None
            over_budget = (
                deps.max_budget_usd is not None
                and getattr(deps.llm, "cost_usd", 0.0) >= deps.max_budget_usd
            )
            if over_budget and cls.category.value in draft_cats and audit:
                audit.record("draft_skipped_budget", thread_id=thread.thread_id)
            if cls.category.value in draft_cats and not thread.has_existing_draft and not over_budget:
                style = deps.style_provider(deps.user) if deps.style_provider else None
                slack_ctx = deps.grounding_provider(thread) if deps.grounding_provider else None
                d = deps.llm.draft(thread, summary, deps.user, style=style, slack_context=slack_ctx)
                secrets = find_secrets(d.body)
                if secrets:
                    d = d.model_copy(update={"body": redact(d.body)})
                    redactions += len(secrets)
                draft = d
                if not deps.dry_run:
                    draft_id = deps.tools.gmail.create_draft(
                        thread_id=thread.thread_id, subject=d.subject, body=d.body
                    )
                    drafts_created += 1
                    if audit:
                        audit.record(
                            "draft_created", thread_id=thread.thread_id,
                            category=cls.category.value, redactions=len(secrets),
                        )

            # ラベル付け（非dry_run時のみ実行）
            if not deps.dry_run:
                deps.tools.gmail.label_thread(
                    thread_id=thread.thread_id, label=label_for(cls.category.value)
                )
                labels_applied += 1

            items.append(
                DigestItem(
                    thread_id=thread.thread_id,
                    category=cls.category,
                    priority=triage.compute_priority(cls),
                    subject=thread.subject,
                    sender=thread.sender,
                    summary=summary,
                    classification=cls,
                    draft=draft,
                    gmail_link=f"https://mail.google.com/mail/u/0/#all/{thread.thread_id}",
                    gmail_draft_id=draft_id,
                    needs_review=cls.needs_review,
                )
            )
        except Exception as exc:  # 失敗は隔離（全体は完走）
            if audit:
                audit.record("thread_failed", thread_id=thread.thread_id, error=type(exc).__name__)
            items.append(_failed_item(thread))

    items.sort(key=lambda it: it.priority)
    reminders = _compute_reminders(deps, items, threads, now)
    digest = Digest(
        generated_at=now,
        user_id=deps.user.user_id,
        items=items,
        counts_by_category=counts,
        quiet_counts=quiet_counts,
        reminders=reminders,
        calendar_events=calendar_events,
        calendar_failed=calendar_failed,
        processed=len(threads),
        elapsed_seconds=round(time.monotonic() - start, 3),
    )
    if audit:
        audit.record("run_completed", count=len(threads))
    return AgentResult(
        digest=digest,
        drafts_created=drafts_created,
        labels_applied=labels_applied,
        redactions=redactions,
        dry_run=deps.dry_run,
    )
