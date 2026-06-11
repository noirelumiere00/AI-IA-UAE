"""SlackUserClient.has_user_replied_after の engagement 判定（fake WebClient・課金ゼロ）。

チャンネル直下(conversations.history)を主判定に、スレッド型はreplies併用。
True=対応済 / False=取得OK・未返信 / None=両取得不能(fail-closed)。
"""
from __future__ import annotations

from typing import Any, Optional

from aiia.adapters.slack_user_client import SlackUserClient


class FakeWC:
    def __init__(
        self,
        history: Optional[list] = None,
        replies: Optional[list] = None,
        hist_ok: bool = True,
        rep_ok: bool = True,
        hist_raise: bool = False,
        rep_raise: bool = False,
    ) -> None:
        self._history = history or []
        self._replies = replies or []
        self._hist_ok, self._rep_ok = hist_ok, rep_ok
        self._hist_raise, self._rep_raise = hist_raise, rep_raise

    def conversations_history(self, channel: str, oldest: str, limit: int) -> dict[str, Any]:
        if self._hist_raise:
            raise RuntimeError("missing_scope")
        return {"ok": self._hist_ok, "messages": self._history}

    def conversations_replies(self, channel: str, ts: str, limit: int) -> dict[str, Any]:
        if self._rep_raise:
            raise RuntimeError("missing_scope")
        return {"ok": self._rep_ok, "messages": self._replies}


def _client(**kw: Any) -> SlackUserClient:
    return SlackUserClient(client=FakeWC(**kw))


def test_replied_in_channel_after_returns_true() -> None:
    su = _client(history=[{"user": "U_ME", "ts": "200.0"}])  # channel直下に本人の後続発言
    assert su.has_user_replied_after("C1", "100.0", "U_ME", "100.0") is True


def test_no_reply_returns_false() -> None:
    su = _client(history=[{"user": "U_OTHER", "ts": "200.0"}])  # 他人のみ＝未返信
    assert su.has_user_replied_after("C1", "100.0", "U_ME", "100.0") is False


def test_thread_reply_when_channel_silent_returns_true() -> None:
    # channel直下は本人なし／スレッド型でスレッド内に本人の返信あり
    su = _client(history=[{"user": "U_OTHER", "ts": "150.0"}], replies=[{"user": "U_ME", "ts": "180.0"}])
    assert su.has_user_replied_after("C1", "90.0", "U_ME", "100.0") is True  # thread_ts(90)!=after(100)


def test_both_unavailable_returns_none() -> None:
    su = _client(hist_raise=True, rep_raise=True)  # 両方取得不能→fail-closed
    assert su.has_user_replied_after("C1", "90.0", "U_ME", "100.0") is None


def test_non_thread_channel_fail_returns_none() -> None:
    # 非スレッド(thread_ts==after_ts)でchannel取得失敗→repliesは見ない→None
    su = _client(hist_raise=True)
    assert su.has_user_replied_after("C1", "100.0", "U_ME", "100.0") is None


def test_invalid_after_ts_returns_none() -> None:
    su = _client(history=[{"user": "U_ME", "ts": "200.0"}])
    assert su.has_user_replied_after("C1", "100.0", "U_ME", "not-a-ts") is None
