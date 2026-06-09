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

    def chat_postMessage(self, *, channel: str, blocks: list, text: str) -> dict:
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
            return {"ok": True, "user": {"id": "U1", "profile": {"real_name_normalized": "小俣翔碁"}}}

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
        store=store, platform=_plat(), config_dir=CONFIG_DIR, slack=slack,
        dry_run=False, gmail_service_factory=factory,
    )
    assert [r.email for r in res] == ["alice@x.com", "bob@x.com"]
    assert all(r.ok for r in res)
    assert all(r.processed == 1 for r in res)  # 各自の受信箱を1件処理
    assert all(r.delivered for r in res)  # 各自のSlack DMに配信


def test_run_for_all_users_isolates_one_failure() -> None:
    store = InMemoryTokenStore({"alice@x.com": OAuthToken("1//a"), "bob@x.com": OAuthToken("1//b")})

    def factory(email: str) -> Any:
        if email == "bob@x.com":
            raise RuntimeError("boom")  # bob だけ失敗
        return FakeGmailService(_thread_get())

    res = run_for_all_users(
        store=store, platform=_plat(), config_dir=CONFIG_DIR, dry_run=False,
        gmail_service_factory=factory,
    )
    by = {r.email: r for r in res}
    assert by["alice@x.com"].ok is True  # 他ユーザーは完走
    assert by["bob@x.com"].ok is False and by["bob@x.com"].error  # 失敗は隔離・記録
