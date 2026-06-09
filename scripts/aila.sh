#!/usr/bin/env bash
# AiLa 運用CLI。秘密は .env.aila（gitignore）から読む。コード/コマンドの薄いラッパ。
#   ./scripts/aila.sh smoke                 # DynamoDB+KMS 疎通（連携済みメール一覧）
#   ./scripts/aila.sh migrate [--dry-run]   # TeamAgent RDS→DynamoDB トークン移行
#   ./scripts/aila.sh batch                 # 連携済み全員の朝ダイジェスト→Slack DM配信
#   ./scripts/aila.sh serve                 # 対話常駐（編集/削除/送信2段確認）Socket Mode
#   ./scripts/aila.sh connect-web           # OAuthコールバック受け(localhost:8788)
#   ./scripts/aila.sh connect-link <email...>  # 管理者用 連携リンク生成
#   ./scripts/aila.sh set-slack <xoxb> <xapp>  # Slackトークン設定(形式検査)
#   ./scripts/aila.sh check                 # env が埋まっているか(形式検査)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env.aila ] && set -a && . ./.env.aila && set +a
PY=".venv/bin/python"
cmd="${1:-help}"; shift || true

case "$cmd" in
  check)
    miss=0
    chk() {  # $1=name $2=value $3=正規表現(任意・形式検査)
      local k="$1" v="$2" re="${3:-}"
      if [ -z "$v" ] || [[ "$v" == FILL_ME* ]]; then echo "  ✗ $k 未設定/FILL_ME"; miss=1; return; fi
      if [ -n "$re" ] && ! [[ "$v" =~ $re ]]; then echo "  ✗ $k 形式不正（プレースホルダのまま？本物の値に直す）"; miss=1; return; fi
      echo "  ✓ $k"
    }
    chk AWS_REGION "${AWS_REGION:-}"
    chk AIIA_DDB_TABLE "${AIIA_DDB_TABLE:-}"
    chk OAUTH_KMS_KEY_ID "${OAUTH_KMS_KEY_ID:-}" '^arn:aws:kms:'
    chk OAUTH_STATE_SECRET "${OAUTH_STATE_SECRET:-}" '^[0-9a-f]{32,}$'
    chk CONNECT_GOOGLE_CLIENT_ID "${CONNECT_GOOGLE_CLIENT_ID:-}" 'apps\.googleusercontent\.com$'
    chk CONNECT_GOOGLE_CLIENT_SECRET "${CONNECT_GOOGLE_CLIENT_SECRET:-}" '^GOCSPX-[A-Za-z0-9_-]+$'
    chk SLACK_BOT_TOKEN "${SLACK_BOT_TOKEN:-}" '^xoxb-[A-Za-z0-9-]+$'
    chk SLACK_APP_TOKEN "${SLACK_APP_TOKEN:-}" '^xapp-[A-Za-z0-9-]+$'
    [ "$miss" = 0 ] && echo "✅ env OK（形式も検査済）" || { echo "⚠ 未設定/形式不正あり（.env.aila を編集）"; exit 1; }
    ;;
  set-slack)  # ./scripts/aila.sh set-slack <xoxb-...> <xapp-...>  ※引数で渡す＝ペースト安全
    bot="${1:-}"; app="${2:-}"
    [[ "$bot" =~ ^xoxb-[A-Za-z0-9-]+$ ]] || { echo "✗ 第1引数が xoxb- 形式でない（本物のBot Tokenを渡す）"; exit 1; }
    [[ "$app" =~ ^xapp-[A-Za-z0-9-]+$ ]] || { echo "✗ 第2引数が xapp- 形式でない（本物のApp Tokenを渡す）"; exit 1; }
    sed -i '' "s|^export SLACK_BOT_TOKEN=.*|export SLACK_BOT_TOKEN=$bot|" .env.aila
    sed -i '' "s|^export SLACK_APP_TOKEN=.*|export SLACK_APP_TOKEN=$app|" .env.aila
    echo "✅ Slackトークンを .env.aila に設定（形式検査OK）"
    exec "$0" check
    ;;
  smoke)
    $PY -c "import os;from aiia.auth.token_store import DynamoDbTokenStore,KmsCipher;\
print('連携済み:', DynamoDbTokenStore(os.environ['AIIA_DDB_TABLE'],KmsCipher(os.environ['OAUTH_KMS_KEY_ID'])).list_emails())"
    ;;
  migrate)
    $PY -m aiia.scripts.migrate_tokens "$@"
    ;;
  batch)
    $PY -c "import os;from aiia.auth.token_store import DynamoDbTokenStore,KmsCipher;\
from aiia.adapters.slack_client import SlackDelivery;from aiia.orchestrator.multi import run_for_all_users;\
from aiia.state.reminder_store import DynamoDbReminderStore;\
store=DynamoDbTokenStore(os.environ['AIIA_DDB_TABLE'],KmsCipher(os.environ['OAUTH_KMS_KEY_ID']));\
rstore=DynamoDbReminderStore(os.environ.get('AIIA_REMINDER_TABLE','aiia-reminder-state'));\
[print(r) for r in run_for_all_users(store=store, slack=SlackDelivery(), dry_run=False, max_budget_usd=1.0, reminder_store=rstore)]"
    ;;
  serve)
    $PY -m aiia.runtime.slack_app
    ;;
  connect-link)
    $PY -m aiia.scripts.make_connect_links "$@"
    ;;
  connect-web)  # OAuthコールバック受け（localhost:8788）。/connect や connect-link のリンク先
    $PY -c "import os,uvicorn;from aiia.auth.token_store import DynamoDbTokenStore,KmsCipher;\
from aiia.connect_web.app import create_app;\
store=DynamoDbTokenStore(os.environ['AIIA_DDB_TABLE'],KmsCipher(os.environ['OAUTH_KMS_KEY_ID']));\
uvicorn.run(create_app(redirect_uri=os.environ['OAUTH_REDIRECT_URI'], store=store), host='127.0.0.1', port=8788)"
    ;;
  *)
    sed -n '2,10p' "$0"
    ;;
esac
