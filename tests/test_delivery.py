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
    assert "メールサマリー" in txt
    assert "要対応メール" in txt and "〔" in txt  # カテゴリ見出し廃止・先頭に〔カテゴリ語〕
    assert "CLIENT_URGENT (" not in txt              # カテゴリ見出しは出さない
    assert "未送信" in txt                            # 下書きがあるので未送信注意


def test_blocks_within_limit_and_header(
    make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset
) -> None:
    blocks = render_slack_blocks(run(make_deps(tools, dry_run=True)).digest)
    assert isinstance(blocks, list)
    assert len(blocks) <= 49  # Block Kit 制約
    assert blocks[0]["type"] == "header"


def test_empty_digest_quiet_morning() -> None:
    d = Digest(generated_at=datetime(2026, 6, 8, 7, 0, tzinfo=timezone.utc), user_id="t")
    assert "今日は静かです" in render_digest_text(d)
    blocks = render_slack_blocks(d)
    assert any("静かです" in str(b) for b in blocks)


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


def test_to_shown_and_cc_excluded() -> None:
    # 引き算後：To は「要対応メール」に個別、情報共有Ccは表示も件数も出さない（参考行は廃止）
    d = Digest(generated_at=datetime(2026, 6, 9, 7, 0, tzinfo=timezone.utc), user_id="t",
               items=[_ditem("to"), _ditem("cc"), _ditem("cc")])
    txt = render_digest_text(d)
    assert "要対応メール（1件）" in txt  # Toの1件だけ
    assert "CC" not in txt and "参考" not in txt  # Cc件数行は出さない


def test_to_top5_fold_within_limit() -> None:
    # 引き算後：要対応はTop5まで個別、超過は「省略」に畳む。≤49を維持。
    many_to = [_ditem("to") for _ in range(60)]
    d = Digest(generated_at=datetime(2026, 6, 9, 7, 0, tzinfo=timezone.utc), user_id="t", items=many_to)
    blocks = render_slack_blocks(d, interactive=True)
    assert len(blocks) <= 49
    assert any("要対応メール" in str(b) for b in blocks)   # To見出しは残る
    assert any("ほか" in str(b) for b in blocks)          # Top5超は「ほかN件はGmailで確認」に畳む


def test_reminder_actions_url_button_vs_action() -> None:
    from aiia.delivery.slack import ACTION_REMIND_REPLY, _reminder_actions
    u = _reminder_actions("t1", reply_url="http://localhost:8788/reply?s=z")
    btn = u["elements"][0]
    assert btn["action_id"] == ACTION_REMIND_REPLY and btn.get("url") == "http://localhost:8788/reply?s=z"
    assert "value" not in btn  # url-button は value を載せない（handlerがackのみで判別）
    a = _reminder_actions("t1")  # reply_url 無し＝従来の action-button
    assert a["elements"][0].get("value") == "t1" and "url" not in a["elements"][0]


def test_reminder_line_uses_markdown_reply_link_for_single_app() -> None:
    # §V4 単一アプリ化：朝バッチ(interactive=False)の本文に「対応する」を markdown リンクで載せる。
    # button だと共用アプリ(OpenClawがSocket Mode保持)で interaction が宙に浮くため、リンクで安全。
    from aiia.delivery.slack import _reminder_line
    from aiia.schemas import Category, ReminderView

    v = ReminderView(
        thread_id="t1", category=Category.CLIENT_NORMAL,
        subject="見積の件", sender="田中 <tanaka@x.co>",
        reply_url="https://aila.example/reply?s=sig",
    )
    line = _reminder_line(v)
    assert "<https://aila.example/reply?s=sig|" in line and "対応する" in line
    # reply_url 無し＝リンクを出さない（壊れたリンクを置かない）
    v2 = ReminderView(thread_id="t2", category=Category.CLIENT_NORMAL, subject="x", sender="y")
    assert "対応する" not in _reminder_line(v2)


def test_item_text_includes_reply_and_gmail_links() -> None:
    # §W: 要対応メール各件に「✏️返信を作成」(reply_url) と「📧Gmailで開く」(gmail_link) が出る。
    from aiia.delivery.slack import _item_text
    from aiia.schemas import Category, ClassificationResult, DigestItem, ThreadSummary

    it = DigestItem(
        thread_id="t1", category=Category.CLIENT_NORMAL, priority=1,
        subject="見積の件", sender="田中 <tanaka@x.co>",
        summary=ThreadSummary(thread_id="t1", one_liner="見積を今日中に"),
        classification=ClassificationResult(category=Category.CLIENT_NORMAL, amount_jpy=3_500_000, key_person="江畑"),
        gmail_link="https://mail.google.com/mail/u/0/#all/t1",
        reply_url="https://aila.example/reply?s=sig",
    )
    txt = _item_text(it)
    assert "<https://aila.example/reply?s=sig|" in txt and "返信を作成" in txt
    assert "📧 Gmail" in txt and "#all/t1" in txt
    assert "350万" in txt and "江畑" in txt  # 金額・担当バッジ
    # reply_url 無し＝返信リンクは出さない
    it2 = DigestItem(thread_id="t2", category=Category.CLIENT_NORMAL, priority=1, subject="x", sender="y",
                     summary=ThreadSummary(thread_id="t2"), classification=ClassificationResult(category=Category.CLIENT_NORMAL))
    assert "返信を作成" not in _item_text(it2)
