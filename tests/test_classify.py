"""分類（triage 経由・HeuristicLLM.classify）の正しさ。K4: 分類精度の土台。"""
from __future__ import annotations

from aiia.config import UserConfig
from aiia.llm import HeuristicLLM
from aiia.schemas import Category, EmailMessage, EmailThread


def _thread(sender: str, domain: str, subject: str, body: str, headers: dict | None = None) -> EmailThread:
    return EmailThread(
        thread_id="x",
        subject=subject,
        messages=[
            EmailMessage(
                message_id="m", sender=sender, sender_domain=domain,
                subject=subject, body_text=body, headers=headers or {},
            )
        ],
    )


def test_vip_urgent_is_client_urgent(user: UserConfig) -> None:
    c = HeuristicLLM().classify(
        _thread("ceo@bigclient.ae", "bigclient.ae", "【至急】本日中にご確認", "本日中にお願いします"), user
    )
    assert c.category == Category.CLIENT_URGENT
    assert c.is_vip is True


def test_press_media(user: UserConfig) -> None:
    c = HeuristicLLM().classify(
        _thread("pr@media.example", "media.example", "取材のご依頼", "取材させてください"), user
    )
    assert c.category == Category.PRESS_MEDIA


def test_newsletter_via_list_unsubscribe(user: UserConfig) -> None:
    c = HeuristicLLM().classify(
        _thread("news@x.com", "x.com", "今月のお知らせ", "配信です",
                headers={"List-Unsubscribe": "<https://x.com/u>"}),
        user,
    )
    assert c.category == Category.NEWSLETTER


def test_internal_domain(user: UserConfig) -> None:
    c = HeuristicLLM().classify(
        _thread("hr@newstv.co.jp", "newstv.co.jp", "経費精算のお願い", "今週中にご提出ください"), user
    )
    assert c.category == Category.INTERNAL
