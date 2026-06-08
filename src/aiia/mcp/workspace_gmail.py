"""per-user 実Gmail 道具（本人の refresh token で本人の受信箱のみ操作）。

`MCPToolset` Protocol を満たす本番ツールセット。Gmail API は遅延 import（googleapiclient）。
- 読取: search_threads / get_thread(→EmailThread・既定 metadata) / list_drafts
- 書込: create_draft(下書きのみ) / label_thread(AIIA/<cat> をresolve-or-create)
- **send は本ツールセットに持たせない**（誤送信を型レベルで防止。送信は M2 のゲート付き経路のみ）。
service を DI 可能＝テストは fake 注入で課金ゼロ。slack スロットは配信を別層(slack_client)で行うため No-op。
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from typing import Any, Optional

from aiia.auth.google_creds import build_user_credentials
from aiia.auth.token_store import OAuthToken
from aiia.mcp.registry import LABEL_PREFIX
from aiia.schemas import EmailMessage, EmailThread

_META_HEADERS = ["From", "Subject", "Date", "List-Unsubscribe"]


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return str(h.get("value", ""))
    return ""


def _domain(sender: str) -> str:
    at = sender.rfind("@")
    if at < 0:
        return ""
    return sender[at + 1 :].split(">")[0].strip().lower()


class WorkspaceGmail:
    """GmailTools 実装。`service` 未指定なら本人トークンから googleapiclient を構築。"""

    def __init__(self, token: OAuthToken, *, service: Any = None) -> None:
        self._token = token
        self._service = service

    def _svc(self) -> Any:
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build(
                "gmail", "v1", credentials=build_user_credentials(self._token), cache_discovery=False
            )
        return self._service

    def search_threads(
        self, query: str, page_token: Optional[str] = None, max_results: int = 50
    ) -> dict:
        resp = (
            self._svc()
            .users()
            .threads()
            .list(userId="me", q=query, maxResults=max_results, pageToken=page_token)
            .execute()
        )
        return {
            "threads": [{"thread_id": t["id"]} for t in resp.get("threads", [])],
            "next_page_token": resp.get("nextPageToken"),
        }

    def get_thread(self, thread_id: str) -> EmailThread:
        resp = (
            self._svc()
            .users()
            .threads()
            .get(userId="me", id=thread_id, format="metadata", metadataHeaders=_META_HEADERS)
            .execute()
        )
        msgs: list[EmailMessage] = []
        subject = ""
        for m in resp.get("messages", []):
            headers = m.get("payload", {}).get("headers", [])
            sender = _header(headers, "From")
            subj = _header(headers, "Subject")
            subject = subject or subj
            snippet = m.get("snippet", "")
            msgs.append(
                EmailMessage(
                    message_id=m.get("id", ""),
                    sender=sender,
                    sender_domain=_domain(sender),
                    subject=subj,
                    snippet=snippet,
                    body_text=snippet,  # metadata 取得＝本文はsnippet（軽量・privacy）。M3でfull化
                    headers={h["name"]: h["value"] for h in headers if "name" in h},
                )
            )
        return EmailThread(thread_id=thread_id, subject=subject, messages=msgs)

    def list_drafts(self, thread_id: Optional[str] = None) -> dict:
        resp = self._svc().users().drafts().list(userId="me", maxResults=100).execute()
        out = []
        for d in resp.get("drafts", []):
            tid = d.get("message", {}).get("threadId")
            if tid and (thread_id is None or tid == thread_id):
                out.append({"thread_id": tid})
        return {"drafts": out}

    def create_draft(self, *, thread_id: str, subject: str, body: str) -> str:
        mime = MIMEText(body, _charset="utf-8")
        mime["Subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        resp = (
            self._svc()
            .users()
            .drafts()
            .create(userId="me", body={"message": {"threadId": thread_id, "raw": raw}})
            .execute()
        )
        return str(resp.get("id", ""))

    def _label_id(self, label: str) -> str:
        svc = self._svc()
        existing = svc.users().labels().list(userId="me").execute().get("labels", [])
        for lab in existing:
            if lab.get("name") == label:
                return str(lab["id"])
        created = (
            svc.users()
            .labels()
            .create(userId="me", body={"name": label, "labelListVisibility": "labelShow"})
            .execute()
        )
        return str(created["id"])

    def label_thread(self, *, thread_id: str, label: str) -> None:
        if not label.startswith(LABEL_PREFIX):
            label = LABEL_PREFIX + label
        lid = self._label_id(label)
        self._svc().users().threads().modify(
            userId="me", id=thread_id, body={"addLabelIds": [lid]}
        ).execute()


@dataclass
class _NoOpSlack:
    """配信は slack_client(別層) で行うため、pipeline 内の slack スロットは記録のみ。"""

    calls: list[tuple] = field(default_factory=list)

    def send_draft(self, *, channel: str, blocks: list, text: str) -> str:
        self.calls.append(("send_draft", channel, len(blocks)))
        return "noop"


@dataclass
class WorkspaceGmailToolset:
    """本番 MCPToolset: gmail=本人実Gmail / slack=No-op(配信は slack_client)。"""

    gmail: WorkspaceGmail
    slack: _NoOpSlack = field(default_factory=_NoOpSlack)

    @classmethod
    def from_token(cls, token: OAuthToken, *, service: Any = None) -> "WorkspaceGmailToolset":
        return cls(gmail=WorkspaceGmail(token, service=service))

    @property
    def calls(self) -> list[tuple]:
        return self.slack.calls


class WorkspaceGmailSender:
    """ゲート済み送信/下書き編集 専用（**MCPToolset/GmailTools には含めない**＝pipeline から到達不可）。

    M2 の Slack 2段確認 submit ハンドラからのみ生成し、`SendConfirmationGate.authorize_send` 通過後に
    `send_draft` を呼ぶ。これにより「ボタン経由・2段確認済み」の送信だけが成立する。
    """

    def __init__(self, token: OAuthToken, *, service: Any = None) -> None:
        self._token = token
        self._service = service

    def _svc(self) -> Any:
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build(
                "gmail", "v1", credentials=build_user_credentials(self._token), cache_discovery=False
            )
        return self._service

    def get_draft(self, draft_id: str) -> dict:
        return dict(
            self._svc().users().drafts().get(userId="me", id=draft_id, format="full").execute()
        )

    def update_draft(self, *, draft_id: str, thread_id: str, subject: str, body: str) -> str:
        mime = MIMEText(body, _charset="utf-8")
        mime["Subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        resp = (
            self._svc()
            .users()
            .drafts()
            .update(userId="me", id=draft_id, body={"message": {"threadId": thread_id, "raw": raw}})
            .execute()
        )
        return str(resp.get("id", ""))

    def delete_draft(self, draft_id: str) -> None:
        self._svc().users().drafts().delete(userId="me", id=draft_id).execute()

    def send_draft(self, draft_id: str) -> str:
        """既存のGmail下書きを送信（drafts.send）。**呼び出し前にゲート authorize_send 必須**。"""
        resp = self._svc().users().drafts().send(userId="me", body={"id": draft_id}).execute()
        return str(resp.get("id", ""))  # 送信後メッセージID
