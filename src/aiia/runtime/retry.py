"""Bedrock 一過性エラー（Throttling/5xx）の指数バックオフ再試行（フルジッタ）。

TeamAgent runtime/retry.py の移植。sleep/rand を注入可能＝テストは決定的・課金ゼロ。
リトライ不可（4xx の入力エラー等）は即 re-raise。最終試行も re-raise。
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

_RETRYABLE_NAMES = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelTimeoutException",
        "ModelNotReadyException",
    }
)
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def is_retryable(exc: Exception) -> bool:
    if type(exc).__name__ in _RETRYABLE_NAMES:
        return True
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        if resp.get("Error", {}).get("Code") in _RETRYABLE_NAMES:
            return True
        if resp.get("ResponseMetadata", {}).get("HTTPStatusCode") in _RETRYABLE_STATUS:
            return True
    if getattr(exc, "status_code", None) in _RETRYABLE_STATUS:
        return True
    return False


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 5,
    base_delay: float = 0.5,
    max_delay: float = 20.0,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
    retryable: Callable[[Exception], bool] = is_retryable,
) -> T:
    last: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 判定して再raiseする
            if not retryable(exc) or attempt == max_attempts - 1:
                raise
            last = exc
            cap = min(max_delay, base_delay * (2**attempt))
            sleep(cap * rand())  # フルジッタ
    raise last if last else RuntimeError("call_with_retry: 到達不能")  # pragma: no cover
