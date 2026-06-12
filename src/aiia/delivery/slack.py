"""Digest → 表示形式（テキスト / Slack Block Kit）。純粋関数・I/O無し。

引き算リデザイン＋最小絵文字（ビジネス調）：スクロールせず10秒で「今日まず何をやるか」。
構成：ヘッダ → 未返信(1行) → 要対応メール(Top5) → CC・一般(1行) → 今日の予定(フル・下部) → フッタ。
優先度は装飾絵文字でなく〔カテゴリ語〕＋並び順で表現。Gmailリンクは送信者/件名に統合。
ステータスの✅リアクション（別レイヤー）は温存。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiia.schemas import Category, Digest, DigestItem

# カテゴリの短い日本語ラベル（色絵文字だけだと意味不明なので併記して戻す）
_CAT_LABEL = {
    Category.CLIENT_URGENT: "顧客・緊急",
    Category.CLIENT_NORMAL: "顧客",
    Category.PRESS_MEDIA: "プレス",
    Category.FINANCE_LEGAL: "金額・法務",
    Category.VENDOR_PARTNER: "取引先",
    Category.INTERNAL: "社内",
    Category.NEWSLETTER: "一般",
}

_MAX_BLOCKS = 49
_MAX_TEXT = 2900
_JST = timezone(timedelta(hours=9))
_CAL_LIST_MAX = 5  # 下部フル予定の時刻列挙上限
_TO_TOP = 5  # 要対応の個別表示上限（超過は「ほかN件」fold）
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


def _reminder_actions(thread_id: str, reply_url: Optional[str] = None) -> dict:
    # 対応する：reply_url ありは url-button（1クリックで /reply→下書き作成→Gmailへ自動リダイレクト）、
    # 無しは従来の action-button（押す→下書き作成→リンク）。
    reply_btn: dict = {
        "type": "button",
        "action_id": ACTION_REMIND_REPLY,
        "style": "primary",
        "text": {"type": "plain_text", "text": "対応する"},
    }
    if reply_url:
        reply_btn["url"] = reply_url
    else:
        reply_btn["value"] = thread_id
    return {
        "type": "actions",
        "block_id": f"{_REMIND_BLOCK_PREFIX}{thread_id}",
        "elements": [
            reply_btn,
            {
                "type": "button",
                "action_id": ACTION_REMIND_DISMISS,
                "text": {"type": "plain_text", "text": "対応済み"},
                "value": thread_id,
            },
            {
                "type": "button",
                "action_id": ACTION_REMIND_SNOOZE,
                "text": {"type": "plain_text", "text": "後で"},
                "value": thread_id,
            },
            {
                "type": "overflow",
                "action_id": ACTION_REMIND_MUTE,
                "options": [
                    {"text": {"type": "plain_text", "text": "もう通知しない"}, "value": thread_id}
                ],
            },
        ],
    }


def _item_actions(value: str) -> dict:
    return {
        "type": "actions",
        "block_id": f"{_ITEM_BLOCK_PREFIX}{value}",
        "elements": [
            {
                "type": "button",
                "action_id": ACTION_EDIT,
                "text": {"type": "plain_text", "text": "編集"},
                "value": value,
            },
            {
                "type": "button",
                "action_id": ACTION_DELETE,
                "text": {"type": "plain_text", "text": "削除"},
                "value": value,
            },
            {
                "type": "button",
                "action_id": ACTION_SEND,
                "style": "primary",
                "text": {"type": "plain_text", "text": "送信"},
                "value": value,
                "confirm": {
                    "title": {"type": "plain_text", "text": "送信の確認 (1/2)"},
                    "text": {
                        "type": "mrkdwn",
                        "text": "この下書きを送信しますか？次の画面でもう一度確認します。",
                    },
                    "confirm": {"type": "plain_text", "text": "次へ"},
                    "deny": {"type": "plain_text", "text": "やめる"},
                },
            },
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
    cc = [
        it
        for it in items
        if it.classification.recipient_kind == "cc" and not it.classification.is_actionable
    ]
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


def _calendar_full_lines(d: Digest) -> list[str]:
    """今日の予定（時刻=左・予定=右・1件ごとに改行の縦リスト）。終日は別行。0件/失敗を区別。"""
    if d.calendar_failed:
        return ["*今日の予定* — 取得できませんでした"]
    evs = d.calendar_events
    if not evs:
        return ["*今日の予定* — なし"]
    timed = sorted(
        (e for e in evs if not e.all_day and e.start),
        key=lambda e: e.start.timestamp() if e.start else 0.0,
    )
    allday = [e for e in evs if e.all_day]
    out = [f"*今日の予定*（{len(evs)}件）"]
    for e in timed[:_CAL_LIST_MAX]:
        extra = ""
        if e.location:
            extra += f" ／ {e.location}"
        if e.conference_url:
            extra += f" <{e.conference_url}|会議リンク>"
        mark = " （未応答）" if e.response_status == "needsAction" else ""
        out.append(
            f"`{_fmt_t(e.start)}`　{_ev_title(e)}{extra}{mark}"
        )  # 時刻左＋予定＋会議室/参加URL
    if len(timed) > _CAL_LIST_MAX:
        out.append(f"`     `　ほか{len(timed) - _CAL_LIST_MAX}件")
    if allday:
        out.append("終日： " + " ・ ".join(_ev_title(e) for e in allday))
    return out


# ── 各項目 ────────────────────────────────────────────────────────────────────
def _remind_subject_link(v: Any) -> str:
    subj = (getattr(v, "subject", "") or "(件名なし)").replace("|", "/").replace(">", "")
    link = getattr(v, "gmail_link", None)
    return f"<{link}|{subj}>" if link else subj


def _reminder_line(v: Any) -> str:
    """未返信1件：行頭に *放置日数バッジ* → 種別 → 相手 → 用件。緊急度を視線の最初へ。

    メール=〔カテゴリ〕*相手* — 件名(link)、Slack=メンション *@相手* in #ch / 抜粋。
    """
    bd = getattr(v, "business_days", 0)
    badge = f"`{bd}営業日`"
    if getattr(v, "source", "email") == "slack":
        who = f"*{v.sender}*" if getattr(v, "sender", "") else ""
        body = (getattr(v, "snippet", "") or "").replace("\n", " ").strip()
        ch = getattr(v, "subject", "") or "Slack"
        line = f"{badge}　メンション {who} in {ch}".rstrip()
        return line + (f"\n_{body}_" if body else "")
    cat = _CAT_LABEL.get(v.category, "未返信")
    line = f"{badge}　〔{cat}〕 *{_short_sender(getattr(v, 'sender', ''))}* — {_remind_subject_link(v)}"
    # §V4 単一アプリ化：返信は markdown リンク（`<url|…>`）で出す。button だと OpenClaw が
    # Socket Mode を握る共用アプリでは interaction が宙に浮き警告が出るが、リンクは interaction
    # ゼロ＝ack 不要で安全。クリック→ /reply（署名検証→全返信下書き作成→Gmail該当スレッドへ302）。
    ru = getattr(v, "reply_url", None)
    if ru:
        line += f"　<{ru}|✏️ 対応する>"
    return line


def _subject_link(it: DigestItem) -> str:
    subj = (it.subject or "(件名なし)").replace("|", "/").replace(">", "")
    return f"<{it.gmail_link}|{subj}>" if it.gmail_link else subj


def _item_badges(it: DigestItem) -> str:
    """行動を分けるシグナルだけ行頭バッジ化（締切→要確認の順）。下書きは補助に降格。"""
    b = []
    if _has_deadline(it):
        b.append("`締切`")
    if it.needs_review:
        b.append("`要確認`")
    return "".join(x + " " for x in b)


def _item_text(it: DigestItem) -> str:
    """要対応メール：行頭バッジ(締切/要確認) → 〔カテゴリ〕 → *相手* → 件名(link) / 補助行(下書き・要約)。"""
    cat = _CAT_LABEL.get(it.category, it.category.value)
    head = f"{_item_badges(it)}〔{cat}〕 *{_short_sender(it.sender)}*　{_subject_link(it)}"
    sub = []
    if it.draft:
        sub.append("下書き準備済み")
    if it.summary.one_liner:
        sub.append(it.summary.one_liner)
    return head + (f"\n_{' ・ '.join(sub)}_" if sub else "")


def _sorted(items: list[DigestItem]) -> list[DigestItem]:
    return sorted(items, key=lambda it: it.priority)  # BASE_PRIORITY順＝緊急度が自然にソート


def _reminders_sorted(d: Digest) -> list:
    return sorted(d.reminders, key=lambda v: -getattr(v, "business_days", 0))  # 放置が長い順


def _quiet_line(d: Digest) -> str:
    """一般（件数のみ）。読むものでなく「無視してよい量」を1行で。0件なら空。"""
    if not d.quiet_counts:
        return ""
    parts = [
        f"{_CAT_LABEL.get(c, getattr(c, 'value', str(c)))} {n}"
        for c, n in d.quiet_counts.items()
        if n
    ]
    return "一般（後でまとめて）： " + " ・ ".join(parts) if parts else ""


def _top_focus(d: Digest, to_items: list[DigestItem]) -> str:
    """最優先1件を名指し（未返信の最長放置 → 無ければ要対応の先頭）。"""
    if d.reminders:
        v = max(d.reminders, key=lambda x: getattr(x, "business_days", 0))
        bd = getattr(v, "business_days", 0)
        if getattr(v, "source", "email") == "slack":
            return f"{getattr(v, 'sender', '')}のメンション（{bd}営業日）"
        return f"{_short_sender(getattr(v, 'sender', ''))}「{getattr(v, 'subject', '')}」（{bd}営業日）"
    if to_items:
        it = _sorted(to_items)[0]
        return f"{_short_sender(it.sender)}「{it.subject}」"
    return ""


def _summary_line(d: Digest, to_items: list[DigestItem]) -> str:
    """最上部サマリー：今日の総量＋最優先1件。10秒で『今日まず何を』を確定させる。"""
    parts = []
    if d.reminders:
        parts.append(f"未返信 {len(d.reminders)}件")
    if to_items:
        parts.append(f"要対応 {len(to_items)}件")
    if d.calendar_events:
        parts.append(f"予定 {len(d.calendar_events)}件")
    nq = sum(d.quiet_counts.values()) if d.quiet_counts else 0
    if nq:
        parts.append(f"一般 {nq}件")
    head = " ・ ".join(parts) if parts else "今日は静か"
    top = _top_focus(d, to_items)
    return f"*今日のサマリー*： {head}" + (f"\n最優先 → {top}" if top else "")


# ── テキスト（dry-run）─────────────────────────────────────────────────────────
def render_digest_text(d: Digest) -> str:
    to_items, _cc = _split_to_cc(d.items)  # Cc は要対応から除外（一覧/件数は出さない）
    lines = [f"メールサマリー — {_date_label(d)}"]
    empty = not d.items and not d.quiet_counts and not _has_calendar(d) and not d.reminders
    if empty:
        lines.append("\n今日は静かです。")
        return "\n".join(lines)
    lines.append("\n" + _summary_line(d, to_items).replace("*", ""))
    if d.reminders:
        lines.append("\n未返信 — 今日中に")
        for v in _reminders_sorted(d):
            for ln in _reminder_line(v).split("\n"):
                lines.append(f"  {ln.replace('`', '').replace('_', '')}")
    if to_items:
        lines.append(f"\n要対応メール（{len(to_items)}件）")
        for it in _sorted(to_items)[:_TO_TOP]:
            for ln in _item_text(it).split("\n"):
                lines.append(f"  {ln.replace('`', '').replace('_', '')}")
        if len(to_items) > _TO_TOP:
            lines.append(f"  ほか{len(to_items) - _TO_TOP}件")
    quiet = _quiet_line(d)
    if quiet:
        lines.append("\n" + quiet)
    if _has_calendar(d):
        lines.append("")
        lines.extend(line.replace("*", "") for line in _calendar_full_lines(d))
    if any(it.draft for it in to_items):
        lines.append("\n下書きは未送信。Gmailで確認のうえ送信してください。")
    return "\n".join(lines)


# ── Slack Block Kit ────────────────────────────────────────────────────────────
def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:_MAX_TEXT]}}


def _ctx(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:_MAX_TEXT]}]}


def _divider() -> dict:
    return {"type": "divider"}


def render_slack_blocks(d: Digest, *, interactive: bool = False) -> list[dict]:
    """Slack Block Kit。構成：ヘッダ→サマリー→[未返信]→[要対応]→[一般]→[予定]→フッタ。

    各論理グループ間に divider。緊急度を行頭バッジに、補助情報は context に降格。≤49ブロック。
    """
    to_items, _cc = _split_to_cc(d.items)  # Cc は要対応から除外（一覧/件数は出さない）
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"メールサマリー — {_date_label(d)}"},
        },
    ]
    if not d.items and not d.quiet_counts and not _has_calendar(d) and not d.reminders:
        blocks.append(_section("*今日は静かです* — 新着の未読メールはありません。"))
        return blocks

    truncated = 0

    def _room() -> bool:
        return len(blocks) < _MAX_BLOCKS - 2

    # サマリー（最上部・全体像＋最優先1件）
    blocks.append(_section(_summary_line(d, to_items)))

    # 未返信（放置が長い順・行頭に営業日バッジ・ボタン）＝最優先
    if d.reminders:
        blocks.append(_divider())
        blocks.append(_section("*未返信 — 今日中に対応*"))
        for v in _reminders_sorted(d):
            if not _room():
                truncated += 1
                continue
            blocks.append(_section(_reminder_line(v)))
            if interactive and _room():
                blocks.append(_reminder_actions(v.thread_id, getattr(v, "reply_url", None)))

    # 要対応メール（Top5・行頭に締切/要確認バッジ＋〔カテゴリ語〕）
    if to_items:
        blocks.append(_divider())
        blocks.append(_section(f"*要対応メール*（{len(to_items)}件）"))
        for it in _sorted(to_items)[:_TO_TOP]:
            if not _room():
                truncated += 1
                continue
            blocks.append(_section(_item_text(it)))
            if interactive and it.draft and _room():
                blocks.append(_item_actions(it.gmail_draft_id or it.thread_id))
        if len(to_items) > _TO_TOP:
            truncated += len(to_items) - _TO_TOP

    # 一般（件数のみ・無視してよい量）
    quiet = _quiet_line(d)
    if quiet and _room():
        blocks.append(_divider())
        blocks.append(_ctx(quiet))

    # 今日の予定（フル・下部）
    if _has_calendar(d) and _room():
        blocks.append(_divider())
        blocks.append(_section("\n".join(_calendar_full_lines(d))))

    # フッタ（1本に統合）
    foot = []
    if truncated:
        foot.append(f"ほか{truncated}件はGmailで確認")
    if any(it.draft for it in to_items):
        foot.append("下書きは未送信（Gmailで確認のうえ送信）")
    if foot and _room():
        blocks.append(_ctx("　·　".join(foot)))
    return blocks[:_MAX_BLOCKS]
