"""対話型Slackアプリ（slack_bolt AsyncApp + Socket Mode）。ロジックは slack_handlers に委譲。

ボタン[編集/削除/送信(2段確認)]＋ `/connect`。Gmail呼びは executor で実行(イベントループを塞がない)。
slack_bolt / slack_sdk は遅延 import（`[multiuser]` extra）。本ファイルは配線のみ＝ロジックは
slack_handlers.py（テスト済み）と hitl.SendConfirmationGate（テスト済み）に集約。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, Optional

from aiia.auth.oauth_flow import OAuthConsentFlow
from aiia.auth.token_store import OAuthToken, TokenStore
from aiia.delivery.slack import (
    ACTION_DELETE,
    ACTION_EDIT,
    ACTION_REMIND_DISMISS,
    ACTION_REMIND_MUTE,
    ACTION_REMIND_REPLY,
    ACTION_REMIND_SNOOZE,
    ACTION_REMIND_UNDO,
    ACTION_SEND,
)
from aiia.mcp.workspace_gmail import WorkspaceGmail, WorkspaceGmailSender
from aiia.runtime.slack_handlers import (
    HandlerDeps,
    handle_delete,
    handle_edit_submit,
    handle_remind_dismiss,
    handle_remind_mute,
    handle_remind_reply,
    handle_remind_snooze,
    handle_remind_undo,
    handle_send_submit,
)
from aiia.safety.hitl import SendConfirmationGate

EDIT_VIEW = "aiia_edit_submit"
SEND_VIEW = "aiia_send_submit"


def build_handler_deps(
    store: TokenStore, *, bot_token: Optional[str] = None,
    gate: Optional[SendConfirmationGate] = None, reminder_store: Any = None,
) -> HandlerDeps:
    """本番用 HandlerDeps を構築（email解決は sync WebClient・送信器は本人トークンから）。"""
    from slack_sdk import WebClient

    wc = WebClient(token=bot_token or os.environ.get("SLACK_BOT_TOKEN"))

    def email_for(slack_user_id: str) -> Optional[str]:
        resp = wc.users_info(user=slack_user_id)
        if resp.get("ok"):
            return resp["user"].get("profile", {}).get("email")
        return None

    import time

    def reply_draft_factory(email: str, thread_id: str) -> dict:
        """『対応する』：本人トークンでスレ取得→Haikuで返信生成→**返信下書きをGmailに作成**。"""
        from aiia.config import load_platform, load_user
        from aiia.providers.factory import build_llm

        token = store.get(email)
        if token is None:
            raise PermissionError(f"{email} は未連携です（/connect）")
        gmail = WorkspaceGmail(token)
        thread = gmail.get_thread(thread_id)
        llm = build_llm(load_platform())
        summary = llm.summarize(thread)
        draft = llm.draft(thread, summary, load_user(email))
        info = gmail.create_reply_draft(
            thread_id=thread_id, body=draft.body, thread=thread, user_email=email)  # 全返信(自分除外)
        return {**info, "body": draft.body}

    return HandlerDeps(
        store=store,
        gate=gate or SendConfirmationGate(),
        email_for_slack_user=email_for,
        sender_factory=lambda t: WorkspaceGmailSender(t),
        now=time.time,
        nonce=lambda: uuid.uuid4().hex,
        reminder_store=reminder_store,
        reply_draft_factory=reply_draft_factory,
    )


def _undo_blocks(thread_id: str, msg: str) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": msg}},
        {"type": "actions", "elements": [{"type": "button", "action_id": ACTION_REMIND_UNDO,
            "text": {"type": "plain_text", "text": "↩ 取り消す"}, "value": thread_id}]},
    ]


def _edit_modal(draft_id: str, thread_id: str, subject: str, body: str) -> dict:
    return {
        "type": "modal",
        "callback_id": EDIT_VIEW,
        "private_metadata": json.dumps({"draft_id": draft_id, "thread_id": thread_id, "subject": subject}),
        "title": {"type": "plain_text", "text": "下書きを編集"},
        "submit": {"type": "plain_text", "text": "保存"},
        "close": {"type": "plain_text", "text": "やめる"},
        "blocks": [
            {
                "type": "input",
                "block_id": "body",
                "label": {"type": "plain_text", "text": "本文"},
                "element": {"type": "plain_text_input", "action_id": "v", "multiline": True, "initial_value": body},
            }
        ],
    }


def _send_confirm_modal(draft_id: str) -> dict:
    return {
        "type": "modal",
        "callback_id": SEND_VIEW,
        "private_metadata": json.dumps({"draft_id": draft_id}),
        "title": {"type": "plain_text", "text": "送信の最終確認 (2/2)"},
        "submit": {"type": "plain_text", "text": "送信する"},
        "close": {"type": "plain_text", "text": "やめる"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "*この下書きを実際に送信します。* よろしいですか？\n（送信後は取り消せません）"}}
        ],
    }


def create_app(deps: HandlerDeps, *, connect_redirect_uri: Optional[str] = None) -> Any:
    from slack_bolt.async_app import AsyncApp

    app = AsyncApp(token=os.environ.get("SLACK_BOT_TOKEN"))
    loop = asyncio.get_event_loop

    async def _run(fn: Any) -> Any:  # Gmail等のブロッキングを executor へ
        return await loop().run_in_executor(None, fn)

    @app.command("/connect")
    async def connect(ack: Any, body: Any, client: Any, respond: Any) -> None:
        await ack()
        info = await client.users_info(user=body["user_id"])
        email = info["user"].get("profile", {}).get("email")
        if not email or not connect_redirect_uri:
            await respond("メール解決に失敗、または連携URL未設定です。")
            return
        url, _ = OAuthConsentFlow(connect_redirect_uri).authorization_url(email)
        await respond(f"👋 *{email}* のGoogleを連携します（1回だけ）。\n下のリンクで許可してください:\n{url}")

    @app.action(ACTION_DELETE)
    async def on_delete(ack: Any, body: Any, respond: Any) -> None:
        await ack()
        draft_id = body["actions"][0]["value"]
        uid = body["user"]["id"]
        msg = await _run(lambda: handle_delete(deps, slack_user_id=uid, draft_id=draft_id))
        await respond(response_type="ephemeral", text=msg)

    @app.action(ACTION_EDIT)
    async def on_edit(ack: Any, body: Any, client: Any) -> None:
        await ack()
        draft_id = body["actions"][0]["value"]
        cur = await _run(lambda: deps.sender_factory(_tok(deps, body["user"]["id"])).get_draft(draft_id))
        body_text = _extract_body(cur)
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=_edit_modal(draft_id, _thread_of(cur), _subject_of(cur), body_text),
        )

    @app.view(EDIT_VIEW)
    async def on_edit_submit(ack: Any, body: Any, view: Any) -> None:
        await ack()
        meta = json.loads(view["private_metadata"])
        new_body = view["state"]["values"]["body"]["v"]["value"]
        uid = body["user"]["id"]
        await _run(lambda: handle_edit_submit(
            deps, slack_user_id=uid, draft_id=meta["draft_id"],
            thread_id=meta["thread_id"], subject=meta["subject"], body=new_body,
        ))

    @app.action(ACTION_SEND)
    async def on_send(ack: Any, body: Any, client: Any) -> None:
        await ack()  # 1段目=Block Kit confirm 済 → 2段目モーダルを開く
        draft_id = body["actions"][0]["value"]
        await client.views_open(trigger_id=body["trigger_id"], view=_send_confirm_modal(draft_id))

    @app.view(SEND_VIEW)
    async def on_send_submit(ack: Any, body: Any, view: Any, client: Any) -> None:
        await ack()
        draft_id = json.loads(view["private_metadata"])["draft_id"]
        uid = body["user"]["id"]
        msg = await _run(lambda: handle_send_submit(deps, slack_user_id=uid, draft_id=draft_id))
        await client.chat_postMessage(channel=uid, text=msg, unfurl_links=False, unfurl_media=False)

    # ── 返信リマインド ──────────────────────────────────────────────────────
    _RX = {"dismiss": "white_check_mark", "snooze": "alarm_clock", "mute": "no_bell"}

    async def _react(client: Any, ch: str, ts: Optional[str], name: str, *, add: bool = True) -> None:
        """親メッセージに✅等のスタンプ（最終アクションの可視化）。reactions:write未付与でも本処理は止めない。"""
        if not ts:
            return
        try:
            if add:
                await client.reactions_add(channel=ch, timestamp=ts, name=name)
            else:
                await client.reactions_remove(channel=ch, timestamp=ts, name=name)
        except Exception:  # noqa: BLE001
            pass

    @app.action(ACTION_REMIND_REPLY)
    async def on_remind_reply(ack: Any, body: Any, client: Any) -> None:
        # 対応する＝下書きを作成し、Gmailの該当スレッド（下書き表示）へ飛ぶリンクを返す。
        # 編集・送信はGmail側で行う（Slack内の編集/送信フローは廃止）。
        await ack()
        tid, uid = body["actions"][0]["value"], body["user"]["id"]
        ch = body["channel"]["id"]
        ph = await client.chat_postMessage(channel=ch, text=f"<@{uid}> 返信下書きを作成しています…",
                                           unfurl_links=False, unfurl_media=False)
        ph_ts = ph.get("ts")
        info = await _run(lambda: handle_remind_reply(deps, slack_user_id=uid, thread_id=tid))
        link = info.get("gmail_link", "")
        if info.get("draft_id"):
            msg = (f"<@{uid}> 返信下書きを作成しました（Gmailの下書きに保存済）。\n"
                   f"件名: {info.get('subject', '')}\n"
                   f"<{link}|Gmailでスレッドを開く（下書きを確認して送信）>")
        else:
            msg = f"<@{uid}> <{link}|Gmailでスレッドを開いて返信する>"
        await client.chat_update(channel=ch, ts=ph_ts, text=msg)

    # 押下フィードバック＝**メインDMに直接**返信（スレッドに埋もれさせない＝[↩取り消す]が必ず見える）。
    # 戻り値tsに✅等のスタンプを付ける（その同じメッセージのundoでスタンプも外れる）。
    async def _remind_ack(body: Any, client: Any, msg: str, *, undo_tid: Optional[str] = None) -> Optional[str]:
        ch, uid = body["channel"]["id"], body["user"]["id"]
        blocks = _undo_blocks(undo_tid, msg) if undo_tid else None
        resp = await client.chat_postMessage(channel=ch, text=f"<@{uid}> {msg}",
                                             blocks=blocks, unfurl_links=False, unfurl_media=False)
        return resp.get("ts")

    @app.action(ACTION_REMIND_SNOOZE)
    async def on_remind_snooze(ack: Any, body: Any, client: Any) -> None:
        await ack()
        tid, uid = body["actions"][0]["value"], body["user"]["id"]
        msg = await _run(lambda: handle_remind_snooze(deps, slack_user_id=uid, thread_id=tid))
        conf_ts = await _remind_ack(body, client, msg)
        await _react(client, body["channel"]["id"], conf_ts, _RX["snooze"])

    @app.action(ACTION_REMIND_DISMISS)
    async def on_remind_dismiss(ack: Any, body: Any, client: Any) -> None:
        await ack()
        tid, uid = body["actions"][0]["value"], body["user"]["id"]
        msg = await _run(lambda: handle_remind_dismiss(deps, slack_user_id=uid, thread_id=tid))
        conf_ts = await _remind_ack(body, client, msg, undo_tid=tid)
        await _react(client, body["channel"]["id"], conf_ts, _RX["dismiss"])  # ✅ 緑チェック

    @app.action(ACTION_REMIND_MUTE)
    async def on_remind_mute(ack: Any, body: Any, client: Any) -> None:
        await ack()  # overflow は selected_option.value に thread_id
        tid = body["actions"][0]["selected_option"]["value"]
        uid = body["user"]["id"]
        msg = await _run(lambda: handle_remind_mute(deps, slack_user_id=uid, thread_id=tid))
        conf_ts = await _remind_ack(body, client, msg, undo_tid=tid)
        await _react(client, body["channel"]["id"], conf_ts, _RX["mute"])

    @app.action(ACTION_REMIND_UNDO)
    async def on_remind_undo(ack: Any, body: Any, client: Any) -> None:
        await ack()  # 元の確認メッセージ（このundoボタンが居るメッセージ）から✅等を外す
        tid, uid = body["actions"][0]["value"], body["user"]["id"]
        msg = await _run(lambda: handle_remind_undo(deps, slack_user_id=uid, thread_id=tid))
        ch, conf_ts = body["channel"]["id"], body["message"].get("ts")
        for nm in _RX.values():
            await _react(client, ch, conf_ts, nm, add=False)
        await _remind_ack(body, client, msg)

    return app


# ── 小ヘルパ（Gmail draft payload からの抽出） ──────────────────────────────
def _tok(deps: HandlerDeps, slack_user_id: str) -> OAuthToken:
    email = deps.email_for_slack_user(slack_user_id)
    token = deps.store.get(email) if email else None
    if token is None:
        raise PermissionError("未連携です（/connect）")
    return token


def _headers(draft: dict) -> dict[str, str]:
    payload = draft.get("message", {}).get("payload", {})
    return {h.get("name", ""): h.get("value", "") for h in payload.get("headers", [])}


def _thread_of(draft: dict) -> str:
    return str(draft.get("message", {}).get("threadId", ""))


def _subject_of(draft: dict) -> str:
    return _headers(draft).get("Subject", "")


def _extract_body(draft: dict) -> str:
    import base64

    payload = draft.get("message", {}).get("payload", {})
    parts = payload.get("parts") or [payload]
    for p in parts:
        if p.get("mimeType") in (None, "text/plain"):
            data = p.get("body", {}).get("data")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    return ""


def run() -> None:  # pragma: no cover - 常駐起動（live）
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    from aiia.auth.token_store import DynamoDbTokenStore, KmsCipher

    from aiia.state.reminder_store import DynamoDbReminderStore

    table = os.environ["AIIA_DDB_TABLE"]
    store = DynamoDbTokenStore(table, KmsCipher(os.environ["OAUTH_KMS_KEY_ID"]))
    rstore = DynamoDbReminderStore(os.environ.get("AIIA_REMINDER_TABLE", "aiia-reminder-state"))
    deps = build_handler_deps(store, reminder_store=rstore)
    app = create_app(deps, connect_redirect_uri=os.environ.get("OAUTH_REDIRECT_URI"))

    async def _main() -> None:
        # ハンドラは**実行ループ内**で生成（aiohttp.ClientSession が running loop を要求）。
        handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
        await handler.start_async()

    asyncio.run(_main())


if __name__ == "__main__":  # pragma: no cover — `python -m aiia.runtime.slack_app` で常駐起動
    run()
