"""プロンプト整形（§12）。system 文の存在とユーザープロンプトの中身を軽く固定。"""
from __future__ import annotations

from aiia.config import UserConfig
from aiia.providers import prompts
from aiia.schemas import Category, ClassificationResult, EmailMessage, EmailThread, ThreadSummary


def _thread() -> EmailThread:
    return EmailThread(
        thread_id="x",
        subject="ご相談",
        messages=[EmailMessage(message_id="m", sender="a@b.example", subject="ご相談", body_text="本日中にご返信ください。")],
    )


def test_system_prompts_present() -> None:
    for s in (prompts.CLASSIFY_SYSTEM, prompts.SUMMARIZE_SYSTEM, prompts.EXTRACT_SYSTEM, prompts.DRAFT_SYSTEM):
        assert isinstance(s, str) and len(s) > 10


def test_classify_user_prompt_includes_hints() -> None:
    heur = ClassificationResult(category=Category.CLIENT_NORMAL, confidence=0.6, is_vip=True, reasons=["VIP"])
    out = prompts.classify_user_prompt(_thread(), heur)
    assert "CLIENT_NORMAL" in out and "is_vip=True" in out
    assert "ご相談" in out  # スレッド本文も載る


def test_draft_user_prompt_has_signature() -> None:
    summ = ThreadSummary(thread_id="x", one_liner="返信依頼")
    out = prompts.draft_user_prompt(_thread(), summ, UserConfig(display_name="小俣翔碁"))
    assert "小俣翔碁" in out


def test_thread_text_truncates() -> None:
    long = EmailThread(thread_id="x", subject="s", messages=[EmailMessage(message_id="m", body_text="あ" * 9000)])
    assert len(prompts.thread_text(long, max_chars=500)) <= 520
