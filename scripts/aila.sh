#!/usr/bin/env bash
# AiLa 運用CLI。秘密は .env.aila（gitignore）から読む。コード/コマンドの薄いラッパ。
#   ./scripts/aila.sh smoke                 # DynamoDB+KMS 疎通（連携済みメール一覧）
#   ./scripts/aila.sh migrate [--dry-run]   # TeamAgent RDS→DynamoDB トークン移行
#   ./scripts/aila.sh batch                 # 連携済み全員の朝ダイジェスト→Slack DM配信
#   ./scripts/aila.sh serve                 # 対話常駐（編集/削除/送信2段確認）Socket Mode
#   ./scripts/aila.sh connect-web           # OAuthコールバック受け(localhost:8788)
#   ./scripts/aila.sh connect-link <email...>  # 管理者用 個別連携リンク生成
#   ./scripts/aila.sh connect-url              # 共通1リンク（Slackに貼って全員タップで連携）
#   ./scripts/aila.sh set-slack <xoxb> <xapp>  # Slackトークン設定(形式検査)
#   ./scripts/aila.sh check                 # env が埋まっているか(形式検査)
#   ./scripts/aila.sh up                    # 再起動後の一発復旧(keepalive+connect-web+serve+tunnel・冪等)
#   ./scripts/aila.sh status                # 稼働状況(プロセス+healthz+公開URL)
#   ./scripts/aila.sh down                  # AiLa関連を停止(SSM/Chromeには触れない)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env.aila ] && set -a && . ./.env.aila && set +a
PY=".venv/bin/python"
cmd="${1:-help}"; shift || true

# .env.aila の値を行単位で安全に書き換え（Mac=BSD sed / Linux=GNU sed 両対応・perm維持）。
_set_env() {  # $1=KEY $2=VALUE
  local key="$1" val="$2" tmp; tmp="$(mktemp)"
  sed "s|^export ${key}=.*|export ${key}=${val}|" .env.aila > "$tmp" && cat "$tmp" > .env.aila && rm -f "$tmp"
}

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
    chk OAUTH_REDIRECT_URI "${OAUTH_REDIRECT_URI:-}" '^https?://'                 # connect-web の callback URL
    chk CONNECT_BASE_URL "${CONNECT_BASE_URL:-http://localhost:8788}" '^https?://'  # /reply ボタンの基底URL
    [ "$miss" = 0 ] && echo "✅ env OK（形式も検査済）" || { echo "⚠ 未設定/形式不正あり（.env.aila を編集）"; exit 1; }
    ;;
  set-slack)  # ./scripts/aila.sh set-slack <xoxb-...> <xapp-...>  ※引数で渡す＝ペースト安全
    bot="${1:-}"; app="${2:-}"
    [[ "$bot" =~ ^xoxb-[A-Za-z0-9-]+$ ]] || { echo "✗ 第1引数が xoxb- 形式でない（本物のBot Tokenを渡す）"; exit 1; }
    [[ "$app" =~ ^xapp-[A-Za-z0-9-]+$ ]] || { echo "✗ 第2引数が xapp- 形式でない（本物のApp Tokenを渡す）"; exit 1; }
    _set_env SLACK_BOT_TOKEN "$bot"
    _set_env SLACK_APP_TOKEN "$app"
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
  connect-url)  # 共通1リンク（Slackに貼って全員タップ連携・本人はGoogleログインで確定）
    $PY -m aiia.scripts.make_connect_links --universal
    ;;
  connect-web)  # OAuthコールバック＋/reply 受け。host/port は env で（本番は 0.0.0.0＋リバプロ背後）
    $PY -c "import os,uvicorn;from aiia.auth.token_store import DynamoDbTokenStore,KmsCipher;\
from aiia.connect_web.app import create_app;\
store=DynamoDbTokenStore(os.environ['AIIA_DDB_TABLE'],KmsCipher(os.environ['OAUTH_KMS_KEY_ID']));\
uvicorn.run(create_app(redirect_uri=os.environ['OAUTH_REDIRECT_URI'], store=store),\
 host=os.environ.get('CONNECT_HOST','127.0.0.1'), port=int(os.environ.get('CONNECT_PORT','8788')))"
    ;;
  up)  # 再起動後の一発復旧: keepalive(スリープ抑止)+connect-web+serve+tunnel を冪等起動（既存は触らない）
    mkdir -p /tmp/aila
    _is_up() { pgrep -f "$1" >/dev/null 2>&1; }
    if _is_up 'caffeinate -dimsu'; then echo "  ✓ keepalive 稼働中"; else nohup caffeinate -dimsu >/tmp/aila/caffeinate.log 2>&1 & echo "  ▶ keepalive(caffeinate) 起動＝Mac非スリープ"; fi
    if _is_up 'aiia.connect_web.app'; then echo "  ✓ connect-web 稼働中"; else nohup "$0" connect-web >/tmp/aila/connect.log 2>&1 & echo "  ▶ connect-web 起動"; fi
    if _is_up 'aiia.runtime.slack_app'; then echo "  ✓ serve 稼働中"; else nohup "$0" serve >/tmp/aila/serve.log 2>&1 & echo "  ▶ serve 起動"; fi
    if _is_up 'cloudflared tunnel --url http://localhost:8788'; then echo "  ✓ tunnel 稼働中"; else nohup cloudflared tunnel --url http://localhost:8788 --protocol http2 >/tmp/aila/tunnel.log 2>&1 & echo "  ▶ tunnel 起動(http2)"; fi
    sleep 4
    url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/aila/tunnel.log 2>/dev/null | tail -1 || true)"
    [ -n "$url" ] && { echo "$url" > /tmp/aila_tunnel_url.txt; echo "  公開URL: $url （※ephemeral。named tunnel/Caddy運用時はそのドメイン）"; }
    exec "$0" status
    ;;
  down)  # AiLa関連を停止（keepalive含む）。SSMトンネル/Chrome等には触れない。
    for pat in 'cloudflared tunnel --url http://localhost:8788' 'aiia.runtime.slack_app' 'aiia.connect_web.app' 'caffeinate -dimsu'; do
      pkill -f "$pat" 2>/dev/null && echo "  ■ stopped: $pat" || true
    done
    ;;
  status)  # 稼働状況の一覧（プロセス＋healthz）
    echo "AiLa status:"
    pgrep -f 'caffeinate -dimsu'   >/dev/null && echo "  ✓ keepalive(非スリープ)" || echo "  ✗ keepalive なし（スリープでサービス停止の恐れ）"
    pgrep -f 'aiia.connect_web.app'>/dev/null && echo "  ✓ connect-web"            || echo "  ✗ connect-web"
    pgrep -f 'aiia.runtime.slack_app'>/dev/null && echo "  ✓ serve(Slackボタン応答)"|| echo "  ✗ serve"
    pgrep -f 'cloudflared tunnel'  >/dev/null && echo "  ✓ tunnel"                  || echo "  ✗ tunnel"
    curl -sf http://localhost:8788/healthz >/dev/null 2>&1 && echo "  ✓ healthz(localhost:8788)" || echo "  ✗ healthz"
    u="$(cat /tmp/aila_tunnel_url.txt 2>/dev/null || true)"; [ -n "$u" ] && echo "  公開URL: $u"
    ;;
  *)
    sed -n '2,10p' "$0"
    ;;
esac
