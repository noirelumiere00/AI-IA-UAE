"""M3 データ源(list_sent / Slack recent_messages) + grounding + pipeline配線。fake注入・課金ゼロ。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aiia.adapters.slack_client import SlackDelivery
from aiia.auth.token_store import OAuthToken
from aiia.llm import HeuristicLLM
from aiia.mcp.fake import FakeMCPToolset
from aiia.mcp.workspace_gmail import WorkspaceGmail
from aiia.pipeline import PipelineDeps, run
from aiia.profile.grounding import find_related_slack
from aiia.schemas import EmailMessage, EmailThread


# ── list_sent (Gmail SENT) ──────────────────────────────────────────────────
class _Exec:
    def __init__(self, r: Any) -> None:
        self._r = r

    def execute(self) -> Any:
        return self._r


class _SentGmail:
    def users(self) -> Any:
        class _M:
            def list(self, **kw: Any) -> _Exec:
                return _Exec({"messages": [{"id": "s1"}, {"id": "s2"}]})

            def get(self, **kw: Any) -> _Exec:
                return _Exec({"snippet": f"送信スニペット {kw['id']}"})

        class _U:
            def messages(self) -> Any:
                return _M()

        return _U()


def test_list_sent_returns_snippets() -> None:
    g = WorkspaceGmail(OAuthToken("1//r"), service=_SentGmail())
    assert g.list_sent(max_results=5) == ["送信スニペット s1", "送信スニペット s2"]


# ── Slack recent_messages（任意でユーザー絞り込み） ────────────────────────────
class _SlackWC:
    def conversations_history(self, *, channel: str, limit: int) -> dict:
        return {
            "ok": True,
            "messages": [
                {"user": "U1", "text": "本人の発言です。"},
                {"user": "U2", "text": "他人の発言 AKIA1234567890ABCDEF"},
            ],
        }


def test_recent_messages_filter_by_user() -> None:
    d = SlackDelivery(client=_SlackWC())
    assert d.recent_messages("C1") == ["本人の発言です。", "他人の発言 AKIA1234567890ABCDEF"]
    assert d.recent_messages("C1", user_id="U1") == ["本人の発言です。"]  # 本人のみ


# ── grounding ───────────────────────────────────────────────────────────────
def _thread() -> EmailThread:
    return EmailThread(
        thread_id="t1", subject="Q3のご相談",
        messages=[EmailMessage(message_id="m", sender="ceo@bigclient.ae", subject="Q3のご相談", body_text="本日中に")],
    )


def test_grounding_matches_channel_and_redacts() -> None:
    slack = SlackDelivery(client=_SlackWC())
    ctx = find_related_slack(_thread(), channel_map={"bigclient": "C123"}, slack=slack)
    assert ctx is not None and "#C123" in ctx
    assert "本人の発言" in ctx
    assert "AKIA1234567890ABCDEF" not in ctx  # redaction 適用


def test_grounding_no_match_returns_none() -> None:
    slack = SlackDelivery(client=_SlackWC())
    assert find_related_slack(_thread(), channel_map={"otherclient": "C9"}, slack=slack) is None


# ── pipeline 配線（style/grounding provider が draft に届く） ───────────────────
class RecordingLLM(HeuristicLLM):
    def __init__(self) -> None:
        super().__init__()
        self.draft_kwargs: list[tuple] = []

    def draft(self, thread, summary, user, *, style=None, slack_context=None):  # type: ignore[override]
        self.draft_kwargs.append((style, slack_context))
        return super().draft(thread, summary, user)


def test_providers_reach_draft(make_deps: Callable[..., PipelineDeps], tools: FakeMCPToolset) -> None:
    base = make_deps(tools, dry_run=True)
    llm = RecordingLLM()
    deps = PipelineDeps(
        llm=llm, tools=tools, user=base.user, agent=base.agent, dry_run=True, now=base.now,
        style_provider=lambda u: "です・ます調",
        grounding_provider=lambda t: "#proj-A: 直近の議論",
    )
    run(deps)
    assert llm.draft_kwargs, "draftが1件以上呼ばれる（要返信カテゴリ）"
    assert all(s == "です・ます調" and c == "#proj-A: 直近の議論" for s, c in llm.draft_kwargs)
