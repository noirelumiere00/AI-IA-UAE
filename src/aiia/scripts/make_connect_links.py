"""管理者向け: 連携リンクを出力（PoC onboarding）。2モード。

- 個別リンク: `python -m aiia.scripts.make_connect_links <email> [<email> ...]`
    → 1行ごとに `email<TAB>連携URL`（本人専用・state にメールを署名）。
- 共通1リンク(Google): `python -m aiia.scripts.make_connect_links --universal`
    → URL を1行だけ出力。**Slackに貼って全員がタップ→自分のGoogleで連携**（本人は id_token で確定）。
- 共通1リンク(Slack): `python -m aiia.scripts.make_connect_links --universal-slack`
    → Slackユーザー認可(xoxp)の共通リンクを1行出力。**Slackに貼って全員タップ→自分のSlackを認可**
      （未返信メンションリマインド用。本人は users.info で確定）。要 SLACK_CLIENT_ID / SLACK_OAUTH_REDIRECT_URI。
      公開URLなので AIIA_CONNECT_ALLOWED_DOMAINS（or AIIA_DEFAULT_INTERNAL_DOMAIN）で社用ドメインに限定推奨。

要 env: OAUTH_STATE_SECRET / OAUTH_REDIRECT_URI / (CONNECT_GOOGLE_|GOOGLE_)CLIENT_ID/SECRET。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from aiia.auth.oauth_flow import OAuthConsentFlow


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    universal = "--universal" in args
    universal_slack = "--universal-slack" in args
    emails = [a for a in args if not a.startswith("--")]
    if universal_slack:
        from aiia.auth.slack_oauth import SlackOAuthConsentFlow

        redirect = os.environ.get("SLACK_OAUTH_REDIRECT_URI") or os.environ.get(
            "OAUTH_REDIRECT_URI"
        )
        if not redirect:
            print(
                "SLACK_OAUTH_REDIRECT_URI（または OAUTH_REDIRECT_URI）が未設定です", file=sys.stderr
            )
            return 2
        url, _ = SlackOAuthConsentFlow(redirect).authorization_url_universal()
        print(url)  # Slack共通1リンク（URLのみ・そのままSlackに貼れる）
        return 0
    if not universal and not emails:
        print(
            "usage: make_connect_links <email> [<email> ...] | --universal | --universal-slack",
            file=sys.stderr,
        )
        return 2
    redirect_uri = os.environ.get("OAUTH_REDIRECT_URI")
    if not redirect_uri:
        print("OAUTH_REDIRECT_URI が未設定です", file=sys.stderr)
        return 2
    flow = OAuthConsentFlow(redirect_uri)
    if universal:
        url, _ = flow.authorization_url_universal()
        print(url)  # 共通1リンク（URLのみ・そのままSlackに貼れる）
        return 0
    for email in emails:
        url, _ = flow.authorization_url(email)
        print(f"{email}\t{url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
