#!/usr/bin/env python3
"""AWS / Bedrock 疎通テスト。

環境設定に AWS 認証情報を登録した後に実行してください:
    AIIA_PROFILE=bedrock AWS_REGION=us-east-1 python scripts/aws_smoke_test.py

- シークレット値は一切表示しません。
- 認証情報の有無 → anthropic[bedrock] 導入 → Bedrockへ1トークンのテスト推論、の順に確認します。
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    profile = os.environ.get("AIIA_PROFILE", "(unset)")
    print(f"[1] region={region} / AIIA_PROFILE={profile}")

    have_creds = bool(
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("AWS_PROFILE")
        or os.environ.get("AWS_SESSION_TOKEN")
    )
    print(f"[2] AWS認証情報が環境にある: {have_creds}")
    if not have_creds:
        print("    → 未設定です。環境設定(シークレット)に AWS_ACCESS_KEY_ID 等を登録してください。")
        return 1

    try:
        from anthropic import AnthropicBedrock  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"[!] anthropic[bedrock] 未導入: {e}")
        print("    → pip install 'anthropic[bedrock]' を実行してください。")
        return 2

    # 注: 正確なBedrockモデルIDは有効化後のものに合わせてください（version接尾辞あり）。
    model = os.environ.get("AIIA_BEDROCK_TEST_MODEL", "anthropic.claude-haiku-4-5")
    print(f"[3] Bedrockテスト推論: model={model} ...")
    try:
        client = AnthropicBedrock(aws_region=region)
        msg = client.messages.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": "ping"}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        print(f"[OK] Bedrock応答: {text[:80]!r}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[NG] Bedrock呼び出し失敗: {e}")
        print("    → モデルID / IAM権限 / リージョン / ネットワーク許可 を確認してください。")
        return 3


if __name__ == "__main__":
    sys.exit(main())
