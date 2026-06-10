"""返信リマインドのルール（純粋関数中心）。**誤検知最小化を最優先**。

未返信判定は From 文字列一致ではなく Gmail の SENT ラベル等の事実で行う
（別端末/エイリアス/表示名に強い）。To限定・要返信必須・OOO/メルマガ除外で「他人の球/蒸し返し」を防ぐ。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aiia.schemas import Category, DigestItem, EmailThread, ReminderView
from aiia.state.reminder_store import ReminderRecord, ReminderStore

_JST = timezone(timedelta(hours=9))

# リマインド母集団となる重要カテゴリ
IMPORTANT_CATS = frozenset({
    Category.CLIENT_URGENT, Category.CLIENT_NORMAL, Category.PRESS_MEDIA, Category.FINANCE_LEGAL,
})
# カテゴリ別しきい値（営業日）。URGENT/PRESS は翌営業日（失注リスク）。
_THRESHOLD_BIZ_DAYS = {
    Category.CLIENT_URGENT.value: 1, Category.PRESS_MEDIA.value: 1,
    Category.CLIENT_NORMAL.value: 3, Category.FINANCE_LEGAL.value: 3,
}
DEFAULT_THRESHOLD = 3
MAX_SNOOZE = 3
EXPIRE_BIZ_DAYS = 14


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


def decide(rec: ReminderRecord, thread: EmailThread, now: datetime) -> ReminderDecision:
    if not is_unreplied(thread):
        return ReminderDecision("dismiss_replied")
    if rec.snooze_until and now < rec.snooze_until:
        return ReminderDecision("snoozed")
    bd = business_days_between(rec.first_seen, now)
    if rec.snooze_count >= MAX_SNOOZE or bd >= EXPIRE_BIZ_DAYS:  # 永久催促を構造的に禁止
        return ReminderDecision("dismiss_expired", bd)
    if bd >= threshold_for(rec.category):
        return ReminderDecision("show", bd)
    return ReminderDecision("wait", bd)


# ── 状態操作（store を get/upsert で薄く操作）─────────────────────────────────
def track(store: ReminderStore, user_email: str, thread_id: str, category: str, now: datetime) -> None:
    """重要×To×要返信スレを追跡開始（既存は first_seen を保持＝idempotent）。"""
    if store.get(user_email, thread_id) is None:
        store.upsert(ReminderRecord(user_email, thread_id, category, first_seen=now))


def snooze(store: ReminderStore, user_email: str, thread_id: str, now: datetime, days: int = 3) -> None:
    rec = store.get(user_email, thread_id)
    if rec:
        rec.snooze_until = now + timedelta(days=days)
        rec.snooze_count += 1
        rec.last_action_at = now
        rec.status = "active"
        store.upsert(rec)


def dismiss(store: ReminderStore, user_email: str, thread_id: str, now: datetime, *, mute: bool = False) -> None:
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
    store: ReminderStore, gmail: Any, user_email: str,
    today_items: list[DigestItem], threads_by_id: dict[str, EmailThread],
    now: datetime, *, write: bool = True,
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
        views.append(ReminderView(
            thread_id=rec.thread_id,
            category=Category(rec.category) if rec.category in Category._value2member_map_ else Category.CLIENT_NORMAL,
            subject=thread.subject,
            sender=thread.sender,
            snippet=(m.snippet if m else "")[:140],
            business_days=dec.business_days,
            first_seen=rec.first_seen,
            gmail_link=gmail_link(rec.thread_id),
        ))
    views.sort(key=lambda v: (-v.business_days, v.subject))  # 滞留が長い順
    return views
