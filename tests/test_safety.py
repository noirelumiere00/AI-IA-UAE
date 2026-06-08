"""NeverSendGate（誤送信不可能の二重防御の一方）。K5: 誤送信0 の核。"""
from __future__ import annotations

import pytest

from aiia.mcp import registry
from aiia.safety.hitl import AutoSendError, NeverSendGate, assert_no_send


def test_gate_denies_all_send_tools() -> None:
    g = NeverSendGate()
    assert g(registry.GMAIL_SEND_MESSAGE).allow is False
    assert g(registry.GMAIL_SEND_DRAFT).allow is False
    assert g(registry.SLACK_SEND_MESSAGE).allow is False
    assert g(registry.SLACK_SCHEDULE_MESSAGE).allow is False


def test_gate_allows_read_draft_label() -> None:
    g = NeverSendGate()
    assert g(registry.GMAIL_SEARCH_THREADS).allow is True
    assert g(registry.GMAIL_CREATE_DRAFT).allow is True
    assert g(registry.GMAIL_LABEL_THREAD).allow is True
    assert g(registry.SLACK_SEND_DRAFT).allow is True


def test_assert_no_send_raises_on_send() -> None:
    with pytest.raises(AutoSendError):
        assert_no_send(registry.GMAIL_SEND_DRAFT)
    with pytest.raises(AutoSendError):
        assert_no_send(registry.SLACK_SEND_MESSAGE)


def test_assert_no_send_passes_on_safe() -> None:
    assert_no_send(registry.GMAIL_CREATE_DRAFT)  # 例外を出さない
    assert_no_send(registry.SLACK_SEND_DRAFT)
