"""per-user 実Gmail 道具（本人の refresh token で本人の受信箱のみ操作）。

`MCPToolset` Protocol を満たす本番ツールセット。Gmail API は遅延 import（googleapiclient）。
- 読取: search_threads / get_thread(→EmailThread・既定 metadata) / list_drafts
- 書込: create_draft(下書きのみ) / label_thread(AIIA/<cat> をresolve-or-create)
- **send は本ツールセットに持たせない**（誤送信を型レベルで防止。送信は M2 のゲート付き経路のみ）。
service を DI 可能＝テストは fake 注入で課金ゼロ。slack スロットは配信を別層(slack_client)で行うため No-op。
"""
from __future__ import annotations

import base64
import html as _html
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from email.utils import getaddresses
from typing import Any, Optional

from aiia.auth.google_creds import build_user_credentials
from aiia.auth.token_store import OAuthToken
from aiia.mcp.workspace_calendar import WorkspaceCalendar
from aiia.mcp.registry import LABEL_PREFIX
from aiia.schemas import EmailMessage, EmailThread

_META_HEADERS = ["From", "To", "Cc", "Subject", "Date", "Message-Id",
                 "List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted"]


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
                    labels=list(m.get("labelIds", [])),  # SENT 等（未返信判定に使う）
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

    def list_sent(self, max_results: int = 30) -> list[str]:
        """送信済みメール(SENT)の本文スニペットを返す（M3 文体学習用）。SENT はシステムラベル。"""
        svc = self._svc()
        resp = svc.users().messages().list(userId="me", labelIds=["SENT"], maxResults=max_results).execute()
        texts: list[str] = []
        for m in resp.get("messages", []):
            full = svc.users().messages().get(userId="me", id=m["id"], format="metadata").execute()
            snippet = full.get("snippet", "")
            if snippet:
                texts.append(snippet)
        return texts

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

    def default_signature(self) -> str:
        """既定 sendAs の署名(HTML)。下書きに本人署名を付けてネイティブ返信並みにする。
        現スコープ(gmail.modify)で取得可。失敗時は ""（署名無しでも下書きは成立）。インスタンスにキャッシュ。"""
        cached = getattr(self, "_sig_cache", None)
        if cached is not None:
            return str(cached)
        sig = ""
        try:
            res = self._svc().users().settings().sendAs().list(userId="me").execute()
            sends = res.get("sendAs", []) or []
            default = next((s for s in sends if s.get("isDefault")), sends[0] if sends else {})
            sig = str(default.get("signature", "") or "")
        except Exception:  # noqa: BLE001 — 署名取得失敗でも下書きは作る
            sig = ""
        self._sig_cache = sig
        return sig

    def create_reply_draft(
        self, *, thread_id: str, body: str, thread: Optional[EmailThread] = None,
        user_email: Optional[str] = None,
    ) -> dict:
        """**全返信(Reply-All)**下書き：To=元From＋元To、Cc=元Cc（いずれも自分を除外・重複排除）。
        件名=Re:・In-Reply-To/References 付き＝そのまま送信して成立。送信は人がGmailで2段確認。
        本文はHTML（本文＋本人署名）＝Gmailでスレッド内インラインのリッチ返信欄で開く。
        戻り値: {draft_id, to, cc, subject}。thread 未指定なら get_thread で取得。"""
        th = thread or self.get_thread(thread_id)
        m = th.last_inbound  # 相手の最新（自分の下書き/送信を除外＝Invalid To header を防ぐ）
        h = {k.lower(): v for k, v in (m.headers if m else {}).items()}
        self_lc = (user_email or "").strip().lower()

        def _addrs(*header_values: str) -> list[str]:
            out: list[str] = []
            for _name, addr in getaddresses([v for v in header_values if v]):
                a = addr.strip().lower()
                if a and a != self_lc and a not in out:  # 自分除外・重複排除・順序保持
                    out.append(a)
            return out

        to_list = _addrs(h.get("from", ""), h.get("to", ""))          # 元From＋元To
        cc_list = [a for a in _addrs(h.get("cc", "")) if a not in to_list]  # 元Cc（Toと重複は除外）
        if not to_list and h.get("from"):
            to_list = [h["from"]]  # フォールバック（最低限 差出人へ）
        subj = th.subject or h.get("subject", "")
        if not subj.lower().startswith("re:"):
            subj = f"Re: {subj}"
        irt = h.get("message-id", "")
        # HTML本文＝AI本文(エスケープ＋改行→<br>) ＋ 本人署名。署名はGmailの自動付与が効かないので明示付与。
        body_html = _html.escape(body).replace("\n", "<br>")
        sig = self.default_signature()
        full_html = f"<div dir=\"ltr\">{body_html}</div>" + (f"<br><br>{sig}" if sig else "")
        mime = MIMEText(full_html, "html", "utf-8")
        mime["Subject"] = subj
        if to_list:
            mime["To"] = ", ".join(to_list)
        if cc_list:
            mime["Cc"] = ", ".join(cc_list)
        if irt:
            mime["In-Reply-To"] = irt
            mime["References"] = irt
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        resp = (
            self._svc()
            .users()
            .drafts()
            .create(userId="me", body={"message": {"threadId": thread_id, "raw": raw}})
            .execute()
        )
        return {"draft_id": str(resp.get("id", "")), "to": ", ".join(to_list),
                "cc": ", ".join(cc_list), "subject": subj}

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
    """本番 MCPToolset: gmail=本人実Gmail / slack=No-op(配信は slack_client) / calendar=本人カレンダー(読取)。"""

    gmail: WorkspaceGmail
    slack: _NoOpSlack = field(default_factory=_NoOpSlack)
    calendar: Optional["WorkspaceCalendar"] = None

    @classmethod
    def from_token(cls, token: OAuthToken, *, service: Any = None) -> "WorkspaceGmailToolset":
        return cls(
            gmail=WorkspaceGmail(token, service=service),
            calendar=WorkspaceCalendar(token),
        )

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
