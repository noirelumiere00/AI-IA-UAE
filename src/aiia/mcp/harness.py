"""実データ harness: 実スレッド JSON を読み、pipeline に供給する MCPToolset。

設計図 S9「手協調」: 取得自体は外部（本AI が セッションの Gmail MCP `search_threads`/`get_thread`
を叩く）で行い、その結果を `EmailThread` 形の JSON にして本ツールセットへ供給する。
- 読取専用の発想: pipeline からは get_thread/search_threads/list_drafts のみ使う。
- create_draft / label_thread / send_draft は **記録のみ**（dry-run 前提・実害ゼロ）＝FakeGmail/FakeSlack を再利用。

JSON 形式: `{"threads": [<EmailThread dict>, ...], "existing_draft_thread_ids": [..]}` もしくは素の配列。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from aiia.mcp.fake import FakeGmail, FakeSlack
from aiia.schemas import EmailThread


def load_threads(path: Union[str, Path]) -> list[EmailThread]:
    """JSON ファイル → EmailThread のリスト（pydantic 検証つき）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data["threads"] if isinstance(data, dict) else data
    return [EmailThread.model_validate(t) for t in raw]


def _draft_ids(path: Union[str, Path]) -> set[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return set(data.get("existing_draft_thread_ids", []))
    return set()


@dataclass
class HarnessMCPToolset:
    """実データ(JSON)を、テスト済みの Fake インフラに載せた MCPToolset。"""

    gmail: FakeGmail
    slack: FakeSlack = field(default_factory=FakeSlack)

    @classmethod
    def from_json(
        cls, path: Union[str, Path], *, existing_draft_thread_ids: Optional[set[str]] = None
    ) -> "HarnessMCPToolset":
        threads = load_threads(path)
        ids = existing_draft_thread_ids if existing_draft_thread_ids is not None else _draft_ids(path)
        return cls(gmail=FakeGmail(threads=threads, existing_draft_thread_ids=set(ids)))

    @property
    def calls(self) -> list[tuple]:
        return self.gmail.calls + self.slack.calls
