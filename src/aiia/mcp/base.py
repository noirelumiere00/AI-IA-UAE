"""MCPToolset の差込口（Protocol）。Phase1 は全同期（YAGNI）。

設計の肝＝「**送信系を構造的に含めない**」: この Protocol には send 系メソッドが存在しないため、
pipeline はそもそも送信を呼べない（型レベルの誤送信防止）。NeverSendGate と合わせて二重防御。

実装: FakeMCPToolset（テスト/ dry-run）／（将来）HarnessMCPToolset／WorkspaceGmailToolset(per-user OAuth)。
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from aiia.schemas import CalendarEvent, EmailThread


@runtime_checkable
class GmailTools(Protocol):
    """Gmail で許可される操作のみ（読取・下書き・ラベル）。send は意図的に無い。"""

    def search_threads(
        self, query: str, page_token: Optional[str] = None, max_results: int = 50
    ) -> dict: ...
    """未読等のスレッド一覧を返す。戻り値: {"threads": [{"thread_id": str}], "next_page_token": str|None}"""

    def get_thread(self, thread_id: str) -> EmailThread: ...
    """スレッド本体を正規化した EmailThread で返す。"""

    def list_drafts(self, thread_id: Optional[str] = None) -> dict: ...
    """既存下書きの一覧。戻り値: {"drafts": [{"thread_id": str}]}（重複下書き防止に使う）。"""

    def create_draft(self, *, thread_id: str, subject: str, body: str) -> str: ...
    """下書きを作成（送信しない）。戻り値: draft_id。"""

    def label_thread(self, *, thread_id: str, label: str) -> None: ...
    """スレッドにラベルを付与（例: AIIA/CLIENT_URGENT）。"""


@runtime_checkable
class SlackTools(Protocol):
    """Slack は「下書き/プレビュー」のみ。実送信メソッドは持たせない。"""

    def send_draft(self, *, channel: str, blocks: list, text: str) -> str: ...
    """Slack に下書き/プレビューとして提示。戻り値: 識別子。"""


@runtime_checkable
class CalendarTools(Protocol):
    """カレンダーは**読み取りのみ**（events.insert/delete/update は持たせない＝書込防止）。"""

    def list_today_events(self) -> list[CalendarEvent]: ...
    """本人カレンダー(primary)の今日の予定（時刻順）。取得不可は例外を投げる。"""


@runtime_checkable
class MCPToolset(Protocol):
    """道具一式。gmail / slack をネスト保持（tools.gmail.search_threads のように使う）。

    属性は読み取り専用プロパティ（共変）にする＝実装側が GmailTools のサブ型(FakeGmail 等)を
    持っていても代入可能（不変属性だと型不一致になるため）。
    """

    @property
    def gmail(self) -> GmailTools: ...
    @property
    def slack(self) -> SlackTools: ...
    @property
    def calendar(self) -> Optional[CalendarTools]: ...
