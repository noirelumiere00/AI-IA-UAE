"""型契約（pydantic）。I/O・SDK import 無し → pipelineの純粋性とテスト容易性を担保。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

Tone = Literal["neutral", "urgent", "frustrated", "positive", "formal"]
PriorityLabel = Literal["緊急", "高", "中", "低"]
Handling = Literal["定型", "非定型"]
RecipientKind = Literal["to", "cc", "unknown"]  # 直接宛(To)/情報共有(Ccのみ)/不明


class Category(str, Enum):
    CLIENT_URGENT = "CLIENT_URGENT"
    CLIENT_NORMAL = "CLIENT_NORMAL"
    PRESS_MEDIA = "PRESS_MEDIA"
    VENDOR_PARTNER = "VENDOR_PARTNER"
    INTERNAL = "INTERNAL"
    FINANCE_LEGAL = "FINANCE_LEGAL"
    NEWSLETTER = "NEWSLETTER"


# 優先度（小さいほど上位）。is_vip は -5 補正（triage.compute_priority）。
BASE_PRIORITY: dict[Category, int] = {
    Category.CLIENT_URGENT: 0,
    Category.PRESS_MEDIA: 10,
    Category.FINANCE_LEGAL: 20,
    Category.CLIENT_NORMAL: 30,
    Category.VENDOR_PARTNER: 40,
    Category.INTERNAL: 50,
    Category.NEWSLETTER: 90,
}

CATEGORY_EMOJI: dict[Category, str] = {
    Category.CLIENT_URGENT: "🔴",
    Category.PRESS_MEDIA: "🟠",
    Category.FINANCE_LEGAL: "🟣",
    Category.CLIENT_NORMAL: "🟡",
    Category.VENDOR_PARTNER: "🔵",
    Category.INTERNAL: "🟢",
    Category.NEWSLETTER: "⚪",
}


class EmailMessage(BaseModel):
    message_id: str
    sender: str = ""
    sender_domain: str = ""
    to: list[str] = Field(default_factory=list)
    subject: str = ""
    date: Optional[datetime] = None
    snippet: str = ""
    body_text: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)  # Gmail labelIds（SENT 判定等）


class EmailThread(BaseModel):
    thread_id: str
    subject: str = ""
    messages: list[EmailMessage] = Field(default_factory=list)
    has_existing_draft: bool = False
    labels: list[str] = Field(default_factory=list)

    @property
    def latest(self) -> Optional[EmailMessage]:
        return self.messages[-1] if self.messages else None

    @property
    def latest_real(self) -> Optional[EmailMessage]:
        """下書き(DRAFT)を除いた最新メッセージ＝会話の実状態。作りかけ下書きで状態を汚さない。"""
        for m in reversed(self.messages):
            if "DRAFT" not in m.labels:
                return m
        return None

    @property
    def last_inbound(self) -> Optional[EmailMessage]:
        """相手から来た最新メッセージ（自分のDRAFT/SENTを除外）＝差出人表示・返信先・本文の基準。"""
        for m in reversed(self.messages):
            if "DRAFT" not in m.labels and "SENT" not in m.labels:
                return m
        return self.latest_real

    @property
    def latest_is_from_self(self) -> bool:
        """本人が最後に送ったか（下書きは無視・Gmailの SENT ラベルで判定）。"""
        m = self.latest_real
        return bool(m and "SENT" in m.labels)

    @property
    def sender(self) -> str:
        m = self.last_inbound  # 差出人＝相手（自分の下書き/送信を除外）
        return m.sender if m else ""

    @property
    def sender_domain(self) -> str:
        m = self.last_inbound
        return m.sender_domain if m else ""

    @property
    def body(self) -> str:
        m = self.last_inbound
        return (m.body_text or m.snippet) if m else ""


class ClassificationResult(BaseModel):
    category: Category
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    is_vip: bool = False
    is_actionable: bool = False  # 返信/対応が必要(質問・依頼・締切・名指し)。一般/FYIはFalse。
    recipient_kind: RecipientKind = "unknown"  # 本人が To(直接) か Cc のみ(情報共有) か
    priority_label: PriorityLabel = "中"
    handling: Handling = "非定型"
    amount_jpy: Optional[int] = None
    key_person: Optional[str] = None
    needs_review: bool = False


class ActionItem(BaseModel):
    text: str
    source_quote: str = ""
    kind: Literal["action", "question", "deadline"] = "action"
    deadline: Optional[datetime] = None

    @model_validator(mode="after")
    def _quote_required_for_deadline(self) -> "ActionItem":
        # 締切は「本文に明示」が前提。原文引用が無ければ無効（推測禁止を構造的に強制）。
        if (self.kind == "deadline" or self.deadline is not None) and not self.source_quote.strip():
            raise ValueError("deadline には source_quote（原文引用）が必須です（推測禁止）")
        return self


class ThreadSummary(BaseModel):
    thread_id: str
    one_liner: str = ""
    tone: Tone = "neutral"
    action_items: list[ActionItem] = Field(default_factory=list)


class DraftReply(BaseModel):
    thread_id: str
    subject: str = ""
    body: str = ""
    rationale: str = ""
    keigo_level: int = 3


class DigestItem(BaseModel):
    thread_id: str
    category: Category
    priority: int
    subject: str
    sender: str
    summary: ThreadSummary
    classification: ClassificationResult
    draft: Optional[DraftReply] = None
    gmail_link: Optional[str] = None
    gmail_draft_id: Optional[str] = None  # 非dry_runで作成したGmail下書きID（M2ボタンが編集/削除/送信に使う）
    needs_review: bool = False


class CalendarEvent(BaseModel):
    event_id: str = ""
    title: str = ""
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    all_day: bool = False
    # accepted / tentative / needsAction / declined（declined は表示前に除外）
    response_status: str = "accepted"
    conference_url: Optional[str] = None  # Meet/Zoom 等の参加URL
    location: Optional[str] = None         # 会議室/場所
    description: Optional[str] = None       # 説明（redaction対象・URL/会議室がここに入ることも）


class ReminderView(BaseModel):
    thread_id: str
    category: Category
    subject: str = ""
    sender: str = ""
    snippet: str = ""           # 軽い手がかり（本文要約は「対応する」押下時にon-demand）
    business_days: int = 0      # 未返信の営業日数
    first_seen: Optional[datetime] = None
    gmail_link: Optional[str] = None


class Digest(BaseModel):
    generated_at: datetime
    user_id: str
    items: list[DigestItem] = Field(default_factory=list)
    counts_by_category: dict[Category, int] = Field(default_factory=dict)
    quiet_counts: dict[Category, int] = Field(default_factory=dict)
    reminders: list[ReminderView] = Field(default_factory=list)  # 重要×未返信×N営業日
    calendar_events: list[CalendarEvent] = Field(default_factory=list)
    calendar_failed: bool = False  # カレンダー取得失敗（0件＝予定なし と区別）
    processed: int = 0
    elapsed_seconds: float = 0.0


class AgentResult(BaseModel):
    digest: Digest
    drafts_created: int = 0
    labels_applied: int = 0
    redactions: int = 0
    dry_run: bool = True
