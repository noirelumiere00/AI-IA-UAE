"""AWS Lambda (Function URL) エントリ。connect_web を ngrok/ドメイン無しで公開する（§V6改）。

Lambda Function URL が無料・固定HTTPS を提供（`https://<id>.lambda-url.<region>.on.aws`）。
OAuthコールバック(/oauth2/callback・/oauth2/slack/callback)＋[対応する](/reply) を1 Lambdaが捌く。
state は HMAC 署名（CSRF防止）・/reply は署名トークン検証＝公開エンドポイントでも安全。

env（Lambda設定）: OAUTH_KMS_KEY_ID / AIIA_DDB_TABLE / OAUTH_REDIRECT_URI /
  (任意) AIIA_SLACK_TOKEN_TABLE + SLACK_CLIENT_ID/SECRET + SLACK_OAUTH_REDIRECT_URI /
  OAUTH_STATE_SECRET / CONNECT_GOOGLE_CLIENT_ID/SECRET。
build_app() が env から FastAPI を構築するので、ここは Mangum で包むだけ。
"""
from __future__ import annotations

from typing import Any

from mangum import Mangum

from aiia.connect_web.serve import build_app

# モジュールロード時に1度だけ構築（Lambda コンテナ再利用でウォーム）。
handler: Any = Mangum(build_app(), lifespan="off")
