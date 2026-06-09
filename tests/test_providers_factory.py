"""M3 provider factory（StyleProfile / grounding を pipeline provider 化）。fake注入・課金ゼロ。"""
from __future__ import annotations


from aiia.config import UserConfig
from aiia.profile.providers import build_grounding_provider, build_style_provider
from aiia.schemas import EmailMessage, EmailThread


class _Gmail:
    def __init__(self, sent: list[str] | None = None, *, boom: bool = False) -> None:
        self._sent = sent or ["お世話になっております。", "承知しました。"]
        self._boom = boom

    def list_sent(self, max_results: int = 20) -> list[str]:
        if self._boom:
            raise RuntimeError("gmail error")
        return self._sent


class _Slack:
    def recent_messages(self, channel: str, *, user_id: str | None = None, limit: int = 50) -> list[str]:
        return ["#chの直近メッセージ"]


def _user(uid: str = "u1") -> UserConfig:
    return UserConfig(user_id=uid, display_name="小俣翔碁")


def test_style_provider_builds_and_caches() -> None:
    calls = {"n": 0}

    def summarize(system: str, user: str) -> str:
        calls["n"] += 1
        return "です・ます調・丁寧"

    cache: dict[str, str] = {}
    provider = build_style_provider(gmail=_Gmail(), summarize=summarize, cache=cache)
    assert provider(_user()) == "です・ます調・丁寧"
    assert provider(_user()) == "です・ます調・丁寧"  # 2回目
    assert calls["n"] == 1  # cacheヒットで summarize は1回だけ


def test_style_provider_includes_slack_when_given() -> None:
    captured: dict[str, str] = {}

    def summarize(system: str, user: str) -> str:
        captured["user"] = user
        return "x"

    provider = build_style_provider(
        gmail=_Gmail(["メール文"]), summarize=summarize, slack=_Slack(),
        slack_channel="C1", slack_user_id="U1",
    )
    provider(_user())
    assert "メール文" in captured["user"] and "#chの直近メッセージ" in captured["user"]


def test_style_provider_robust_on_gmail_error() -> None:
    # gmail 失敗 + slack 無 → サンプル0 → descriptor 空 → None（素draftにフォールバック）
    provider = build_style_provider(gmail=_Gmail(boom=True), summarize=lambda s, u: "y")
    assert provider(_user()) is None


def _thread() -> EmailThread:
    return EmailThread(
        thread_id="t1", subject="Q3のご相談",
        messages=[EmailMessage(message_id="m", sender="ceo@bigclient.ae", subject="Q3のご相談", body_text="")],
    )


def test_grounding_provider_matches_and_empty_map() -> None:
    prov = build_grounding_provider(slack=_Slack(), channel_map={"bigclient": "C9"})
    ctx = prov(_thread())
    assert ctx is not None and "#C9" in ctx
    # channel_map 空なら None
    assert build_grounding_provider(slack=_Slack(), channel_map={})(_thread()) is None
