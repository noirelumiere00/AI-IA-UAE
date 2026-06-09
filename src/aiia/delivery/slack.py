"""Digest → 表示形式（テキスト / Slack Block Kit）。純粋関数・I/O無し。

- render_digest_text: dry-run の stdout 用。人が読みやすい朝ダイジェスト。
- render_slack_blocks: Slack 投稿用 Block Kit（≤49ブロック・各text≤2900字の制約を守る）。
構成：ヘッダ＋意思決定サマリ → 📥あなた宛(To) → 📅今日の予定 → 👥CC → 📭一般(件数) → フッタ。
To を先に積んで予算を確保（溢れはカレンダー/CC側から degrade）。「下書きは未送信」を明示。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiia.schemas import BASE_PRIORITY, CATEGORY_EMOJI, Category, Digest, DigestItem

_MAX_BLOCKS = 49
_MAX_TEXT = 2900
_JST = timezone(timedelta(hours=9))
_CAL_LIST_MAX = 5  # 時刻つき予定をこの数まで列挙し、超過は「ほかN件」に件数fold

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
    g = d.generated_at.astimezone(_JST)  # JST 表示（generated_at は UTC のことがある）
    return f"{g.month}/{g.day} {g:%H:%M}"


def _group(items: list[DigestItem]) -> dict[Category, list[DigestItem]]:
    groups: dict[Category, list[DigestItem]] = defaultdict(list)
    for it in items:
        groups[it.category].append(it)
    return groups


def _split_to_cc(items: list[DigestItem]) -> tuple[list[DigestItem], list[DigestItem]]:
    """To（直接宛＋要返信Ccは昇格）と Cc（情報共有のみ）に分割。"""
    cc = [it for it in items
          if it.classification.recipient_kind == "cc" and not it.classification.is_actionable]
    cc_ids = {id(it) for it in cc}
    to = [it for it in items if id(it) not in cc_ids]
    return to, cc


def _deadline_note(it: DigestItem) -> str:
    dls = [a for a in it.summary.action_items if a.kind == "deadline" and a.source_quote]
    return f"    ⏰ 締切言及: {dls[0].source_quote[:40]}" if dls else ""


# ── カレンダー（今日の予定）─────────────────────────────────────────────────
def _ev_title(e) -> str:  # type: ignore[no-untyped-def]
    return e.title or "（予定あり）"  # show_titles=False で空 → 件名非表示


def _fmt_t(dt: Optional[datetime]) -> str:
    return dt.astimezone(_JST).strftime("%H:%M") if dt else "??:??"


def _has_calendar(d: Digest) -> bool:
    return bool(d.calendar_events) or d.calendar_failed


def _calendar_lines(d: Digest) -> list[str]:
    """今日の予定の表示行（次の予定ハイライト＋終日＋時刻リスト・≥maxは件数fold）。
    0件と取得失敗を区別（連携切れを『暇』と誤認させない）。"""
    if d.calendar_failed:
        return ["📅 *今日の予定* — ⚠️ 取得できませんでした"]
    evs = d.calendar_events
    if not evs:
        return ["📅 *今日の予定* — なし"]
    timed = [e for e in evs if not e.all_day and e.start]
    allday = [e for e in evs if e.all_day]
    out = [f"📅 *今日の予定*（{len(evs)}件）"]
    nxt = next((e for e in timed if e.start and e.start >= d.generated_at), None)
    if nxt and nxt.start:
        mins = max(0, int((nxt.start - d.generated_at).total_seconds() // 60))
        when = f"あと{mins // 60}時間{mins % 60}分" if mins >= 60 else f"あと{mins}分"
        out.append(f"⏭ 次は {_fmt_t(nxt.start)} {_ev_title(nxt)}（{when}）")
    for e in allday:
        out.append(f"🗓 終日: {_ev_title(e)}")
    for e in timed[:_CAL_LIST_MAX]:
        mark = (" ❓未応答" if e.response_status == "needsAction"
                else " ・仮" if e.response_status == "tentative" else "")
        rng = _fmt_t(e.start) + (f"–{_fmt_t(e.end)}" if e.end else "")
        out.append(f"・{rng} {_ev_title(e)}{mark}")
    if len(timed) > _CAL_LIST_MAX:
        out.append(f"・ほか{len(timed) - _CAL_LIST_MAX}件")
    return out


def _decision_summary(d: Digest, to_items: list[DigestItem], cc_items: list[DigestItem]) -> str:
    bits = []
    if to_items:
        bits.append(f"📥 要対応 {len(to_items)}")
    if d.calendar_events or d.calendar_failed:
        bits.append(f"📅 予定 {len(d.calendar_events)}")
    other = sum(d.quiet_counts.values()) + len(cc_items)
    if other:
        bits.append(f"📭 その他 {other}")
    return " ・ ".join(bits) or f"{d.processed}件処理"


# ── テキスト（dry-run）─────────────────────────────────────────────────────
def render_digest_text(d: Digest) -> str:
    to_items, cc_items = _split_to_cc(d.items)
    lines = [
        f"📬 朝のダイジェスト — {_date_label(d)}",
        _decision_summary(d, to_items, cc_items),
    ]
    if _has_calendar(d):
        lines.append("")
        lines.extend(_calendar_lines(d))
    if not d.items and not d.quiet_counts:
        lines.append("\n☕ 新着の未読メールはありません。" if _has_calendar(d)
                     else "\n☕ 静かな朝です — 新着の未読メールはありません。")
        return "\n".join(lines)

    def _emit(items: list[DigestItem]) -> None:
        for cat in sorted(_group(items), key=lambda c: BASE_PRIORITY[c]):
            its = _group(items)[cat]
            lines.append(f"\n{CATEGORY_EMOJI[cat]} {cat.value} ({len(its)})")
            for it in its:
                flag = " ⚠️要確認" if it.needs_review else ""
                draft = "📝 " if it.draft else ""
                lines.append(f"  • {draft}{it.sender} ・ {it.subject}{flag}")
                if it.summary.one_liner:
                    lines.append(f"    要約: {it.summary.one_liner}")
                dl = _deadline_note(it)
                if dl:
                    lines.append(dl)
                if it.gmail_link:
                    lines.append(f"    ↗ {it.gmail_link}")

    if to_items:
        lines.append("\n━━ 📥 あなた宛（To・要対応）━━")
        _emit(to_items)
    if cc_items:
        lines.append("\n━━ 👥 CC（情報共有・参考）━━")
        _emit(cc_items)
    if d.quiet_counts:
        folded = " ・ ".join(
            f"{CATEGORY_EMOJI[cat]} {cat.value} {n}"
            for cat, n in sorted(d.quiet_counts.items(), key=lambda kv: BASE_PRIORITY[kv[0]])
        )
        lines.append(f"\n📭 一般メール（件数のみ・要返信/重要以外）: {folded}")
    lines.append("\n⏱ 下書きは未送信です。Gmailで確認のうえ、送信は人が行ってください。")
    return "\n".join(lines)


# ── Slack Block Kit ────────────────────────────────────────────────────────
def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:_MAX_TEXT]}}


def render_slack_blocks(d: Digest, *, interactive: bool = False) -> list[dict]:
    """Slack Block Kit。≤49ブロック。To を先に積んで予算確保（溢れはカレンダー/CC側を degrade）。

    interactive=True で下書きのある項目に操作ボタン[📝編集/🗑削除/📤送信]を付与（M2の常駐アプリ用）。
    """
    to_items, cc_items = _split_to_cc(d.items)
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📬 朝のダイジェスト — {_date_label(d)}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _decision_summary(d, to_items, cc_items)}]},
    ]
    if not d.items and not d.quiet_counts and not _has_calendar(d):
        blocks.append(_section("☕ *静かな朝です* — 新着の未読メールはありません。"))
        return blocks

    truncated = 0

    def _emit_blocks(items: list[DigestItem], header: str) -> None:
        nonlocal truncated
        if not items:
            return
        if len(blocks) >= _MAX_BLOCKS - 2:
            truncated += len(items)
            return
        blocks.append(_section(header))
        for cat in sorted(_group(items), key=lambda c: BASE_PRIORITY[c]):
            its = _group(items)[cat]
            if len(blocks) >= _MAX_BLOCKS - 2:
                truncated += len(its)
                continue
            blocks.append(_section(f"*{CATEGORY_EMOJI[cat]} {cat.value} ({len(its)})*"))
            for it in its:
                if len(blocks) >= _MAX_BLOCKS - 2:
                    truncated += 1
                    continue
                flag = " ⚠️要確認" if it.needs_review else ""
                draft_icon = "📝 " if it.draft else ""
                dl = _deadline_note(it).strip()
                dl_line = f"\n{dl}" if dl else ""
                link = f"\n<{it.gmail_link}|↗ Gmailで開く>" if it.gmail_link else ""
                blocks.append(_section(
                    f"{draft_icon}*{it.sender}*{flag}\n件名: {it.subject}\n要約: {it.summary.one_liner}{dl_line}{link}"
                ))
                # 下書きのある項目だけ操作ボタン（編集/削除/送信）。送信は2段確認の1段目付き。
                if interactive and it.draft and len(blocks) < _MAX_BLOCKS - 2:
                    blocks.append(_item_actions(it.gmail_draft_id or it.thread_id))

    # 1) 📥 あなた宛(To)＝最優先・予算先取り
    _emit_blocks(to_items, "*📥 あなた宛（To・要対応）*")
    # 2) 📅 今日の予定＝常に1ブロック（予算が無ければ出さない＝Toを潰さない）
    if _has_calendar(d) and len(blocks) < _MAX_BLOCKS - 2:
        blocks.append(_section("\n".join(_calendar_lines(d))))
    # 3) 👥 CC（情報共有）＝余りで
    _emit_blocks(cc_items, "*👥 CC（情報共有・参考）*")
    # 4) 📭 一般メール（件数のみ）
    quiet_txt = " ・ ".join(
        f"{CATEGORY_EMOJI[c]} {c.value} ({n})"
        for c, n in sorted(d.quiet_counts.items(), key=lambda kv: BASE_PRIORITY[kv[0]])
    )
    if quiet_txt and len(blocks) < _MAX_BLOCKS - 1:
        blocks.append(_section(f"📭 *一般メール（件数のみ・要返信/重要以外）*: {quiet_txt}"))
    if truncated:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": f"＋{truncated}件は表示省略 — Gmailで確認してください"}]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
        "text": "⏱ 下書きは未送信。Gmailで確認のうえ送信は人が行ってください。"}]})
    return blocks[:_MAX_BLOCKS]
