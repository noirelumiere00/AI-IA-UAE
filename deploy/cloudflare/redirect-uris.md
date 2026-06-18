# OAuth Redirect URI / 環境変数 設定値（確定）

公開ホスト名は **`connect.vectorinc.co.jp`**。connect-web が実装しているコールバックパスは:
- Google: `/oauth2/callback`
- Slack : `/oauth2/slack/callback`（`SLACK_CLIENT_ID` と `AIIA_SLACK_TOKEN_TABLE` の両方が env にある時だけ生える）

（出典: `src/aiia/connect_web/app.py` の `@app.get("/oauth2/callback")` / `@app.get("/oauth2/slack/callback")`、
`src/aiia/connect_web/serve.py` の `OAUTH_REDIRECT_URI` / `SLACK_OAUTH_REDIRECT_URI` 読み取り）

---

## 1. Google（OAuth クライアント／Internal タイプ）

Google Cloud Console → APIs & Services → 認証情報 → 対象 OAuth 2.0 クライアント ID →
「承認済みのリダイレクト URI」に以下を**追加**:

| 用途 | Redirect URI |
| --- | --- |
| 本番（会社ドメイン） | `https://connect.vectorinc.co.jp/oauth2/callback` |
| ローカル検証 | `http://localhost:8788/oauth2/callback` |

> このクライアントは **Internal（社内）タイプ** である前提。Internal なら OAuth 同意画面の
> Google 審査（verification）なしで redirect URI を追加・運用できる。
> （External だと審査対象になりうるため、必ず Internal であることを確認すること。）

## 2. Slack（App の Redirect URLs）

api.slack.com/apps → 対象アプリ → **OAuth & Permissions → Redirect URLs** に以下を**追加**:

| 用途 | Redirect URL |
| --- | --- |
| 本番（会社ドメイン） | `https://connect.vectorinc.co.jp/oauth2/slack/callback` |
| ローカル検証 | `http://localhost:8788/oauth2/slack/callback` |

差し替え元のプレースホルダは `infra/slack/aila_app_manifest.yaml` の
`oauth_config.redirect_urls`（現状 `https://REPLACE-WITH-PUBLIC-HOST/oauth2/slack/callback`）。
manifest を再適用する場合はこの行を `https://connect.vectorinc.co.jp/oauth2/slack/callback` に書き換える。

> Slack は複数 Redirect URL 可・後編集可。ローカル検証用と本番用を両方登録しておいてよい。
> Slack 連携を使わない（メールのみ）段階導入なら、この節はスキップして問題ない。

---

## 3. 設定する環境変数（`.env.aila`）

connect-web / connect リンク生成が参照する env を、会社ドメインに差し替える。

```sh
# Google OAuth コールバック（connect-web が必須で読む）
export OAUTH_REDIRECT_URI=https://connect.vectorinc.co.jp/oauth2/callback

# /reply ボタンの基底 URL（返信ドラフト url-button が叩く）
export CONNECT_BASE_URL=https://connect.vectorinc.co.jp

# Slack User OAuth コールバック（Slack 連携を使う時だけ）
export SLACK_OAUTH_REDIRECT_URI=https://connect.vectorinc.co.jp/oauth2/slack/callback
```

### ローカル検証用（差し替え前 / 切り戻し用の控え）
```sh
export OAUTH_REDIRECT_URI=http://localhost:8788/oauth2/callback
export CONNECT_BASE_URL=http://localhost:8788
export SLACK_OAUTH_REDIRECT_URI=http://localhost:8788/oauth2/slack/callback
```

> connect-web 側の待受は `CONNECT_HOST=127.0.0.1` / `CONNECT_PORT=8788` のままでよい
> （cloudflared が localhost:8788 に繋ぐ。0.0.0.0 公開は不要）。
> env 反映の検査は `./scripts/aila.sh check`、反映後は **connect-web を再起動**すること
> （Slack パスを新たに生やす場合は特に必須）。
