#!/usr/bin/env bash
# AiLa EC2(Ubuntu) ほぼ1コマンド設置スクリプト。
#
# 前提（あなたが先にやる4つだけ）:
#   1. Ubuntu 22.04+ の小型EC2(t3.small程度)を起動し、SG で 22/80/443 を開放。
#   2. ドメイン（例 connect-aila.vectorinc.co.jp）の A/AAAA を EC2 のIPに向ける。
#   3. このリポジトリ一式を EC2 に置く（git clone でも rsync でも scp でも可）。
#   4. 同梱の .env.aila（本物の値・perm600）をリポジトリ直下に置く。← 秘密。git に入れない。
#
# 実行:
#   sudo bash deploy/deploy.sh connect-aila.vectorinc.co.jp
#
# やること: aila専用ユーザー作成 → /opt/aila へ配置 → venv + 依存 → Caddy(自動HTTPS) →
#           systemd で connect-web/serve 常駐 + 朝バッチ(平日9:30 JST) 有効化。
# 冪等: 何度流しても安全（既存は上書き/再起動）。
set -euo pipefail

DOMAIN="${1:-}"
[ -n "$DOMAIN" ] || { echo "usage: sudo bash deploy/deploy.sh <DOMAIN>   例: connect-aila.vectorinc.co.jp" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "root で実行してください（sudo bash deploy/deploy.sh <DOMAIN>）" >&2; exit 2; }

SRC="$(cd "$(dirname "$0")/.." && pwd)"          # このリポジトリのルート
DEST=/opt/aila
echo "▶ source=$SRC  dest=$DEST  domain=$DOMAIN"

[ -f "$SRC/.env.aila" ] || { echo "✗ $SRC/.env.aila が見つかりません（本物の値で配置してから再実行）" >&2; exit 2; }

# 1) 専用ユーザー + 配置
id aila >/dev/null 2>&1 || useradd -m -d "$DEST" -s /usr/sbin/nologin aila
mkdir -p "$DEST"
echo "▶ ファイル同期 $SRC → $DEST"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --exclude '.git' --exclude '.venv' "$SRC"/ "$DEST"/
else
  cp -a "$SRC"/. "$DEST"/
fi
chown -R aila:aila "$DEST"
chmod 600 "$DEST/.env.aila"

# 2) Python venv + 依存
apt-get update -y
apt-get install -y python3-venv python3-pip rsync caddy || {
  # caddy が無いリポジトリの場合は公式手順で追加
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
}
sudo -u aila python3 -m venv "$DEST/.venv"
sudo -u aila "$DEST/.venv/bin/pip" install --upgrade pip
sudo -u aila "$DEST/.venv/bin/pip" install -e "$DEST"'[multiuser,bedrock]'

# 3) Caddy（自動HTTPS・<DOMAIN> を置換）
install -d /etc/caddy
sed "s|<DOMAIN>|$DOMAIN|g" "$DEST/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy

# 4) systemd 常駐（connect-web / serve）+ 朝バッチ timer
cp "$DEST"/deploy/systemd/aila-*.service "$DEST"/deploy/systemd/aila-batch.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now aila-connect aila-serve aila-batch.timer

cat <<EOF

✅ 設置完了。残り（あなた）:
  1) $DEST/.env.aila を本番URLに:
       OAUTH_REDIRECT_URI=https://$DOMAIN/oauth2/callback
       CONNECT_BASE_URL=https://$DOMAIN
       AIIA_CONNECT_ALLOWED_DOMAINS=vectorinc.co.jp   # 共通リンクの連携を社用に限定（任意だが推奨）
     → sudo systemctl restart aila-connect aila-serve
  2) GCP(pgd1 OAuthクライアント) の「承認済みリダイレクトURI」に
       https://$DOMAIN/oauth2/callback  を追加して保存。
  3) 共通1リンクを発行して Slack に貼る:
       sudo -u aila bash -lc 'cd $DEST && source .env.aila && ./scripts/aila.sh connect-url'
  検証:
       curl -s https://$DOMAIN/healthz        # {"ok":true}
       sudo -u aila bash -lc 'cd $DEST && source .env.aila && ./scripts/aila.sh smoke'
       sudo systemctl start aila-batch        # 初回配信
       journalctl -u aila-batch -n 50
EOF
