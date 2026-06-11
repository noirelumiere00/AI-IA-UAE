"""返信リマインドのルール（純粋関数中心）。**誤検知最小化を最優先**。

未返信判定は From 文字列一致ではなく Gmail の SENT ラベル等の事実で行う
（別端末/エイリアス/表示名に強い）。To限定・要返信必須・OOO/メルマガ除外で「他人の球/蒸し返し」を防ぐ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aiia.schemas import Category, DigestItem, EmailThread, ReminderView
from aiia.state.reminder_store import ReminderRecord, ReminderStore

_JST = timezone(timedelta(hours=9))

# リマインド母集団となる重要カテゴリ
IMPORTANT_CATS = frozenset(
    {
        Category.CLIENT_URGENT,
        Category.CLIENT_NORMAL,
        Category.PRESS_MEDIA,
        Category.FINANCE_LEGAL,
    }
)
# カテゴリ別しきい値（営業日）。URGENT/PRESS は翌営業日（失注リスク）。
_THRESHOLD_BIZ_DAYS = {
    Category.CLIENT_URGENT.value: 1,
    Category.PRESS_MEDIA.value: 1,
    Category.CLIENT_NORMAL.value: 3,
    Category.FINANCE_LEGAL.value: 3,
}
DEFAULT_THRESHOLD = 3
MAX_SNOOZE = 3
EXPIRE_BIZ_DAYS = 14

# Slackリマインダの thread_id 名前空間。email の Gmail thread_id と衝突させないための接頭辞。
# 形式: f"slack:{channel_id}:{ts}"（ts はドット入りなので復元は split(":", 2)）。
SLACK_KEY_PREFIX = "slack:"


def threshold_for(category_value: str) -> int:
    return _THRESHOLD_BIZ_DAYS.get(category_value, DEFAULT_THRESHOLD)


def gmail_link(thread_id: str) -> str:
    # #all/ は会話をインラインで開き、下書きがあれば返信欄に本文＋署名がリッチ表示される
    # （実測：#inbox/ はポップアップ、#all/ は会話インライン＝理想形で安定）。
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


def is_unreplied(thread: EmailThread) -> bool:
    """本人が未返信か。下書きは無視し、SENT/Auto-Submitted/bulk/noreply で誤検知を排除。"""
    m = thread.latest_real  # 作りかけ下書きを除いた実状態で判定
    if m is None:
        return False
    if thread.latest_is_from_self:  # 本人が最後に送った＝返信済み（SENTラベル）
        return False
    h = {k.lower(): v for k, v in m.headers.items()}
    auto = h.get("auto-submitted", "").lower()
    if auto and auto != "no":  # OOO/自動応答
        return False
    if "list-id" in h or h.get("precedence", "").lower() in {"list", "bulk"}:
        return False  # メルマガ続報
    s = m.sender.lower()
    if "noreply" in s or "no-reply" in s:
        return False
    return True


def is_reminder_candidate(item: DigestItem) -> bool:
    """追跡対象：重要cat × To × 要返信。Cc/unknown・非actionable は除外（他人の球/FYIを蒸し返さない）。"""
    c = item.classification
    return item.category in IMPORTANT_CATS and c.recipient_kind == "to" and c.is_actionable


def business_days_between(start: datetime, end: datetime) -> int:
    """start〜end の営業日数（土日除外・JST暦日基準・同日/逆順は0）。"""
    s = start.astimezone(_JST).date()
    e = end.astimezone(_JST).date()
    days, d = 0, s
    while d < e:
        d += timedelta(days=1)
        if d.weekday() < 5:  # 月〜金
            days += 1
    return days


@dataclass
class ReminderDecision:
    action: str  # show | wait | snoozed | dismiss_replied | dismiss_expired
    business_days: int = 0


def decide_from_state(
    rec: ReminderRecord, *, unreplied: bool, now: datetime, threshold: int
) -> ReminderDecision:
    """source非依存の判定核。未返信フラグ・しきい値を外から渡す（email/slack 共用）。

    snooze/期限切れ(14営業日)/スヌーズ上限(3回) の寿命制御を email も slack も同じく継承する。
    """
    if not unreplied:
        return ReminderDecision("dismiss_replied")
    if rec.snooze_until and now < rec.snooze_until:
        return ReminderDecision("snoozed")
    bd = business_days_between(rec.first_seen, now)
    if rec.snooze_count >= MAX_SNOOZE or bd >= EXPIRE_BIZ_DAYS:  # 永久催促を構造的に禁止
        return ReminderDecision("dismiss_expired", bd)
    if bd >= threshold:
        return ReminderDecision("show", bd)
    return ReminderDecision("wait", bd)


def decide(rec: ReminderRecord, thread: EmailThread, now: datetime) -> ReminderDecision:
    """email 用：未返信判定は EmailThread から、しきい値はカテゴリから（挙動は従来どおり）。"""
    return decide_from_state(
        rec, unreplied=is_unreplied(thread), now=now, threshold=threshold_for(rec.category)
    )


# ── 状態操作（store を get/upsert で薄く操作）─────────────────────────────────
def track(
    store: ReminderStore, user_email: str, thread_id: str, category: str, now: datetime
) -> None:
    """重要×To×要返信スレを追跡開始（既存は first_seen を保持＝idempotent）。"""
    if store.get(user_email, thread_id) is None:
        store.upsert(ReminderRecord(user_email, thread_id, category, first_seen=now))


def snooze(
    store: ReminderStore, user_email: str, thread_id: str, now: datetime, days: int = 3
) -> None:
    rec = store.get(user_email, thread_id)
    if rec:
        rec.snooze_until = now + timedelta(days=days)
        rec.snooze_count += 1
        rec.last_action_at = now
        rec.status = "active"
        store.upsert(rec)


def dismiss(
    store: ReminderStore, user_email: str, thread_id: str, now: datetime, *, mute: bool = False
) -> None:
    rec = store.get(user_email, thread_id)
    if rec:
        rec.status = "muted" if mute else "dismissed"  # 論理削除（undoで復活可）
        rec.last_action_at = now
        store.upsert(rec)


def undo(store: ReminderStore, user_email: str, thread_id: str, now: datetime) -> None:
    rec = store.get(user_email, thread_id)
    if rec:
        rec.status = "active"
        rec.snooze_until = None
        rec.last_action_at = now
        store.upsert(rec)


def compute_reminders(
    store: ReminderStore,
    gmail: Any,
    user_email: str,
    today_items: list[DigestItem],
    threads_by_id: dict[str, EmailThread],
    now: datetime,
    *,
    write: bool = True,
) -> list[ReminderView]:
    """朝バッチ：①今日の重要×To×要返信を追跡 ②追跡中を再取得して未返信×しきい値を抽出。

    write=False（dry-run）は状態を変えない（プレビュー）。返信済み/期限切れは自動解除（write時）。
    """
    if write:
        for it in today_items:
            if is_reminder_candidate(it):
                track(store, user_email, it.thread_id, it.category.value, now)

    views: list[ReminderView] = []
    for rec in store.list_active(user_email):
        if rec.thread_id.startswith(SLACK_KEY_PREFIX):
            continue  # Slackリマインダは compute_slack_reminders が処理（gmail.get_thread に投げない）
        thread = threads_by_id.get(rec.thread_id)
        if thread is None:
            try:
                thread = gmail.get_thread(rec.thread_id)
            except Exception:  # noqa: BLE001 — 取得不能スレはスキップ（本処理は止めない）
                continue
        dec = decide(rec, thread, now)
        if dec.action in ("dismiss_replied", "dismiss_expired"):
            if write:
                dismiss(store, user_email, rec.thread_id, now)
            continue
        if dec.action != "show":
            continue
        m = thread.last_inbound  # 差出人/手がかりは相手の最新（自分の下書きを除外）
        views.append(
            ReminderView(
                thread_id=rec.thread_id,
                category=Category(rec.category)
                if rec.category in Category._value2member_map_
                else Category.CLIENT_NORMAL,
                subject=thread.subject,
                sender=thread.sender,
                snippet=(m.snippet if m else "")[:140],
                business_days=dec.business_days,
                first_seen=rec.first_seen,
                gmail_link=gmail_link(rec.thread_id),
            )
        )
    views.sort(key=lambda v: (-v.business_days, v.subject))  # 滞留が長い順
    return views


# ── Slack 未返信メンション ───────────────────────────────────────────────────
_SLACK_TOKEN_RE = re.compile(r"<[@#!][^>]*>")


def _slack_snippet(text: str) -> str:
    """表示用にSlackの `<@U..>` `<#C..|name>` `<!here>` 等のトークンを除去して読みやすく。"""
    return _SLACK_TOKEN_RE.sub("", text).replace("  ", " ").strip()


def compute_slack_reminders(
    store: ReminderStore,
    slack_user: Any,
    user_email: str,
    slack_user_id: str,
    now: datetime,
    *,
    threshold_biz_days: int = 1,
    lookback_days: int = 14,
    max_items: int = 5,
    write: bool = True,
) -> list[ReminderView]:
    """Slackの「自分宛メンション × 未返信」を検知して ReminderView 化（**誤検知最小化＝fail-closed**）。

    - 対象はスレッド内メンション。未返信判定が不能(None)/返信済み(True)なら**出さない**。
    - 既存の snooze/dismiss/期限切れ(14営業日)制御を共有（thread_id=f"slack:{ch}:{ts}"）。
    - email の compute_reminders とは別経路（slackキーは互いに skip）。dry-run は状態を変えない。
    """
    cutoff_ts = (now - timedelta(days=lookback_days)).timestamp()
    views: list[ReminderView] = []
    for h in slack_user.search_mentions(slack_user_id):
        ch, ts = h.get("channel_id") or "", h.get("ts") or ""
        text = h.get("text") or ""
        if not ch or not ts or h.get("is_im"):
            continue  # DM/グループDMは「メンション」概念が薄い→除外
        if h.get("user") == slack_user_id:
            continue  # 自分発は除外
        if (
            any(b in text for b in ("<!channel>", "<!here>", "<!everyone>"))
            and f"<@{slack_user_id}>" not in text
        ):
            continue  # ブロードキャストのみ（個人宛でない）は除外
        try:
            if float(ts) < cutoff_ts:
                continue  # lookback 超過
        except (TypeError, ValueError):
            continue
        thread_ts = h.get("thread_ts") or ts
        replied = slack_user.has_user_replied_after(ch, thread_ts, slack_user_id, ts)
        if replied is None or replied is True:
            continue  # fail-closed: 判定不能/返信済みは催促しない

        key = f"{SLACK_KEY_PREFIX}{ch}:{ts}"
        if write:
            track(store, user_email, key, Category.CLIENT_NORMAL.value, now)
        rec = store.get(user_email, key)
        if rec is None:
            rec = ReminderRecord(user_email, key, Category.CLIENT_NORMAL.value, first_seen=now)
        elif rec.status not in ("active",):
            continue  # 対応済み(dismissed)/もう通知しない(muted) は出さない（list_active相当の絞り）
        dec = decide_from_state(rec, unreplied=True, now=now, threshold=threshold_biz_days)
        if dec.action in ("dismiss_replied", "dismiss_expired"):
            if write:
                dismiss(store, user_email, key, now)
            continue
        if dec.action != "show":
            continue
        permalink = h.get("permalink") or ""
        author = h.get("username") or h.get("user") or ""
        chname = h.get("channel_name") or ""
        views.append(
            ReminderView(
                thread_id=key,
                category=Category.CLIENT_NORMAL,
                subject=(f"#{chname}" if chname else "Slack"),
                sender=(f"@{author}" if author else ""),
                snippet=_slack_snippet(text)[:140],
                business_days=dec.business_days,
                first_seen=rec.first_seen,
                source="slack",
                permalink=permalink,
                reply_url=permalink or None,  # [対応する]=permalink（既存url-button経路を流用）
                channel_id=ch,
                message_ts=ts,
            )
        )
    # 同一channelは最も滞留の長い1件に集約（活発chでの行数爆発を防ぐ）。tracking は per-message のまま、
    # 表示だけ代表1件＋「（ほかN件）」。dismiss/後で は代表メッセージに作用する。
    reps: dict[str, ReminderView] = {}
    extra: dict[str, int] = {}
    for v in sorted(views, key=lambda v: -v.business_days):
        ck = v.channel_id or v.thread_id
        if ck in reps:
            extra[ck] = extra.get(ck, 0) + 1
        else:
            reps[ck] = v
    collapsed: list[ReminderView] = []
    for ck, v in reps.items():
        n = extra.get(ck, 0)
        if n:
            v.subject = f"{v.subject}（ほか{n}件）"
        collapsed.append(v)
    collapsed.sort(key=lambda v: (-v.business_days, v.subject))
    return collapsed[:max_items]
