"""connect-web 起動ランチャ（aila.sh connect-web から `python -m aiia.connect_web.serve`）。

Google連携(/oauth2/callback)＋[対応する](/reply) は常時。SLACK_CLIENT_ID と AIIA_SLACK_TOKEN_TABLE が
揃う時だけ Slackユーザー認可(/oauth2/slack/callback) を生やす（別テーブル aiia-slack-tokens に xoxp 保管）。
"""

from __future__ import annotations

import os
from typing import Any


def build_app() -> Any:
    from aiia.auth.token_store import DynamoDbTokenStore, KmsCipher
    from aiia.connect_web.app import create_app

    cipher = KmsCipher(os.environ["OAUTH_KMS_KEY_ID"])
    store = DynamoDbTokenStore(os.environ["AIIA_DDB_TABLE"], cipher)
    redirect_uri = os.environ["OAUTH_REDIRECT_URI"]

    kwargs: dict[str, Any] = {}
    slack_table = os.environ.get("AIIA_SLACK_TOKEN_TABLE")
    if os.environ.get("SLACK_CLIENT_ID") and slack_table:
        # xoxp は別テーブルに（Googleと混ざらない・同じKMSキーで暗号化・EncryptionContext={user_email}）。
        kwargs["slack_store"] = DynamoDbTokenStore(slack_table, cipher)
        kwargs["slack_redirect_uri"] = os.environ.get("SLACK_OAUTH_REDIRECT_URI", redirect_uri)
    return create_app(redirect_uri=redirect_uri, store=store, **kwargs)


def main() -> None:
    import uvicorn

    uvicorn.run(
        build_app(),
        host=os.environ.get("CONNECT_HOST", "127.0.0.1"),
        port=int(os.environ.get("CONNECT_PORT", "8788")),
    )


if __name__ == "__main__":
    main()
