"""配信レンダラ（テキスト / Slack Block Kit）。WF-1/WF-2 + Slack制約。"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from aiia.delivery import render_digest_text, render_slack_blocks
from aiia.mcp.fake import FakeMCPToolset
from aiia.pipeline import PipelineDeps, run
from aiia.schemas import Digest, DigestItem


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


def test_interactive_buttons_on_draft_items(
    make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset
) -> None:
    from aiia.delivery.slack import ACTION_DELETE, ACTION_EDIT, ACTION_SEND

    d = run(make_deps(tools, dry_run=True)).digest
    plain = render_slack_blocks(d)  # M1 バッチ＝ボタン無し
    inter = render_slack_blocks(d, interactive=True)  # M2 常駐＝操作ボタン付き

    assert [b for b in plain if b.get("type") == "actions"] == []
    actions = [b for b in inter if b.get("type") == "actions"]
    assert len(actions) >= 1
    assert len(inter) <= 49  # Block Kit 制約は維持

    a0 = actions[0]
    assert a0["block_id"].startswith("aiia_item_")
    ids = {e["action_id"] for e in a0["elements"]}
    assert ids == {ACTION_EDIT, ACTION_DELETE, ACTION_SEND}
    send = next(e for e in a0["elements"] if e["action_id"] == ACTION_SEND)
    assert send["value"]  # thread_id を載せる（ハンドラが対象特定）
    assert "confirm" in send  # 送信は2段確認の1段目（Slackネイティブ確認）


def _ditem(rk: str, *, actionable: bool = False) -> DigestItem:
    from aiia.schemas import Category, ClassificationResult, ThreadSummary
    return DigestItem(
        thread_id="t" + rk, category=Category.CLIENT_NORMAL, priority=30,
        subject="件名", sender="x@a.example", summary=ThreadSummary(thread_id="t"),
        classification=ClassificationResult(
            category=Category.CLIENT_NORMAL, recipient_kind=rk, is_actionable=actionable),  # type: ignore[arg-type]
    )


def test_split_to_cc_promotes_actionable_cc() -> None:
    from aiia.delivery.slack import _split_to_cc
    to_only, cc_fyi, cc_act, unknown = (
        _ditem("to"), _ditem("cc"), _ditem("cc", actionable=True), _ditem("unknown"))
    to, cc = _split_to_cc([to_only, cc_fyi, cc_act, unknown])
    assert cc == [cc_fyi]  # 非アクションCcだけ情報共有グループ
    assert {id(x) for x in to} == {id(to_only), id(cc_act), id(unknown)}  # 要返信Cc/不明はTo側


def test_text_separates_to_and_cc_groups() -> None:
    d = Digest(generated_at=datetime(2026, 6, 9, 7, 0, tzinfo=timezone.utc), user_id="t",
               items=[_ditem("to"), _ditem("cc")])
    txt = render_digest_text(d)
    assert "あなた宛（To" in txt and "CC（情報共有" in txt


def test_block_budget_protects_to_over_calendar() -> None:
    # Toを大量に積んでも≤49・To見出しは残り、カレンダーは予算切れで degrade（出ない）する
    from aiia.schemas import CalendarEvent
    many_to = [_ditem("to") for _ in range(60)]
    d = Digest(generated_at=datetime(2026, 6, 9, 7, 0, tzinfo=timezone.utc), user_id="t",
               items=many_to,
               calendar_events=[CalendarEvent(event_id="c", title="定例",
                                              start=datetime(2026, 6, 9, 9, 0, tzinfo=timezone.utc))])
    blocks = render_slack_blocks(d, interactive=True)
    assert len(blocks) <= 49
    assert any("あなた宛" in str(b) for b in blocks)           # To は必ず出る（予算先取り）
    assert any("表示省略" in str(b) for b in blocks)            # 溢れは省略表記
    assert not any("今日の予定" in str(b) for b in blocks)      # カレンダーは予算切れで degrade
