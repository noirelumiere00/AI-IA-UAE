"""AWS Lambda (Function URL) エントリ。connect_web を ngrok/ドメイン無しで公開する（§V6改）。

Lambda Function URL が無料・固定HTTPS を提供（`https://<id>.lambda-url.<region>.on.aws`）。
OAuthコールバック(/oauth2/callback・/oauth2/slack/callback)＋[対応する](/reply) を1 Lambdaが捌く。
state は HMAC 署名（CSRF防止）・/reply は署名トークン検証＝公開エンドポイントでも安全。

**秘密値は Lambda env に置かない**（漏洩面）。`AIIA_SECRET_ID` の Secrets Manager シークレット
（JSON）をコールドスタート時に取得し os.environ へ注入してから build_app() する。
シークレットに入れる例: OAUTH_KMS_KEY_ID / OAUTH_STATE_SECRET / CONNECT_GOOGLE_CLIENT_ID /
CONNECT_GOOGLE_CLIENT_SECRET / SLACK_BOT_TOKEN / SLACK_CLIENT_ID / SLACK_CLIENT_SECRET /
AIIA_DDB_TABLE / AIIA_SLACK_TOKEN_TABLE / OAUTH_REDIRECT_URI / SLACK_OAUTH_REDIRECT_URI。
Lambda env には AIIA_SECRET_ID（=シークレット名）だけ置く。
"""
from __future__ import annotations

import json
import os
from typing import Any


def _load_secrets() -> None:
    """AIIA_SECRET_ID が指す Secrets Manager の JSON を os.environ へ注入（無ければ無視）。"""
    secret_id = os.environ.get("AIIA_SECRET_ID")
    if not secret_id:
        return
    import boto3  # Lambda ランタイム同梱

    region = os.environ.get("AWS_REGION") or "ap-northeast-1"
    raw = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=secret_id)
    data = json.loads(raw["SecretString"])
    for k, v in data.items():
        if v is not None:
            os.environ[str(k)] = str(v)  # 既存 env を上書き（シークレットが真実源）


_load_secrets()

from mangum import Mangum  # noqa: E402  ← secrets 注入後に import/構築

from aiia.connect_web.serve import build_app  # noqa: E402

handler: Any = Mangum(build_app(), lifespan="off")
