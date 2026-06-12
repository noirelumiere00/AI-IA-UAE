"""カレンダー通知の二重送信防止（claim-before-send）。DynamoDB / InMemory。

PK=user_email(HASH) / notify_key(RANGE)=「{JST日付}#{event_id}」。TTL(expires_at)で翌日自動失効。
`claim()` は条件付き put（既に存在すれば失敗）で **原子的に1回だけ**通知を許す
（毎分ポーリングで同じ予定を何度も拾っても通知は1回）。token_store と同じ DI パターン。
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional, Protocol


class NotifiedStore(Protocol):
    def claim(self, user_email: str, notify_key: str) -> bool:
        """初回なら True（通知してよい）。既に通知済みなら False。"""
        ...


class InMemoryNotifiedStore:
    """テスト/単一プロセス用。"""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def claim(self, user_email: str, notify_key: str) -> bool:
        k = (user_email, notify_key)
        if k in self._seen:
            return False
        self._seen.add(k)
        return True


class DynamoDbNotifiedStore:
    """本番。テーブル `aiia-notified-events`（PK=user_email, SK=notify_key, TTL=expires_at）。"""

    def __init__(
        self,
        table_name: str,
        *,
        client: Any = None,
        region: Optional[str] = None,
        ttl_seconds: int = 172800,  # 2日でTTL失効（翌日には消える）
    ) -> None:
        self._table = table_name
        self._client = client
        self._region = region
        self._ttl = ttl_seconds

    def _c(self) -> Any:
        if self._client is None:
            import boto3  # 遅延 import

            region = self._region or os.environ.get("AWS_REGION") or "ap-northeast-1"
            self._client = boto3.client("dynamodb", region_name=region)
        return self._client

    def claim(self, user_email: str, notify_key: str) -> bool:
        exp = str(int(time.time()) + self._ttl)
        try:
            self._c().put_item(
                TableName=self._table,
                Item={
                    "user_email": {"S": user_email},
                    "notify_key": {"S": notify_key},
                    "expires_at": {"N": exp},
                },
                # 既存（=通知済み）なら ConditionalCheckFailed で弾く＝原子的 claim
                ConditionExpression="attribute_not_exists(user_email)",
            )
            return True
        except Exception as e:  # noqa: BLE001
            code = ""
            resp = getattr(e, "response", None)
            if isinstance(resp, dict):
                code = resp.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                return False  # 既に通知済み（別ポーリングが先に claim）
            raise
