"""トリアージのルール（ヒューリスティック）。純粋関数・I/O無し。

LLM版（providers/anthropic_llm）はこのヒントを尊重しつつ本文で確定する。
HeuristicLLM はこのルールのみで分類する（ネットワーク不要・決定論的）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .schemas import BASE_PRIORITY, Category, ClassificationResult, EmailThread

if TYPE_CHECKING:
    from .config import UserConfig

# 緊急語（日英）
URGENT_KEYWORDS = [
    "至急", "緊急", "本日中", "今日中", "本日まで", "大至急", "急ぎ", "即時",
    "asap", "urgent", "immediately", "deadline", "eod", "by today",
]
# プレス/媒体
PRESS_KEYWORDS = ["取材", "インタビュー", "掲載", "記者", "プレス", "press", "interview", "media", "coverage", "報道"]
# 金融/法務
FINANCE_LEGAL_KEYWORDS = ["契約", "請求", "見積", "nda", "守秘", "法務", "invoice", "contract", "payment", "支払", "決裁"]
# 取引先/ベンダー文脈
VENDOR_KEYWORDS = ["請求書", "見積書", "発注", "納品", "invoice", "purchase order", "po番号"]
CONFIDENTIAL_LABELS = {"confidential", "機密", "社外秘"}

_AMOUNT_RE = re.compile(r"([0-9０-９,，]+)\s*(万円|百万|円)")
_KEY_PERSON_RE = re.compile(r"(社長|部長|課長|本部長|cfo|ceo|coo|役員|取締役)", re.IGNORECASE)


@dataclass
class Hints:
    sender_domain: str = ""
    is_vip: bool = False
    has_urgent_kw: bool = False
    is_press: bool = False
    is_finance_legal: bool = False
    is_vendor: bool = False
    is_internal: bool = False
    is_client: bool = False
    is_newsletter: bool = False
    is_confidential: bool = False
    amount_jpy: Optional[int] = None
    key_person: Optional[str] = None
    matched: list[str] = field(default_factory=list)


def _text_of(thread: EmailThread) -> str:
    parts = [thread.subject]
    for m in thread.messages:
        parts.append(m.subject)
        parts.append(m.snippet)
        parts.append(m.body_text)
    return "\n".join(p for p in parts if p)


def _any(text_lower: str, words: list[str]) -> Optional[str]:
    for w in words:
        if w.lower() in text_lower:
            return w
    return None


def _parse_amount(text: str) -> Optional[int]:
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    num = m.group(1).translate(str.maketrans("０１２３４５６７８９，", "0123456789,")).replace(",", "")
    if not num.isdigit():
        return None
    base = int(num)
    unit = m.group(2)
    if unit == "万円":
        return base * 10_000
    if unit == "百万":
        return base * 1_000_000
    return base


def heuristic_signals(thread: EmailThread, user: "UserConfig") -> Hints:
    text = _text_of(thread)
    low = text.lower()
    domain = thread.sender_domain.lower()
    sender = thread.sender.lower()

    h = Hints(sender_domain=domain)
    h.is_vip = any(s.lower() in sender for s in user.vip_senders) or domain in {d.lower() for d in user.vip_domains}
    u = _any(low, URGENT_KEYWORDS)
    h.has_urgent_kw = u is not None
    if u:
        h.matched.append(f"緊急語: {u}")
    p = _any(low, PRESS_KEYWORDS)
    h.is_press = p is not None
    if p:
        h.matched.append(f"媒体語: {p}")
    fl = _any(low, FINANCE_LEGAL_KEYWORDS)
    h.is_finance_legal = fl is not None
    vk = _any(low, VENDOR_KEYWORDS)
    h.is_vendor = vk is not None or domain in {d.lower() for d in user.partner_domains}
    h.is_internal = bool(user.internal_domain) and domain == user.internal_domain.lower()
    h.is_client = domain in {d.lower() for d in user.client_domains}
    # ニュースレター: List-Unsubscribe ヘッダ or 一括配信特徴
    headers = {}
    if thread.latest:
        headers = {k.lower(): v for k, v in thread.latest.headers.items()}
    h.is_newsletter = "list-unsubscribe" in headers or "noreply" in sender or "no-reply" in sender
    h.is_confidential = bool(CONFIDENTIAL_LABELS & {l.lower() for l in thread.labels})
    h.amount_jpy = _parse_amount(text)
    kp = _KEY_PERSON_RE.search(text)
    h.key_person = kp.group(1) if kp else None
    return h


def classify_from_hints(hints: Hints) -> ClassificationResult:
    """ルーブリック（先勝ち）でカテゴリを決定。"""
    reasons = list(hints.matched)
    priority_label = "緊急" if hints.has_urgent_kw else ("高" if hints.is_vip else "中")
    handling: str = "非定型"

    def res(cat: Category, conf: float, why: str, review: bool = False) -> ClassificationResult:
        return ClassificationResult(
            category=cat,
            confidence=conf,
            reasons=reasons + [why],
            is_vip=hints.is_vip,
            priority_label=priority_label,  # type: ignore[arg-type]
            handling=handling,  # type: ignore[arg-type]
            amount_jpy=hints.amount_jpy,
            key_person=hints.key_person,
            needs_review=review,
        )

    if hints.is_finance_legal and (hints.is_vendor or hints.amount_jpy):
        return res(Category.FINANCE_LEGAL, 0.85, "法務/金融文脈")
    if hints.is_vip:
        if hints.has_urgent_kw:
            return res(Category.CLIENT_URGENT, 0.92, "VIP＋緊急語")
        return res(Category.CLIENT_NORMAL, 0.8, "VIP（非緊急）")
    if hints.is_press:
        return res(Category.PRESS_MEDIA, 0.85, "媒体/取材")
    if hints.is_client:
        if hints.has_urgent_kw:
            return res(Category.CLIENT_URGENT, 0.85, "クライアント＋緊急語")
        return res(Category.CLIENT_NORMAL, 0.8, "クライアント（非緊急）")
    if hints.is_vendor:
        return res(Category.VENDOR_PARTNER, 0.78, "取引先/請求文脈")
    if hints.is_internal:
        return res(Category.INTERNAL, 0.8, "社内ドメイン")
    if hints.is_newsletter:
        return res(Category.NEWSLETTER, 0.85, "一括配信/List-Unsubscribe")
    # 未該当: 低信頼で要確認
    return res(Category.CLIENT_NORMAL, 0.4, "ヒューリスティック非該当（要確認）", review=True)


def compute_priority(cls: ClassificationResult) -> int:
    p = BASE_PRIORITY[cls.category]
    if cls.is_vip:
        p -= 5
    return p
