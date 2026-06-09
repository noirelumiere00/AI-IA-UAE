"""論理モデル名 + region → Bedrock 推論プロファイルID 解決（+ 起動時 doctor）。

Bedrock の Claude は `anthropic.` 接頭辞 ＋ クロスリージョン推論プロファイル接頭辞(us./eu./apac.)。
正確なID(version接尾辞の有無)はモデルアクセス申請やリージョンで変わるため、
`AIIA_BEDROCK_MODEL_<TIER>` env で各 tier を直接上書き可能にする（申請確定後は env だけで解決）。
既定値は TeamAgent の us-east-1 実績に準拠。
"""
from __future__ import annotations

import os

from aiia.config import PlatformConfig

# 論理名 → Bedrock ベースID（anthropic. 接頭辞付き・region接頭辞なし）。
_DEFAULT_BASE_ID: dict[str, str] = {
    "claude-haiku-4-5": "anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-sonnet-4-6": "anthropic.claude-sonnet-4-6",
    "claude-opus-4-8": "anthropic.claude-opus-4-8",
}

# Claude 4.x の推論プロファイル接頭辞。東京(ap-northeast-1)は jp.（4.x系・データは日本内）。
_REGION_OVERRIDE: dict[str, str] = {"ap-northeast-1": "jp."}
_REGION_PREFIX: dict[str, str] = {"us": "us.", "eu": "eu.", "ap": "apac."}
_PROFILE_HEADS = frozenset({"us", "eu", "apac", "jp", "global"})


def _profile_prefix(region: str | None) -> str:
    if not region:
        return ""
    r = region.lower()
    if r in _REGION_OVERRIDE:  # 東京は jp.（4.x系の実在プロファイル）
        return _REGION_OVERRIDE[r]
    head = r.split("-", 1)[0]
    return _REGION_PREFIX.get(head, "")


def resolve_model_id(logical: str, region: str | None = None) -> str:
    """論理名(例 claude-haiku-4-5) → Bedrock 推論プロファイルID(例 us.anthropic.claude-haiku-4-5-…)。"""
    base = _DEFAULT_BASE_ID.get(
        logical, logical if logical.startswith("anthropic.") else f"anthropic.{logical}"
    )
    if base.split(".", 1)[0] in _PROFILE_HEADS:  # 既に us./eu./apac. 付き
        return base
    return f"{_profile_prefix(region)}{base}"


def tier_ids(platform: PlatformConfig, region: str | None) -> dict[str, str]:
    """classify/summarize/draft の各モデルIDを解決（env 上書き優先）。"""
    m = platform.models
    return {
        "classify": os.environ.get("AIIA_BEDROCK_MODEL_CLASSIFY") or resolve_model_id(m.classify, region),
        "summarize": os.environ.get("AIIA_BEDROCK_MODEL_SUMMARIZE") or resolve_model_id(m.summarize, region),
        "draft": os.environ.get("AIIA_BEDROCK_MODEL_DRAFT") or resolve_model_id(m.draft, region),
    }


def doctor(platform: PlatformConfig, region: str | None) -> list[str]:
    """起動時検証。問題があれば説明文字列のリストを返す（空＝OK）。"""
    problems: list[str] = []
    if not region:
        problems.append("region 未設定（AWS_REGION か platform.profiles[bedrock].region を設定）")
    for tier, mid in tier_ids(platform, region).items():
        if not mid or "anthropic" not in mid:
            problems.append(f"{tier}: モデルID解決失敗 ({mid!r})")
    return problems
