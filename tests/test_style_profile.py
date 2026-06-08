"""文体プロファイル抽出（redaction適用・summarize注入・課金ゼロ）＋ draftへのstyle/context注入。"""
from __future__ import annotations

from aiia.config import UserConfig
from aiia.profile.style import build_style_profile
from aiia.providers import prompts
from aiia.schemas import EmailMessage, EmailThread, ThreadSummary


def test_build_style_profile_counts_and_redacts() -> None:
    captured: dict[str, str] = {}

    def fake_summarize(system: str, user: str) -> str:
        captured["user"] = user
        return "  です・ます調。丁寧。一人称は『私』。  "

    sp = build_style_profile(
        "s-komata",
        sent_texts=["お世話になっております。鍵は AKIA1234567890ABCDEF です。", ""],
        slack_texts=["承知しました！対応します。"],
        summarize=fake_summarize,
    )
    assert sp.descriptor == "です・ます調。丁寧。一人称は『私』。"  # strip 済
    assert sp.sent_samples == 1 and sp.slack_samples == 1  # 空文字は除外
    assert "AKIA1234567890ABCDEF" not in captured["user"]  # redaction 後にLLMへ渡す


def test_build_style_profile_empty() -> None:
    sp = build_style_profile("u", sent_texts=[], slack_texts=[], summarize=lambda s, u: "x")
    assert sp.descriptor == "" and sp.sent_samples == 0  # サンプル無→空(寄せない)


def test_draft_prompt_includes_style_and_context() -> None:
    th = EmailThread(
        thread_id="t1", subject="ご相談",
        messages=[EmailMessage(message_id="m", sender="a@b.example", subject="ご相談", body_text="本日中に")],
    )
    summary = ThreadSummary(thread_id="t1", one_liner="要点")
    user = UserConfig(display_name="小俣翔碁")
    p = prompts.draft_user_prompt(
        th, summary, user, style="です・ます調・一人称は私", slack_context="#proj-A: 直近の議論…"
    )
    assert "本人の文体プロファイル" in p and "です・ます調" in p
    assert "関連Slackの抜粋" in p and "#proj-A" in p
    assert "本人の文体に寄せて" in p
    # style/context 無しなら従来どおり(余計なセクションを出さない)
    plain = prompts.draft_user_prompt(th, summary, user)
    assert "本人の文体プロファイル" not in plain and "関連Slackの抜粋" not in plain
