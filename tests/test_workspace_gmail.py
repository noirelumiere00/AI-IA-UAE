"""WorkspaceGmail（本人実Gmail道具）を fake Google service 注入で検証（課金ゼロ・google依存不要）。"""
from __future__ import annotations

import base64
from typing import Any

from aiia.auth.token_store import OAuthToken
from aiia.mcp.workspace_gmail import WorkspaceGmail, WorkspaceGmailToolset


# ── fake googleapiclient service（.users().threads().list(...).execute() 連鎖を模倣）──
class _Exec:
    def __init__(self, result: Any) -> None:
        self._r = result

    def execute(self) -> Any:
        return self._r


class _Threads:
    def __init__(self, svc: "FakeGmailService") -> None:
        self.svc = svc

    def list(self, **kw: Any) -> _Exec:
        self.svc.calls.append(("threads.list", kw))
        return _Exec({"threads": [{"id": "t1"}], "nextPageToken": None})

    def get(self, **kw: Any) -> _Exec:
        self.svc.calls.append(("threads.get", kw))
        return _Exec(self.svc.thread_get)

    def modify(self, **kw: Any) -> _Exec:
        self.svc.calls.append(("threads.modify", kw))
        return _Exec({})


class _Drafts:
    def __init__(self, svc: "FakeGmailService") -> None:
        self.svc = svc

    def list(self, **kw: Any) -> _Exec:
        return _Exec({"drafts": [{"message": {"threadId": "t1"}}, {"message": {"threadId": "t2"}}]})

    def create(self, **kw: Any) -> _Exec:
        self.svc.calls.append(("drafts.create", kw))
        return _Exec({"id": "draft_1"})


class _Labels:
    def __init__(self, svc: "FakeGmailService") -> None:
        self.svc = svc

    def list(self, **kw: Any) -> _Exec:
        return _Exec({"labels": self.svc.labels})

    def create(self, **kw: Any) -> _Exec:
        self.svc.calls.append(("labels.create", kw))
        nid = {"id": "L99", "name": kw["body"]["name"]}
        self.svc.labels.append(nid)
        return _Exec(nid)


class _Users:
    def __init__(self, svc: "FakeGmailService") -> None:
        self.svc = svc

    def threads(self) -> _Threads:
        return _Threads(self.svc)

    def drafts(self) -> _Drafts:
        return _Drafts(self.svc)

    def labels(self) -> _Labels:
        return _Labels(self.svc)


class FakeGmailService:
    def __init__(self, thread_get: dict) -> None:
        self.calls: list[tuple] = []
        self.labels: list[dict] = []
        self.thread_get = thread_get

    def users(self) -> _Users:
        return _Users(self)


def _thread_get() -> dict:
    return {
        "id": "t1",
        "messages": [
            {
                "id": "m1",
                "snippet": "本日中にご返信ください",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "CEO <ceo@bigclient.ae>"},
                        {"name": "Subject", "value": "【至急】ご確認"},
                        {"name": "List-Unsubscribe", "value": "<https://x/u>"},
                    ]
                },
            }
        ],
    }


def _gmail() -> WorkspaceGmail:
    return WorkspaceGmail(OAuthToken("1//r"), service=FakeGmailService(_thread_get()))


def test_search_threads_normalizes() -> None:
    res = _gmail().search_threads("is:unread", max_results=10)
    assert res["threads"] == [{"thread_id": "t1"}]
    assert res["next_page_token"] is None


def test_get_thread_parses_headers() -> None:
    th = _gmail().get_thread("t1")
    assert th.thread_id == "t1"
    assert th.subject == "【至急】ご確認"
    m = th.messages[0]
    assert m.sender_domain == "bigclient.ae"  # From の <...> からドメイン抽出
    assert m.body_text == "本日中にご返信ください"  # metadata=snippet
    assert m.headers.get("List-Unsubscribe")  # ヘッダ保持(NEWSLETTER判定に効く)


def test_list_drafts_filters_by_thread() -> None:
    g = _gmail()
    assert {d["thread_id"] for d in g.list_drafts()["drafts"]} == {"t1", "t2"}
    assert g.list_drafts(thread_id="t1")["drafts"] == [{"thread_id": "t1"}]


def test_create_draft_builds_mime_no_send() -> None:
    import email
    from email.header import make_header, decode_header

    g = _gmail()
    did = g.create_draft(thread_id="t1", subject="Re: 件名", body="お世話になっております。")
    assert did == "draft_1"
    call = next(c for c in g._service.calls if c[0] == "drafts.create")  # type: ignore[attr-defined]
    assert call[1]["body"]["message"]["threadId"] == "t1"
    raw = call[1]["body"]["message"]["raw"]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))  # RFC2047/base64 を正しく復号
    assert str(make_header(decode_header(msg["Subject"]))) == "Re: 件名"
    assert msg.get_payload(decode=True).decode("utf-8") == "お世話になっております。"
    # create_draft は下書きのみ＝send 系 API を一切呼ばない
    assert not any("send" in c[0] for c in g._service.calls)  # type: ignore[attr-defined]


def test_label_thread_resolves_or_creates_prefixed() -> None:
    g = _gmail()
    g.label_thread(thread_id="t1", label="CLIENT_URGENT")
    svc = g._service  # type: ignore[attr-defined]
    created = next(c for c in svc.calls if c[0] == "labels.create")
    assert created[1]["body"]["name"] == "AIIA/CLIENT_URGENT"  # prefix 補完
    assert any(c[0] == "threads.modify" for c in svc.calls)


def test_toolset_from_token_and_noop_slack() -> None:
    ts = WorkspaceGmailToolset.from_token(OAuthToken("1//r"), service=FakeGmailService(_thread_get()))
    assert ts.gmail.get_thread("t1").thread_id == "t1"  # gmail 経由で動く（MCPToolset 準拠）
    ts.slack.send_draft(channel="me", blocks=[{}], text="x")  # No-op（配信は別層）
    assert ts.calls == [("send_draft", "me", 1)]


def test_create_reply_draft_reply_all_excludes_self() -> None:
    # 全返信：To=元From＋元To（自分除く）／Cc=元Cc（自分除く・To重複除く）
    import base64
    th = {"id": "t1", "messages": [{"id": "m1", "snippet": "x", "payload": {"headers": [
        {"name": "From", "value": "Sender <sender@a.com>"},
        {"name": "To", "value": "me@self.com, One <other1@b.com>"},
        {"name": "Cc", "value": "other2@c.com, me@self.com"},
        {"name": "Subject", "value": "案件"},
        {"name": "Message-Id", "value": "<abc@a.com>"},
    ]}}]}
    g = WorkspaceGmail(OAuthToken("1//r"), service=FakeGmailService(th))
    info = g.create_reply_draft(thread_id="t1", body="承知しました", user_email="me@self.com")
    assert "sender@a.com" in info["to"] and "other1@b.com" in info["to"]
    assert "me@self.com" not in info["to"] and "me@self.com" not in info["cc"]
    assert info["cc"] == "other2@c.com"  # 自分・To重複を除いた元Cc
    assert info["subject"] == "Re: 案件"  # 件名Re:付（MIMEでは日本語がRFC2047符号化される）
    call = next(c for c in g._service.calls if c[0] == "drafts.create")  # type: ignore[attr-defined]
    mime = base64.urlsafe_b64decode(call[1]["body"]["message"]["raw"]).decode("utf-8")
    assert "To: sender@a.com, other1@b.com" in mime  # 全返信の宛先
    assert "Cc: other2@c.com" in mime and "In-Reply-To: <abc@a.com>" in mime
