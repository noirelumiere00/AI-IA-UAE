"""本人の文体プロファイル（過去の送信メール＋Slack発言から抽出）。

文体記述に要約する関数 `summarize(system, user)->str` を注入（本番は Bedrock haiku・テストは fake）。
**サンプルは redaction 後に LLM へ渡す**（固有名詞/機密を文体抽出に混ぜない）。
descriptor は draft プロンプトの [本人の文体プロファイル] に差し込まれる。
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from aiia.safety.redaction import redact

_STYLE_SYSTEM = (
    "次の本人の文章サンプル（送信済みメール・Slack発言）から、返信下書きに使える『文体プロファイル』を"
    "日本語で簡潔に箇条書きしてください：語尾(です/ます/だ等)・敬語度・距離感・よく使う定型表現・"
    "句読点や改行の癖・一人称/署名。固有名詞や機密内容は含めず、文体だけを記述すること。"
)


@dataclass
class StyleProfile:
    user_id: str
    descriptor: str  # draft プロンプトに差し込む文体記述（空なら寄せない）
    sent_samples: int = 0
    slack_samples: int = 0


def _samples_block(
    sent: Sequence[str], slack: Sequence[str], *, max_each: int = 20, max_chars: int = 300
) -> str:
    lines: list[str] = []
    for t in list(sent)[:max_each]:
        lines.append("[メール] " + redact(t)[:max_chars])  # redaction後に投入
    for t in list(slack)[:max_each]:
        lines.append("[Slack] " + redact(t)[:max_chars])
    return "\n".join(lines)


def build_style_profile(
    user_id: str,
    sent_texts: Sequence[str],
    slack_texts: Sequence[str],
    *,
    summarize: Callable[[str, str], str],
) -> StyleProfile:
    sent = [t for t in sent_texts if t and t.strip()]
    slack = [t for t in slack_texts if t and t.strip()]
    if not sent and not slack:
        return StyleProfile(user_id=user_id, descriptor="")
    descriptor = summarize(_STYLE_SYSTEM, _samples_block(sent, slack)).strip()
    return StyleProfile(
        user_id=user_id, descriptor=descriptor, sent_samples=len(sent), slack_samples=len(slack)
    )
