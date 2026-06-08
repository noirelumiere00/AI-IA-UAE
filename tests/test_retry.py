"""Bedrock throttle 用の指数バックオフ再試行（sleep/rand 注入・課金ゼロ）。"""
from __future__ import annotations

import pytest

from aiia.runtime.retry import call_with_retry, is_retryable


class Throttling(Exception):
    pass  # type名 "Throttling" は対象外。名前で判定するため別途用意


class ThrottlingException(Exception):
    pass  # 名前一致でリトライ対象


class _HttpErr(Exception):
    def __init__(self, status: int) -> None:
        self.status_code = status


def test_is_retryable_by_name_and_status() -> None:
    assert is_retryable(ThrottlingException())
    assert is_retryable(_HttpErr(503))
    assert not is_retryable(_HttpErr(400))
    assert not is_retryable(ValueError("bad input"))


def test_retries_transient_then_succeeds() -> None:
    calls = {"n": 0}
    slept: list[float] = []

    def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ThrottlingException()
        return "ok"

    out = call_with_retry(fn, sleep=slept.append, rand=lambda: 1.0)
    assert out == "ok"
    assert calls["n"] == 3
    assert slept == [0.5, 1.0]  # base*2^0, base*2^1（rand=1.0・full jitter最大）


def test_non_retryable_raises_immediately() -> None:
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise ValueError("input")

    with pytest.raises(ValueError):
        call_with_retry(fn, sleep=lambda s: None)
    assert calls["n"] == 1  # リトライしない


def test_exhausts_then_raises_last() -> None:
    def fn() -> str:
        raise ThrottlingException()

    with pytest.raises(ThrottlingException):
        call_with_retry(fn, max_attempts=3, sleep=lambda s: None, rand=lambda: 0.0)
