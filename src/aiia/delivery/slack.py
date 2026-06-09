"""Digest → 表示形式（テキスト / Slack Block Kit）。純粋関数・I/O無し。

引き算リデザイン：スクロールせず10秒で「今日まず何をやるか」。
構成：ヘッダ＋意思決定サマリ(⏭次の予定含む) → 🔔未返信(1行) → 📥今日やること(Top5・カテゴリ見出し無し)
→ 👥CC・📭一般(1行) → 📅今日の予定(フル・下部) → フッタ(下書きある日だけ)。
カテゴリは色絵文字を行頭に溶かし見出しを廃止。Gmailリンクは送信者名に統合。ボタンは温存。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiia.schemas import CATEGORY_EMOJI, Digest, DigestItem

_MAX_BLOCKS = 49
_MAX_TEXT = 2900
_JST = timezone(timedelta(hours=9))
_CAL_LIST_MAX = 5    # 下部フル予定の時刻列挙上限
_TO_TOP = 5          # 要対応の個別表示上限（超過は「ほかN件」fold）
_WD = ["月", "火", "水", "木", "金", "土", "日"]

# 対話ボタン（M2 の block_actions ハンドラが束縛）。value には thread_id / draft_id。
ACTION_EDIT = "aiia_edit_draft"
ACTION_DELETE = "aiia_delete_item"
ACTION_SEND = "aiia_send_reply"
_ITEM_BLOCK_PREFIX = "aiia_item_"

ACTION_REMIND_REPLY = "aiia_remind_reply"
ACTION_REMIND_DISMISS = "aiia_remind_dismiss"
ACTION_REMIND_SNOOZE = "aiia_remind_snooze"
ACTION_REMIND_MUTE = "aiia_remind_mute"
ACTION_REMIND_UNDO = "aiia_remind_undo"
_REMIND_BLOCK_PREFIX = "aiia_remind_"


def _reminder_actions(thread_id: str) -> dict:
    return {
        "type": "actions",
        "block_id": f"{_REMIND_BLOCK_PREFIX}{thread_id}",
        "elements": [
            {"type": "button", "action_id": ACTION_REMIND_REPLY, "style": "primary",
             "text": {"type": "plain_text", "text": "✏️ 対応する"}, "value": thread_id},
            {"type": "button", "action_id": ACTION_REMIND_DISMISS,
             "text": {"type": "plain_text", "text": "✅ 対応済み"}, "value": thread_id},
            {"type": "button", "action_id": ACTION_REMIND_SNOOZE,
             "text": {"type": "plain_text", "text": "⏰ 後で"}, "value": thread_id},
            {"type": "overflow", "action_id": ACTION_REMIND_MUTE,
             "options": [{"text": {"type": "plain_text", "text": "🔕 もう通知しない"}, "value": thread_id}]},
        ],
    }


def _item_actions(value: str) -> dict:
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


# ── 共通ヘルパ ────────────────────────────────────────────────────────────────
def _date_label(d: Digest) -> str:
    g = d.generated_at.astimezone(_JST)
    return f"{g.month}/{g.day}({_WD[g.weekday()]})"


def _fmt_t(dt: Optional[datetime]) -> str:
    return dt.astimezone(_JST).strftime("%H:%M") if dt else "??:??"


_NAME_RE = re.compile(r'^\s*"?([^"<]+?)"?\s*<')


def _short_sender(sender: str) -> str:
    """`"田中部長" <tanaka@x>` → `田中部長`（名が無ければアドレス）。Slackリンク用に|>を除去。"""
    m = _NAME_RE.match(sender or "")
    name = m.group(1) if m else (sender or "").strip().strip("<>")
    return name.replace("|", "/").replace(">", "").strip() or "(不明)"


def _sender_link(obj: Any) -> str:
    name = _short_sender(getattr(obj, "sender", ""))
    link = getattr(obj, "gmail_link", None)
    return f"<{link}|{name}>" if link else name


def _split_to_cc(items: list[DigestItem]) -> tuple[list[DigestItem], list[DigestItem]]:
    cc = [it for it in items
          if it.classification.recipient_kind == "cc" and not it.classification.is_actionable]
    cc_ids = {id(it) for it in cc}
    to = [it for it in items if id(it) not in cc_ids]
    return to, cc


def _has_deadline(it: DigestItem) -> bool:
    return any(a.kind == "deadline" and a.source_quote for a in it.summary.action_items)


def _ev_title(e: Any) -> str:
    return e.title or "（予定あり）"


# ── カレンダー ────────────────────────────────────────────────────────────────
def _has_calendar(d: Digest) -> bool:
    return bool(d.calendar_events) or d.calendar_failed


def _next_event_str(d: Digest) -> Optional[str]:
    """サマリ用「次の予定1行」。次の時刻つき予定が無ければ None。"""
    if d.calendar_failed or not d.calendar_events:
        return None
    timed = sorted((e for e in d.calendar_events if not e.all_day and e.start), key=lambda e: e.start.timestamp() if e.start else 0.0)
    nxt = next((e for e in timed if e.start and e.start >= d.generated_at), None)
    if not (nxt and nxt.start):
        return None
    mins = max(0, int((nxt.start - d.generated_at).total_seconds() // 60))
    h, m = divmod(mins, 60)
    when = f"あと{h}時間{m}分" if h and m else f"あと{h}時間" if h else f"あと{m}分"
    return f"⏭ 次 {_fmt_t(nxt.start)} {_ev_title(nxt)}（{when}）"


def _calendar_full_lines(d: Digest) -> list[str]:
    """下部のフル予定（終日1行＋時刻リスト1行・コンパクト）。0件/失敗を区別。"""
    if d.calendar_failed:
        return ["📅 *今日の予定* — ⚠️ 取得できませんでした"]
    evs = d.calendar_events
    if not evs:
        return ["📅 *今日の予定* — なし"]
    timed = sorted((e for e in evs if not e.all_day and e.start), key=lambda e: e.start.timestamp() if e.start else 0.0)
    allday = [e for e in evs if e.all_day]
    out = [f"📅 *今日の予定*（{len(evs)}件）"]
    if allday:
        out.append("🗓 終日: " + " ・ ".join(_ev_title(e) for e in allday))
    shown = timed[:_CAL_LIST_MAX]
    if shown:
        out.append(" ・ ".join(
            f"{_fmt_t(e.start)} {_ev_title(e)}" + (" ❓" if e.response_status == "needsAction" else "")
            for e in shown))
    if len(timed) > _CAL_LIST_MAX:
        out.append(f"ほか{len(timed) - _CAL_LIST_MAX}件")
    return out


# ── サマリ / 各項目 ───────────────────────────────────────────────────────────
def _decision_summary(d: Digest, to_items: list[DigestItem]) -> str:
    bits = []
    if d.reminders:
        bits.append(f"🔔 未返信 {len(d.reminders)}")
    if to_items:
        bits.append(f"📥 要対応 {len(to_items)}")
    nxt = _next_event_str(d)
    if nxt:
        bits.append(nxt)
    elif d.calendar_failed:
        bits.append("📅 予定 取得不可")
    return " ・ ".join(bits) or f"{d.processed}件確認"


def _misc_line(d: Digest, cc_items: list[DigestItem]) -> Optional[str]:
    parts = []
    if cc_items:
        parts.append(f"👥 CC {len(cc_items)}")
    qn = sum(d.quiet_counts.values())
    if qn:
        parts.append(f"📭 一般 {qn}")
    return ("　".join(parts) + "（参考・件数のみ）") if parts else None


def _remind_when(v: Any) -> str:
    fs = v.first_seen.astimezone(_JST) if v.first_seen else None
    tail = f"（{fs.month}/{fs.day}〜）" if fs else ""
    return f"{v.business_days}営業日放置{tail}"


def _reminder_line(v: Any) -> str:
    """リマインド1行：色絵文字 + 送信者(リンク) + 件名 — N営業日放置。生スニペットは出さない。"""
    emoji = CATEGORY_EMOJI.get(v.category, "🔔")
    return f"{emoji} {_sender_link(v)} {v.subject} — {_remind_when(v)}"


def _item_text(it: DigestItem) -> str:
    """要対応メール：色 *送信者(リンク)* 件名 [📝⏰⚠️] ＋ 要約(平文)。カテゴリ見出し・ラベルなし。"""
    icons = ("" + (" 📝" if it.draft else "") + (" ⏰" if _has_deadline(it) else "")
             + (" ⚠️" if it.needs_review else ""))
    head = f"{CATEGORY_EMOJI[it.category]} *{_sender_link(it)}* {it.subject}{icons}"
    return head + (f"\n{it.summary.one_liner}" if it.summary.one_liner else "")


def _sorted(items: list[DigestItem]) -> list[DigestItem]:
    return sorted(items, key=lambda it: it.priority)  # BASE_PRIORITY順＝色が自然にソート


# ── テキスト（dry-run）─────────────────────────────────────────────────────────
def render_digest_text(d: Digest) -> str:
    to_items, cc_items = _split_to_cc(d.items)
    lines = [f"📬 朝のダイジェスト — {_date_label(d)}", _decision_summary(d, to_items)]
    empty = not d.items and not d.quiet_counts and not _has_calendar(d) and not d.reminders
    if empty:
        lines.append("\n☕ 今日は静かです。")
        return "\n".join(lines)
    if d.reminders:
        lines.append("\n🔔 未返信 ― 今日中に")
        lines.extend(f"  {_reminder_line(v)}" for v in d.reminders)
    if to_items:
        lines.append(f"\n📥 今日やること（{len(to_items)}）")
        for it in _sorted(to_items)[:_TO_TOP]:
            for ln in _item_text(it).split("\n"):
                lines.append(f"  {ln}")
        if len(to_items) > _TO_TOP:
            lines.append(f"  ほか{len(to_items) - _TO_TOP}件")
    misc = _misc_line(d, cc_items)
    if misc:
        lines.append(f"\n{misc}")
    if _has_calendar(d):
        lines.append("")
        lines.extend(_calendar_full_lines(d))
    if any(it.draft for it in to_items):
        lines.append("\n⏱ 下書きは未送信。Gmailで確認のうえ送信してください。")
    return "\n".join(lines)


# ── Slack Block Kit ────────────────────────────────────────────────────────────
def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:_MAX_TEXT]}}


def _ctx(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:_MAX_TEXT]}]}


def render_slack_blocks(d: Digest, *, interactive: bool = False) -> list[dict]:
    """Slack Block Kit。引き算レイアウト・≤49ブロック。リマインド/To を先に積んで予算確保。"""
    to_items, cc_items = _split_to_cc(d.items)
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📬 朝のダイジェスト — {_date_label(d)}"}},
        _ctx(_decision_summary(d, to_items)),
    ]
    if not d.items and not d.quiet_counts and not _has_calendar(d) and not d.reminders:
        blocks.append(_section("☕ *今日は静かです* — 新着の未読メールはありません。"))
        return blocks

    truncated = 0

    def _room() -> bool:
        return len(blocks) < _MAX_BLOCKS - 2

    # 🔔 未返信（1行＋ボタン）＝最優先
    if d.reminders:
        blocks.append(_section("*🔔 未返信 ― 今日中に*"))
        for v in d.reminders:
            if not _room():
                truncated += 1
                continue
            blocks.append(_section(_reminder_line(v)))
            if interactive and _room():
                blocks.append(_reminder_actions(v.thread_id))

    # 📥 今日やること（Top5・カテゴリ見出し無し・色は行頭）
    if to_items:
        blocks.append(_section("*📥 今日やること*"))
        for it in _sorted(to_items)[:_TO_TOP]:
            if not _room():
                truncated += 1
                continue
            blocks.append(_section(_item_text(it)))
            if interactive and it.draft and _room():
                blocks.append(_item_actions(it.gmail_draft_id or it.thread_id))
        if len(to_items) > _TO_TOP:
            truncated += len(to_items) - _TO_TOP

    # 👥CC ・ 📭一般（1行）
    misc = _misc_line(d, cc_items)
    if misc and _room():
        blocks.append(_ctx(misc))

    # 📅 今日の予定（フル・下部）
    if _has_calendar(d) and _room():
        blocks.append(_section("\n".join(_calendar_full_lines(d))))

    if truncated:
        blocks.append(_ctx(f"＋{truncated}件は省略 — Gmailで確認してください"))
    if any(it.draft for it in to_items):
        blocks.append(_ctx("⏱ 下書きは未送信。Gmailで確認のうえ送信してください。"))
    return blocks[:_MAX_BLOCKS]
