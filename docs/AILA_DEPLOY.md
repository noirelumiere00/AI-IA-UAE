# AiLa 本番化 ＋ TeamAgent コスト最小化 runbook（B）

利用者＝vectorinc.co.jp 同一org。**pgd1(web) OAuthクライアントを流用＋既存トークンを移行＝全員再連携不要**。
Gmail scope＝`gmail.modify` 一本（送信は drafts.send・2段確認ゲート経由のみ）。AWS＝vector ap-northeast-1。
**AWS書込/GCP/Slack設定はあなたが実行**（私はコード＋スクリプト＋本手順を用意済）。

> ⚠️ **順序厳守**：トークン移行 → 件数照合 → AiLa疎通 → **その後に RDS teardown**。逆順は復号不可。

---

## 0. 前提
- AWS creds（vector・ap-northeast-1・KMS/DynamoDB/EC2/SSM 権限）
- GCP `ntv-ai` プロジェクト（OAuth同意画面 Internal）の編集権限
- Slack ワークスペース管理者（新規アプリ作成可）
- ローカル: `cd ~/Documents/AI-IA-UAE && uv pip install -e '.[multiuser,bedrock]'`

## 1. AiLa Slack アプリ（新規・本番）
1. api.slack.com/apps → Create New App → 名前 **AiLa**。
2. **Socket Mode** を ON → App-Level Token 発行（`connections:write`）＝`SLACK_APP_TOKEN`(xapp-)。
3. **OAuth & Permissions** → Bot Token Scopes：`chat:write` / `users:read.email` / `channels:history` / `groups:history` / `commands`。
4. **Slash Commands** → `/connect` を追加。
5. ワークスペースにインストール → Bot Token `SLACK_BOT_TOKEN`(xoxb-)。
6. Interactivity を ON（ボタン/モーダル）。

## 2. AWS（ap-northeast-1・最小）
```bash
export AWS_REGION=ap-northeast-1
# DynamoDB: per-user トークン（PK=user_email）
aws dynamodb create-table --table-name aiia-oauth-tokens \
  --attribute-definitions AttributeName=user_email,AttributeType=S \
  --key-schema AttributeName=user_email,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region ap-northeast-1
# （任意）文体キャッシュ。当面は run 内メモリで足りるので後回し可
# KMS は既存 teamagent-oauth-tokens 鍵を流用（最小化）。鍵ARNを控える:
aws kms describe-key --key-id alias/teamagent-oauth-tokens --region ap-northeast-1 \
  --query KeyMetadata.Arn --output text   # → OAUTH_KMS_KEY_ID に使う
```

## 3. GCP（pgd1 に redirect_uri 追加）
1. GCP Console → API とサービス → 認証情報 → **pgd1...（web型 連携用）** を開く。
2. 「承認済みのリダイレクト URI」に **AI-IA-UAE connect_web の URL** を追加
   （例 `https://connect.aila.example/oauth2/callback`／ローカル検証なら `http://localhost:8788/oauth2/callback`）。
3. OAuth同意画面のスコープに **`gmail.modify`** が含まれることを確認（Internal＝Google審査不要）。

## 4. env（`~/Documents/AI-IA-UAE/.env.aila` 等にまとめる）
```bash
export AWS_REGION=ap-northeast-1
export AIIA_DDB_TABLE=aiia-oauth-tokens
export OAUTH_KMS_KEY_ID=arn:aws:kms:ap-northeast-1:718959508629:key/...   # 手順2の鍵
export AIIA_PROFILE=bedrock
# Google（pgd1 流用）
export CONNECT_GOOGLE_CLIENT_ID=676659122211-pgd1mj4et6sf7uqqmsni2b3kmbbd8qeg.apps.googleusercontent.com
export CONNECT_GOOGLE_CLIENT_SECRET=...   # Secrets Manager teamagent/dev/connect_google_secret
export OAUTH_STATE_SECRET=...             # 任意の長い乱数
export OAUTH_REDIRECT_URI=https://connect.aila.example/oauth2/callback
# Slack（AiLa）
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
# Bedrock（ap-northeast-1 で Claude 未許可なら us-east-1 に変え、AIIA_BEDROCK_MODEL_* で上書き）
```

## 5. トークン移行（RDS → DynamoDB・全員再連携不要）
SSM 踏み台等で TeamAgent RDS に到達できる環境で：
```bash
export DATABASE_URL='postgresql://teamagent:***@teamagent-dev....rds.amazonaws.com:5432/teamagent?sslmode=require'
export SOURCE_KMS_KEY_ID=$OAUTH_KMS_KEY_ID   # TeamAgentのoauth-tokens鍵（流用なら同じ）
export TARGET_KMS_KEY_ID=$OAUTH_KMS_KEY_ID
python -m aiia.scripts.migrate_tokens --dry-run    # 件数とメール確認（書込なし）
python -m aiia.scripts.migrate_tokens              # 移行実行
# → "DynamoDB aiia-oauth-tokens へ N 件移行完了 / 現在件数 N"
```

## 6. AiLa 起動 ＋ 朝バッチ
```bash
source .env.aila
# (a) 朝ダイジェスト生成＋Slack DM配信（連携済み全員）
python -c "from aiia.auth.token_store import DynamoDbTokenStore,KmsCipher; \
from aiia.adapters.slack_client import SlackDelivery; from aiia.orchestrator.multi import run_for_all_users; import os; \
store=DynamoDbTokenStore(os.environ['AIIA_DDB_TABLE'],KmsCipher(os.environ['OAUTH_KMS_KEY_ID'])); \
print([(r.email,r.ok,r.processed,r.delivered) for r in run_for_all_users(store=store, slack=SlackDelivery(), dry_run=False, max_budget_usd=1.0)])"
# (b) 対話ボタン常駐（編集/削除/送信2段確認）
python -m aiia.runtime.slack_app   # run() が Socket Mode 起動
```
- 新規ユーザー：Slack で `/connect` → 表示URLで許可（pgd1・gmail.modify）。
- 毎朝自動化：(a) を EventBridge→スケジュール（常駐不要）。(b) は対話用に常駐。

## 7. ライブ検証（慎重に）
1. 自分(s-komata)の Slack DM に重要度タブ＋ボタンのダイジェストが届く。
2. **送信は最初に「自分宛のテストメール」で1通だけ**：[📤送信]→確認(1/2)→モーダル(2/2)→実送信を確認。
3. [編集]（モーダル保存→drafts.update）／[削除]（drafts.delete）も確認。

## 8. TeamAgent コスト最小化（Bedrock/VSEO 保持・RAG 解放）
**保持**：Bedrock、S3(raw-files/VSEO成果)、Secrets、KMS、DynamoDB(tflock)、CloudWatch/Trail、**VSEO/動画ツール**。
```bash
cd ~/Documents/TeamAgent/infra   # terraform 管理
# (1) 一番効く：常駐 Worker EC2 を停止/破棄（Mac or AiLa が代替）
terraform destroy -target=aws_instance.worker -target=aws_iam_instance_profile.worker \
  -target=aws_iam_role.worker -target=aws_iam_role_policy.worker_app \
  -target=aws_security_group.worker
# (2) RAG=RDS を解放（★トークン移行・照合の後に）
#     destroy前に VSEO が RDS(usage_events 等)非依存を確認！依存あればS3/CloudWatchへ振替 or 無効化
terraform destroy -target=aws_db_instance.main   # 実リソース名は rds.tf を確認
# (3) 踏み台 EC2 を破棄（RDS無くなれば不要）
terraform destroy -target=aws_instance.bastion -target=aws_iam_instance_profile.bastion \
  -target=aws_iam_role.bastion -target=aws_security_group.bastion
```
- **AiLa の対話常駐先**：当面 Mac（PoC）／本番は小型EC2 1台 or Fargate。朝バッチは Lambda/EventBridge で常駐不要。
- 完全に EC2 ゼロにするなら：対話部=Fargate、朝バッチ=Lambda。

## ロールバック / 注意
- 移行は**冪等**（再実行で上書き）。RDS teardown 前なら何度でもやり直せる。
- `gmail.modify` は広い権限。安全はコード（送信は2段確認ゲート経由のみ・送信/破壊系を toolset に出さない・全送信を監査・redaction）。社員へ HITL 周知。
- Bedrock が ap-northeast-1 で Claude 未許可なら `AWS_REGION=us-east-1` ＋ `AIIA_BEDROCK_MODEL_*` で us. プロファイルに上書き（本日 us-east-1 実走実績あり）。
