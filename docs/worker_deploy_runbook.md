# AiLa worker デプロイ runbook（§V6/§V7・2026-06-12）

統合AiLa（朝メール要約＋カレンダー5分前通知）を **既存 EC2 `teamagent-dev-worker`** で 10名運用するための手順。
OpenClaw（会社ナレッジ）と**同じ1つのSlackアプリ**を共用し、AiLaは **bot push＋markdownリンクのみ**（Socket Mode 非保持）。

## 済（このセッションで完了）
- AiLaコード §V1–V5＋V4（224 tests green・branch `claude/vibrant-galileo-lXVG6`・push済）。
- DynamoDB 4表：`aiia-oauth-tokens` / `aiia-reminder-state` / `aiia-slack-tokens`(新) / `aiia-notified-events`(新・TTL)。
- worker IAMロール `teamagent-dev-worker` にインラインポリシー `aiia-runtime`（DynamoDB4表 / KMS `teamagent-oauth-tokens` / Bedrock anthropic）追加。
- worker に clone＋python3.11 venv＋`pip install -e .[multiuser,bedrock]`＋import/entrypoint 検証 OK（`/home/ec2-user/AI-IA-UAE`）。

## 公開URL＝ngrok 無料静的ドメイン（cloudflareドメイン不要・URL不変）
**なぜ**：OAuthコールバック(`/oauth2/callback`・`/oauth2/slack/callback`)は Google/Slack コンソールに**一度登録した固定URL**が要る。ドメインを持たずに固定HTTPSを得る最簡手段が ngrok 無料static domain（1アカウントにつき1つ無料・再起動で不変）。
1. https://dashboard.ngrok.com で無料サインアップ → **Your Authtoken** を控える。
2. 左メニュー **Domains** → 無料の static domain（例 `vector-aila.ngrok-free.app`）を1つ作成。
3. （worker側は私が設定）authtoken を SSM Parameter(SecureString) 経由で worker に渡し、`ngrok http 8788 --domain=<static>` を systemd 常駐化。

## .env.aila（worker /home/ec2-user/AI-IA-UAE/.env.aila・perm600）
既存値（Mac）を基に、**公開URLを ngrok static に差し替え**＋Slack User OAuth を追記：
- `AIIA_PROFILE=bedrock` / `AWS_REGION=ap-northeast-1`
- `AIIA_DDB_TABLE=aiia-oauth-tokens` / `AIIA_REMINDER_TABLE=aiia-reminder-state`
- `AIIA_SLACK_TOKEN_TABLE=aiia-slack-tokens`（←追記＝両連携ゲート有効化）
- `AIIA_NOTIFY_TABLE=aiia-notified-events`（既定一致・任意）
- `OAUTH_KMS_KEY_ID=<alias/teamagent-oauth-tokens の鍵ARN>`
- `OAUTH_STATE_SECRET=<32hex>`（既存）
- `CONNECT_GOOGLE_CLIENT_ID/SECRET`（既存＝TeamAgent OAuth client 流用）
- `SLACK_BOT_TOKEN`(xoxb)/`SLACK_APP_TOKEN`(xapp)（共用アプリ＝OpenClawと同じ）
- `SLACK_CLIENT_ID`/`SLACK_CLIENT_SECRET`（←共用アプリの Basic Information → App Credentials）
- `CONNECT_BASE_URL=https://<static>.ngrok-free.app`
- `OAUTH_REDIRECT_URI=https://<static>.ngrok-free.app/oauth2/callback`
- `SLACK_OAUTH_REDIRECT_URI=https://<static>.ngrok-free.app/oauth2/slack/callback`
> 秘密値はチャットに貼らない。Mac の .env.aila を Secrets Manager `aiia/prod/env` に put（file://）→ worker が取得して書き出す方式を私が用意。

## Slack 共用アプリに User OAuth 設定追加（コンソール・本人）
api.slack.com/apps → 該当アプリ：
- **OAuth & Permissions → User Token Scopes** に：`search:read` `channels:history` `groups:history` `mpim:history`
- **Redirect URLs** に `https://<static>.ngrok-free.app/oauth2/slack/callback` を追加 → Save
- （Bot scope は既に配信に必要な分が入っている）→ 変更後 **Reinstall**。

## Google OAuth（コンソール・本人）
TeamAgent の OAuth client（Internal）に Redirect URI `https://<static>.ngrok-free.app/oauth2/callback` を追加。

## worker 常駐（私が SSM で設定）
- systemd: `aila-connect`(connect_web :8788 常駐) / `aila-ngrok`(ngrok 常駐) / timer `aila-batch`(平日9:30 JST=UTC0:30) / timer `aila-notify`(毎分)。
- 確認：`aila.sh check`（env形式）→ `aila.sh smoke`（DynamoDB疎通）→ `/healthz` 200。

## §V7 10名連携→E2E
1. 2リンク発行：`aila.sh connect-url`(Google) ＋ `aila.sh connect-url-slack`(Slack)。
2. 10名へ配布（専用ch or DM）→各自タップで Google＋Slack 両方認可（両方揃って有効）。
3. `aila.sh smoke` で連携済み一覧に10名→`aila.sh batch` 手動実行で朝ダイジェストDM受信を確認。
4. 予定5分前メンションを実機確認（テスト予定をnow+5分に作って待つ）。
5. 翌朝から自動（batch 9:30 / notify 毎分）。

## ロールバック
`systemctl stop aila-*`（AiLa停止）。OpenClaw（会社ナレッジ）は無影響＝独立。
