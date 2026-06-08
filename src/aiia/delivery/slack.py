"""Digest → 表示形式（テキスト / Slack Block Kit）。純粋関数・I/O無し。

- render_digest_text: dry-run の stdout 用。人が読みやすい朝ダイジェスト。
- render_slack_blocks: Slack 投稿用 Block Kit（≤49ブロック・各text≤2900字の制約を守る）。
両者とも「下書きは未送信」を明示し、quiet_categories は件数のみに畳む。
"""
from __future__ import annotations

from collections import defaultdict

from aiia.schemas import BASE_PRIORITY, CATEGORY_EMOJI, Category, Digest, DigestItem

_MAX_BLOCKS = 49
_MAX_TEXT = 2900

# 対話ボタンの action_id（M2 の block_actions ハンドラが束縛）。value には thread_id を載せる。
ACTION_EDIT = "aiia_edit_draft"
ACTION_DELETE = "aiia_delete_item"
ACTION_SEND = "aiia_send_reply"
_ITEM_BLOCK_PREFIX = "aiia_item_"


def _item_actions(value: str) -> dict:
    """各メール項目の操作ボタン。value は Gmail下書きID(無ければthread_id)＝M2ハンドラが対象特定に使う。
    送信は Slack ネイティブ確認(1/2)付き＝2段確認の1段目。"""
    return {
        "type": "actions",
        "block_id": f"{_ITEM_BLOCK_PREFIX}{value}",
        "elements": [
            {"type": "button", "action_id": ACTION_EDIT,
             "text": {"type": "plain_text", "text": "📝 編集"}, "value": value},
            {"type": "button", "action_id": ACTION_DELETE,
             "text": {"type": "plain_text", "text": "🗑 削除"}, "value": value},
            {"type": "button", "action_id": ACTION_SEND, "style": "primary",
             "text": {"type": "plain_text", "text": "📤 送信"}, "value": value,
             "confirm": {
                 "title": {"type": "plain_text", "text": "送信の確認 (1/2)"},
                 "text": {"type": "mrkdwn", "text": "この下書きを送信しますか？次の画面でもう一度確認します。"},
                 "confirm": {"type": "plain_text", "text": "次へ"},
                 "deny": {"type": "plain_text", "text": "やめる"},
             }},
        ],
    }


def _date_label(d: Digest) -> str:
    g = d.generated_at
    return f"{g.month}/{g.day} {g:%H:%M}"


def _group(items: list[DigestItem]) -> dict[Category, list[DigestItem]]:
    groups: dict[Category, list[DigestItem]] = defaultdict(list)
    for it in items:
        groups[it.category].append(it)
    return groups


def _deadline_note(it: DigestItem) -> str:
    dls = [a for a in it.summary.action_items if a.kind == "deadline" and a.source_quote]
    return f"    ⏰ 締切言及: {dls[0].source_quote[:40]}" if dls else ""


def render_digest_text(d: Digest) -> str:
    """dry-run 用の人間可読テキスト。"""
    draft_n = sum(1 for it in d.items if it.draft)
    lines = [
        f"📬 朝のダイジェスト — {_date_label(d)}",
        f"{d.processed}件処理 / {d.elapsed_seconds}秒 / 下書き{draft_n}件 ・ 未送信(Gmailで確認後に送信)",
    ]
    if not d.items and not d.quiet_counts:
        lines.append("\n☕ 静かな朝です — 新着の未読メールはありません。")
        return "\n".join(lines)

    groups = _group(d.items)
    for cat in sorted(groups, key=lambda c: BASE_PRIORITY[c]):
        its = groups[cat]
        lines.append(f"\n{CATEGORY_EMOJI[cat]} {cat.value} ({len(its)})")
        for it in its:
            flag = " ⚠️要確認" if it.needs_review else ""
            lines.append(f"  • {it.sender} ・ {it.subject}{flag}")
            if it.summary.one_liner:
                lines.append(f"    要約: {it.summary.one_liner}")
            dl = _deadline_note(it)
            if dl:
                lines.append(dl)
            if it.draft:
                lines.append(f"    📝 下書きプレビュー作成済（未送信・敬語Lv{it.draft.keigo_level}）")
            if it.gmail_link:
                lines.append(f"    ↗ {it.gmail_link}")

    for cat, n in sorted(d.quiet_counts.items(), key=lambda kv: BASE_PRIORITY[kv[0]]):
        lines.append(f"\n{CATEGORY_EMOJI[cat]} {cat.value} ({n}) ・ 折りたたみ（件数のみ）")

    lines.append("\n⏱ 下書きは未送信です。Gmailで確認のうえ、送信は人が行ってください。")
    return "\n".join(lines)


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:_MAX_TEXT]}}


def render_slack_blocks(d: Digest, *, interactive: bool = False) -> list[dict]:
    """Slack Block Kit。≤49ブロックに収める（超過分は『Gmailで確認』に集約）。

    interactive=True で下書きのある項目に操作ボタン[📝編集/🗑削除/📤送信]を付与（M2の常駐アプリ用）。
    M1のバッチ配信は interactive=False（ボタン無し＝クリックしても動く相手がいないため）。
    """
    draft_n = sum(1 for it in d.items if it.draft)
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📬 朝のダイジェスト — {_date_label(d)}"}},
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": f"{d.processed}件処理 / {d.elapsed_seconds}秒 / 下書き{draft_n}件 ・ 未送信(Gmailで確認後に送信)"}]},
    ]
    if not d.items and not d.quiet_counts:
        blocks.append(_section("☕ *静かな朝です* — 新着の未読メールはありません。"))
        return blocks

    groups = _group(d.items)
    truncated = 0
    for cat in sorted(groups, key=lambda c: BASE_PRIORITY[c]):
        its = groups[cat]
        if len(blocks) >= _MAX_BLOCKS - 2:
            truncated += len(its)
            continue
        blocks.append(_section(f"*{CATEGORY_EMOJI[cat]} {cat.value} ({len(its)})*"))
        for it in its:
            if len(blocks) >= _MAX_BLOCKS - 2:
                truncated += 1
                continue
            flag = " ⚠️要確認" if it.needs_review else ""
            draft_line = "\n📝 下書きプレビュー作成済（未送信）" if it.draft else ""
            dl = _deadline_note(it).strip()
            dl_line = f"\n{dl}" if dl else ""
            link = f"\n<{it.gmail_link}|↗ Gmailで開く>" if it.gmail_link else ""
            blocks.append(_section(
                f"*{it.sender}*{flag}\n件名: {it.subject}\n要約: {it.summary.one_liner}{dl_line}{draft_line}{link}"
            ))
            # 下書きのある項目だけ操作ボタン（編集/削除/送信）。送信は2段確認の1段目付き。
            # value=Gmail下書きID(M2ハンドラが drafts.update/send/delete に使用)・無ければthread_id。
            if interactive and it.draft and len(blocks) < _MAX_BLOCKS - 2:
                blocks.append(_item_actions(it.gmail_draft_id or it.thread_id))

    quiet_txt = " ・ ".join(
        f"{CATEGORY_EMOJI[c]} {c.value} ({n})"
        for c, n in sorted(d.quiet_counts.items(), key=lambda kv: BASE_PRIORITY[kv[0]])
    )
    if quiet_txt:
        blocks.append(_section(f"折りたたみ（件数のみ）: {quiet_txt}"))
    if truncated:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": f"＋{truncated}件は表示省略 — Gmailで確認してください"}]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
        "text": "⏱ 下書きは未送信。Gmailで確認のうえ送信は人が行ってください。"}]})
    return blocks[:_MAX_BLOCKS]
