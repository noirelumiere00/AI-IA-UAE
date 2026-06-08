"""mcp 層: registry の単一真実源 + FakeMCPToolset の挙動。"""
from __future__ import annotations

from aiia.mcp import registry
from aiia.mcp.fake import FakeGmail, FakeMCPToolset
from aiia.safety.hitl import ALLOWED_TOOLS, SEND_TOOLS


def test_registry_is_single_source() -> None:
    assert SEND_TOOLS is registry.SEND_TOOLS
    assert ALLOWED_TOOLS is registry.ALLOWED_TOOLS
    assert registry.GMAIL_SEND_MESSAGE in SEND_TOOLS
    assert registry.GMAIL_CREATE_DRAFT in ALLOWED_TOOLS
    # 送信系と許可系は重ならない
    assert not (SEND_TOOLS & ALLOWED_TOOLS)


def test_label_for() -> None:
    assert registry.label_for("CLIENT_URGENT") == "AIIA/CLIENT_URGENT"


def test_fake_paging_and_dedup_shape() -> None:
    g = FakeGmail()
    seen: list[str] = []
    token = None
    while True:
        res = g.search_threads("q", page_token=token, max_results=2)
        seen += [t["thread_id"] for t in res["threads"]]
        token = res["next_page_token"]
        if not token:
            break
    assert len(seen) == 6 and len(set(seen)) == 6  # 6件・重複なし


def test_fake_get_thread_marks_existing_draft() -> None:
    g = FakeGmail()
    assert g.get_thread("t_has_draft").has_existing_draft is True
    assert g.get_thread("t_vip_urgent").has_existing_draft is False


def test_fake_side_effects_recorded_not_executed() -> None:
    tools = FakeMCPToolset()
    tools.gmail.create_draft(thread_id="t1", subject="s", body="b")
    tools.gmail.label_thread(thread_id="t1", label="AIIA/X")
    tools.slack.send_draft(channel="U1", blocks=[{}], text="x")
    assert ("create_draft", "t1") in tools.calls
    assert ("label_thread", "t1", "AIIA/X") in tools.calls
