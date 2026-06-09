"""返信リマインドの状態永続化（DynamoDB / InMemory）。

PK=user_email(HASH) / thread_id(RANGE)。**論理削除**（status）で誤解除を取り消し可能に。
件名/本文は保存しない（PII最小・表示は live thread から再取得）。token_store と同じ DI パターン。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol


@dataclass
class ReminderRecord:
    user_email: str
    thread_id: str
    category: str  # Category の値（しきい値判定に使用）
    first_seen: datetime
    last_action_at: Optional[datetime] = None
    snooze_until: Optional[datetime] = None
    snooze_count: int = 0
    status: str = "active"  # active | dismissed | muted | expired
    reply_draft_id: Optional[str] = None  # 「対応する」で作った下書きID（対応済み時に孤児を片付ける）


class ReminderStore(Protocol):
    def get(self, user_email: str, thread_id: str) -> Optional[ReminderRecord]: ...
    def upsert(self, rec: ReminderRecord) -> None: ...
    def list_active(self, user_email: str) -> list[ReminderRecord]: ...


class InMemoryReminderStore:
    """テスト/単一プロセス用。"""

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], ReminderRecord] = {}

    def get(self, user_email: str, thread_id: str) -> Optional[ReminderRecord]:
        return self._d.get((user_email, thread_id))

    def upsert(self, rec: ReminderRecord) -> None:
        self._d[(rec.user_email, rec.thread_id)] = rec

    def list_active(self, user_email: str) -> list[ReminderRecord]:
        return [r for (e, _), r in self._d.items() if e == user_email and r.status == "active"]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _dt(s: Any) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


class DynamoDbReminderStore:
    """本番。テーブル `aiia-reminder-state`（PK=user_email, SK=thread_id）。"""

    def __init__(self, table_name: str, *, client: Any = None, region: Optional[str] = None) -> None:
        self._table = table_name
        self._client = client
        self._region = region

    def _c(self) -> Any:
        if self._client is None:
            import boto3  # 遅延 import

            # region: 明示指定 → AWS_REGION env → 東京。botocore は AWS_REGION を
            # 既定セッションで拾わないことがあり、未指定だと us-east-1 等にズレてテーブル不在になるため明示。
            region = self._region or os.environ.get("AWS_REGION") or "ap-northeast-1"
            self._client = boto3.client("dynamodb", region_name=region)
        return self._client

    def _to_item(self, r: ReminderRecord) -> dict:
        item = {
            "user_email": {"S": r.user_email},
            "thread_id": {"S": r.thread_id},
            "category": {"S": r.category},
            "first_seen": {"S": r.first_seen.isoformat()},
            "snooze_count": {"N": str(r.snooze_count)},
            "status": {"S": r.status},
        }
        if r.last_action_at:
            item["last_action_at"] = {"S": r.last_action_at.isoformat()}
        if r.snooze_until:
            item["snooze_until"] = {"S": r.snooze_until.isoformat()}
        if r.reply_draft_id:
            item["reply_draft_id"] = {"S": r.reply_draft_id}
        return item

    def _from_item(self, it: dict) -> ReminderRecord:
        return ReminderRecord(
            user_email=it["user_email"]["S"],
            thread_id=it["thread_id"]["S"],
            category=it.get("category", {}).get("S", ""),
            first_seen=_dt(it.get("first_seen", {}).get("S")) or datetime.min,
            last_action_at=_dt(it.get("last_action_at", {}).get("S")),
            snooze_until=_dt(it.get("snooze_until", {}).get("S")),
            snooze_count=int(it.get("snooze_count", {}).get("N", "0")),
            status=it.get("status", {}).get("S", "active"),
            reply_draft_id=it.get("reply_draft_id", {}).get("S"),
        )

    def get(self, user_email: str, thread_id: str) -> Optional[ReminderRecord]:
        resp = self._c().get_item(
            TableName=self._table,
            Key={"user_email": {"S": user_email}, "thread_id": {"S": thread_id}},
        )
        it = resp.get("Item")
        return self._from_item(it) if it else None

    def upsert(self, rec: ReminderRecord) -> None:
        self._c().put_item(TableName=self._table, Item=self._to_item(rec))

    def list_active(self, user_email: str) -> list[ReminderRecord]:
        resp = self._c().query(
            TableName=self._table,
            KeyConditionExpression="user_email = :u",
            FilterExpression="#st = :a",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":u": {"S": user_email}, ":a": {"S": "active"}},
        )
        return [self._from_item(it) for it in resp.get("Items", [])]
