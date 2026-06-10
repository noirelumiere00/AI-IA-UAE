"""返信下書き生成 factory（slack_app の[対応する]と connect_web の /reply で共通利用）。

本人トークンで get_thread → Haiku 要約 → 返信生成 → **全返信HTML+署名の下書き**を Gmail に作成。
slack_bolt 等のUI依存を持たない＝connect_web(FastAPI) からも呼べる。
"""
from __future__ import annotations

from typing import Callable

from aiia.auth.token_store import TokenStore
from aiia.mcp.workspace_gmail import WorkspaceGmail


def make_reply_draft_factory(store: TokenStore) -> Callable[[str, str], dict]:
    """`(user_email, thread_id) -> {draft_id,to,cc,subject,body}` を返す factory。
    未連携は PermissionError。下書き生成は WorkspaceGmail.create_reply_draft（全返信・HTML・署名）。"""

    def reply_draft_factory(email: str, thread_id: str) -> dict:
        from aiia.config import load_platform, load_user
        from aiia.providers.factory import build_llm

        token = store.get(email)
        if token is None:
            raise PermissionError(f"{email} は未連携です（/connect）")
        gmail = WorkspaceGmail(token)
        thread = gmail.get_thread(thread_id)
        cleanup_thread_drafts(gmail, thread_id)  # 同スレの古い下書きを掃除（再実行でべき等）
        llm = build_llm(load_platform())
        summary = llm.summarize(thread)
        draft = llm.draft(thread, summary, load_user(email))
        info = gmail.create_reply_draft(
            thread_id=thread_id, body=draft.body, thread=thread, user_email=email)
        return {**info, "body": draft.body}

    return reply_draft_factory


def cleanup_thread_drafts(gmail: WorkspaceGmail, thread_id: str) -> int:
    """同スレの既存下書きを掃除（/reply 再クリックで下書きが増えないようべき等寄せ）。失敗は無視。"""
    removed = 0
    try:
        svc = gmail._svc()
        for d in svc.users().drafts().list(userId="me").execute().get("drafts", []) or []:
            full = svc.users().drafts().get(userId="me", id=d["id"], format="minimal").execute()
            if full.get("message", {}).get("threadId") == thread_id:
                svc.users().drafts().delete(userId="me", id=d["id"]).execute()
                removed += 1
    except Exception:  # noqa: BLE001 — 掃除失敗でも下書き作成は続行
        pass
    return removed
