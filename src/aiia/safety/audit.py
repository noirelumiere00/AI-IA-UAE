"""監査ログ（JSONL・メタデータのみ）。本文/送信者アドレス/下書き本文は保存しない。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 監査に残してよいメタデータのキー（本文・PIIは含めない）
_ALLOWED_META = {
    "thread_id", "category", "model", "input_tokens", "output_tokens",
    "redactions", "tool", "draft_id", "label", "count", "error", "events",
}


class AuditLog:
    def __init__(self, user_id: str, path: Optional[Path] = None, run_id: Optional[str] = None):
        self.user_id = user_id
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.path = path
        self.records: list[dict] = []

    def record(self, event: str, **meta) -> dict:
        row: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "user_id": self.user_id,
            "event": event,
        }
        for k, v in meta.items():
            if k in _ALLOWED_META:
                row[k] = v
        self.records.append(row)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
