#!/usr/bin/env bash
# AiLa 運用CLI。秘密は .env.aila（gitignore）から読む。コード/コマンドの薄いラッパ。
#   ./scripts/aila.sh smoke                 # DynamoDB+KMS 疎通（連携済みメール一覧）
#   ./scripts/aila.sh migrate [--dry-run]   # TeamAgent RDS→DynamoDB トークン移行
#   ./scripts/aila.sh batch                 # 連携済み全員の朝ダイジェスト→Slack DM配信
#   ./scripts/aila.sh serve                 # 対話常駐（編集/削除/送信2段確認）Socket Mode
#   ./scripts/aila.sh connect-link <email...>  # 管理者用 連携リンク生成
#   ./scripts/aila.sh check                 # 必須 env が埋まっているか（FILL_ME 残りを警告）
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env.aila ] && set -a && . ./.env.aila && set +a
PY=".venv/bin/python"
cmd="${1:-help}"; shift || true

case "$cmd" in
  check)
    miss=0
    for k in AWS_REGION AIIA_DDB_TABLE OAUTH_KMS_KEY_ID OAUTH_STATE_SECRET \
             CONNECT_GOOGLE_CLIENT_ID CONNECT_GOOGLE_CLIENT_SECRET SLACK_BOT_TOKEN SLACK_APP_TOKEN; do
      v="${!k:-}"
      if [ -z "$v" ] || [[ "$v" == FILL_ME* ]]; then echo "  ✗ $k 未設定/FILL_ME"; miss=1; else echo "  ✓ $k"; fi
    done
    [ "$miss" = 0 ] && echo "✅ env OK" || { echo "⚠ 未設定あり（.env.aila を編集）"; exit 1; }
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
store=DynamoDbTokenStore(os.environ['AIIA_DDB_TABLE'],KmsCipher(os.environ['OAUTH_KMS_KEY_ID']));\
[print(r) for r in run_for_all_users(store=store, slack=SlackDelivery(), dry_run=False, max_budget_usd=1.0)]"
    ;;
  serve)
    $PY -m aiia.runtime.slack_app
    ;;
  connect-link)
    $PY -m aiia.scripts.make_connect_links "$@"
    ;;
  *)
    sed -n '2,9p' "$0"
    ;;
esac
