"""管理者向け: メールアドレスから per-user 連携リンク(TSV)を出力（PoC onboarding）。

要 env: OAUTH_STATE_SECRET / OAUTH_REDIRECT_URI / (CONNECT_GOOGLE_|GOOGLE_)CLIENT_ID/SECRET。
出力: 1行ごとに `email<TAB>連携URL`。管理者が本人へ安全な経路で配布し、本人が許可→callbackで保管。
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from aiia.auth.oauth_flow import OAuthConsentFlow


def main(argv: Optional[list[str]] = None) -> int:
    emails = list(sys.argv[1:] if argv is None else argv)
    if not emails:
        print("usage: python -m aiia.scripts.make_connect_links <email> [<email> ...]", file=sys.stderr)
        return 2
    redirect_uri = os.environ.get("OAUTH_REDIRECT_URI")
    if not redirect_uri:
        print("OAUTH_REDIRECT_URI が未設定です", file=sys.stderr)
        return 2
    flow = OAuthConsentFlow(redirect_uri)
    for email in emails:
        url, _ = flow.authorization_url(email)
        print(f"{email}\t{url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
