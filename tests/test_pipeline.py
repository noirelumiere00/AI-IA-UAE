"""pipeline（心臓部）の振る舞い＝KPIの肝を構造的に固定。"""
from __future__ import annotations

from collections.abc import Callable

from conftest import FIXED_NOW

from aiia.config import MorningEmailConfig, UserConfig
from aiia.llm import HeuristicLLM
from aiia.mcp.fake import FakeGmail, FakeMCPToolset, FakeSlack, default_threads
from aiia.pipeline import PipelineDeps, run
from aiia.schemas import Category, EmailThread, ThreadSummary


def test_dry_run_zero_side_effects(make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset) -> None:
    res = run(make_deps(tools, dry_run=True))
    assert tools.calls == []  # 副作用ゼロ（create_draft/label/send 一切なし）
    assert res.drafts_created == 0 and res.labels_applied == 0
    assert res.dry_run is True
    assert res.digest.processed == 6
    assert any(it.draft for it in res.digest.items)  # プレビュー下書きは作る


def test_live_records_drafts_labels_no_send(
    make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset
) -> None:
    res = run(make_deps(tools, dry_run=False))
    kinds = [c[0] for c in tools.calls]
    assert "create_draft" in kinds and "label_thread" in kinds
    assert not any("send_message" in k for k in kinds)  # K5: 実送信は決して呼ばれない
    # 新ポリシー：要返信/重要だけ個別表示＋ラベル付与（一般メールは件数畳みで非表示・非ラベル）
    assert res.drafts_created == 2
    assert res.labels_applied == len(res.digest.items)  # 表示した項目だけラベル付与
    assert res.digest.quiet_counts  # 一般メールは件数に畳まれている


def test_existing_draft_is_skipped(
    make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset
) -> None:
    run(make_deps(tools, dry_run=False))
    drafted = [c[1] for c in tools.calls if c[0] == "create_draft"]
    assert "t_has_draft" not in drafted  # 既存下書きありは新規作成しない（FR-15）


def test_only_draft_categories_get_drafts(
    make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset
) -> None:
    res = run(make_deps(tools, dry_run=True))
    for it in res.digest.items:
        if it.draft is not None:
            assert it.category in (Category.CLIENT_URGENT, Category.PRESS_MEDIA)


def test_quiet_categories_folded(
    make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset
) -> None:
    res = run(make_deps(tools, dry_run=True))
    assert Category.NEWSLETTER not in {it.category for it in res.digest.items}
    assert res.digest.quiet_counts.get(Category.NEWSLETTER) == 1


def test_priority_sorted_urgent_first(
    make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset
) -> None:
    res = run(make_deps(tools, dry_run=True))
    priorities = [it.priority for it in res.digest.items]
    assert priorities == sorted(priorities)
    assert res.digest.items[0].category == Category.CLIENT_URGENT


def test_dedup_by_thread_id(
    user: UserConfig, agent: MorningEmailConfig, llm: HeuristicLLM
) -> None:
    ths = default_threads()
    dup = ths[0]  # 同一 thread_id を意図的に重複させる
    tools = FakeMCPToolset(gmail=FakeGmail(threads=[*ths, dup]), slack=FakeSlack())
    res = run(PipelineDeps(llm=llm, tools=tools, user=user, agent=agent, dry_run=True, now=FIXED_NOW))
    assert res.digest.processed == 6  # 7参照でも重複排除で6


class _BoomLLM(HeuristicLLM):
    """特定スレッドの要約で例外を出す（失敗隔離の検証用）。"""

    def summarize(self, thread: EmailThread) -> ThreadSummary:
        if thread.thread_id == "t_press":
            raise RuntimeError("boom")
        return super().summarize(thread)


def test_failure_isolated_run_completes(
    user: UserConfig, agent: MorningEmailConfig, tools: FakeMCPToolset
) -> None:
    deps = PipelineDeps(llm=_BoomLLM(), tools=tools, user=user, agent=agent, dry_run=True, now=FIXED_NOW)
    res = run(deps)
    failed = [it for it in res.digest.items if it.thread_id == "t_press"]
    assert failed and failed[0].needs_review
    assert "失敗" in failed[0].summary.one_liner
    assert res.digest.processed == 6  # 1件失敗でも全体は完走
