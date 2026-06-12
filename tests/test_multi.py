"""多人数オーケストレーション＋Slack配信（fake注入・課金ゼロ）。並行・失敗隔離・配信を検証。"""

from __future__ import annotations

from typing import Any

from conftest import CONFIG_DIR
from test_workspace_gmail import FakeGmailService, _thread_get

from aiia.adapters.slack_client import SlackDelivery
from aiia.auth.token_store import InMemoryTokenStore, OAuthToken
from aiia.config import PlatformConfig
from aiia.orchestrator.multi import run_for_all_users


class FakeSlackWC:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def users_lookupByEmail(self, *, email: str) -> dict:
        return {"ok": True, "user": {"id": "U_" + email}}

    def conversations_open(self, *, users: str) -> dict:
        return {"ok": True, "channel": {"id": "D_" + users}}

    def chat_postMessage(self, *, channel: str, blocks: list, text: str, **kw: object) -> dict:
        self.sent.append(channel)
        return {"ok": True}


def test_slack_delivery_happy_path() -> None:
    wc = FakeSlackWC()
    assert SlackDelivery(client=wc).send_digest(email="a@x.com", blocks=[{}], text="t") is True
    assert wc.sent == ["D_U_a@x.com"]


def test_slack_delivery_user_not_found() -> None:
    class _WC:
        def users_lookupByEmail(self, *, email: str) -> dict:
            return {"ok": False}

    assert SlackDelivery(client=_WC()).send_digest(email="a@x", blocks=[], text="t") is False


def test_display_name_for_email() -> None:
    class _WC:
        def users_lookupByEmail(self, *, email: str) -> dict:
            return {
                "ok": True,
                "user": {"id": "U1", "profile": {"real_name_normalized": "小俣翔碁"}},
            }

    assert SlackDelivery(client=_WC()).display_name_for_email("s-komata@x") == "小俣翔碁"

    class _WCNone:
        def users_lookupByEmail(self, *, email: str) -> dict:
            return {"ok": False}

    assert SlackDelivery(client=_WCNone()).display_name_for_email("x@y") is None


def _plat() -> PlatformConfig:
    return PlatformConfig(default_profile="heuristic")


def test_run_for_all_users_parallel_and_delivers() -> None:
    store = InMemoryTokenStore({"alice@x.com": OAuthToken("1//a"), "bob@x.com": OAuthToken("1//b")})
    slack = SlackDelivery(client=FakeSlackWC())

    def factory(email: str) -> Any:
        return FakeGmailService(_thread_get())

    res = run_for_all_users(
        store=store,
        platform=_plat(),
        config_dir=CONFIG_DIR,
        slack=slack,
        dry_run=False,
        gmail_service_factory=factory,
    )
    assert [r.email for r in res] == ["alice@x.com", "bob@x.com"]
    assert all(r.ok for r in res)
    assert all(r.processed == 1 for r in res)  # 各自の受信箱を1件処理
    assert all(r.delivered for r in res)  # 各自のSlack DMに配信


class _CapWC(FakeSlackWC):
    """配信blocksを channel ごとに捕捉（メンション行の混入を検証する用）。"""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: dict[str, list] = {}

    def chat_postMessage(self, *, channel: str, blocks: list, text: str, **kw: object) -> dict:
        self.blocks[channel] = blocks
        return super().chat_postMessage(channel=channel, blocks=blocks, text=text, **kw)


def test_run_for_all_users_merges_slack_mentions_only_for_authorized() -> None:
    from datetime import datetime, timedelta, timezone

    from aiia.schemas import Category
    from aiia.state.reminder_store import InMemoryReminderStore, ReminderRecord

    now = datetime.now(timezone.utc)
    ts = str(now.timestamp() - 3600)  # 1時間前（lookback内）
    key = f"slack:C9:{ts}"
    rstore = InMemoryReminderStore()
    rstore.upsert(  # 5日前から放置＝閾値超え（show 条件）
        ReminderRecord(
            "alice@x.com", key, Category.CLIENT_NORMAL.value, first_seen=now - timedelta(days=5)
        )
    )
    mention = {
        "channel_id": "C9",
        "channel_name": "sales",
        "is_im": False,
        "ts": ts,
        "thread_ts": ts,
        "text": "<@U_alice@x.com> 確認お願いします",
        "permalink": "https://slack/pX",
        "user": "U_other",
        "username": "carol",
    }

    class FakeSU:
        def search_mentions(self, uid: str, *, count: int = 100) -> list:
            return [mention]

        def has_user_replied_after(self, ch, tts, uid, after) -> bool:
            return False  # 未返信

    store = InMemoryTokenStore({"alice@x.com": OAuthToken("1//a"), "bob@x.com": OAuthToken("1//b")})
    slack_token_store = InMemoryTokenStore({"alice@x.com": OAuthToken("xoxp-a")})  # bobは未認可
    wc = _CapWC()

    def factory(email: str) -> Any:
        return FakeGmailService(_thread_get())

    res = run_for_all_users(
        store=store,
        platform=_plat(),
        config_dir=CONFIG_DIR,
        slack=SlackDelivery(client=wc),
        dry_run=False,
        gmail_service_factory=factory,
        reminder_store=rstore,
        slack_token_store=slack_token_store,
        slack_user_factory=lambda tok: FakeSU(),
    )
    assert all(r.ok for r in res)
    by = {r.email: r for r in res}
    assert by["alice@x.com"].delivered is True  # 認可者へ配信成功
    # 描画された配信blocksに「メンション」表記＝Slack分が合流して render された証拠
    assert "メンション" in str(wc.blocks.get("D_U_alice@x.com", []))  # 認可者はメンション合流
    assert "メンション" not in str(wc.blocks.get("D_U_bob@x.com", []))  # 未認可者はskip


def test_run_for_all_users_no_slack_store_unchanged() -> None:
    store = InMemoryTokenStore({"alice@x.com": OAuthToken("1//a")})
    wc = _CapWC()

    def factory(email: str) -> Any:
        return FakeGmailService(_thread_get())

    res = run_for_all_users(
        store=store,
        platform=_plat(),
        config_dir=CONFIG_DIR,
        slack=SlackDelivery(client=wc),
        dry_run=False,
        gmail_service_factory=factory,
    )
    assert res[0].ok and res[0].delivered  # slack_token_store未指定でも従来どおり配信
    assert "【メンション】" not in str(wc.blocks.get("D_U_alice@x.com", []))  # Slack分は混入しない


def test_run_for_all_users_isolates_one_failure() -> None:
    store = InMemoryTokenStore({"alice@x.com": OAuthToken("1//a"), "bob@x.com": OAuthToken("1//b")})

    def factory(email: str) -> Any:
        if email == "bob@x.com":
            raise RuntimeError("boom")  # bob だけ失敗
        return FakeGmailService(_thread_get())

    res = run_for_all_users(
        store=store,
        platform=_plat(),
        config_dir=CONFIG_DIR,
        dry_run=False,
        gmail_service_factory=factory,
    )
    by = {r.email: r for r in res}
    assert by["alice@x.com"].ok is True  # 他ユーザーは完走
    assert by["bob@x.com"].ok is False and by["bob@x.com"].error  # 失敗は隔離・記録
