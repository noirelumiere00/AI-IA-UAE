# deploy/cloudflare/systemd/ — Cloudflare Tunnel 常駐ユニット

このディレクトリは Plan C（Cloudflare Tunnel + Cloudflare Access）専用の systemd ユニットを置く。
既存の `deploy/systemd/` の流儀（WorkingDirectory=/opt/aila・User=aila・Restart=always RestartSec）を踏襲している。

## 含まれるユニット

### `aila-cloudflared.service`（このディレクトリで新規追加）
cloudflared を常駐させ、`connect.vectorinc.co.jp` 宛の外部アクセスを
ローカルの connect-web(`http://localhost:8788`) に橋渡しする。
受信ポートは一切開かない（cloudflared は外向き接続のみ）。

設定ファイルは `/etc/cloudflared/config.yml`（= `../cloudflared-config.yml` を配置したもの）を読む。

## connect-web 本体は既存ユニットを流用する

connect-web（OAuth コールバック受け :8788）の常駐は **新規に作らない**。
既存の `deploy/systemd/aila-connect.service` をそのまま使う。

- そのユニットは `ExecStart=/opt/aila/scripts/aila.sh connect-web` を `User=aila` / `WorkingDirectory=/opt/aila` で常駐させる。
- 既定では `CONNECT_HOST=127.0.0.1` / `CONNECT_PORT=8788` でローカルにだけ待受ける。
  Plan C では **このまま（127.0.0.1）でよい**。0.0.0.0 にする必要はない
  （cloudflared が同じホストの localhost に繋ぐため）。
- `.env.aila` の `OAUTH_REDIRECT_URI` / `SLACK_OAUTH_REDIRECT_URI` /
  `CONNECT_BASE_URL` を会社ドメインに差し替えてから connect-web を再起動すること
  （値は `../redirect-uris.md` を参照）。

## 導入（Linux 常時起動機の場合）

```bash
# 1) ユニット配置
sudo cp /opt/aila/deploy/systemd/aila-connect.service        /etc/systemd/system/
sudo cp /opt/aila/deploy/cloudflare/systemd/aila-cloudflared.service /etc/systemd/system/

# 2) cloudflared 設定配置（UUID/credentials は RUNBOOK.md ステップ1 で取得）
sudo mkdir -p /etc/cloudflared
sudo cp /opt/aila/deploy/cloudflare/cloudflared-config.yml /etc/cloudflared/config.yml
# 編集: <TUNNEL_UUID> と credentials-file パスを実値に
sudo $EDITOR /etc/cloudflared/config.yml

# 3) 有効化
sudo systemctl daemon-reload
sudo systemctl enable --now aila-connect.service
sudo systemctl enable --now aila-cloudflared.service

# 4) 確認
systemctl status aila-connect aila-cloudflared
curl -sf http://localhost:8788/healthz   # {"ok":true}
```

## macOS（個人 Mac を常時起動機にする暫定運用）の場合

当面のオリジンは個人 Mac / 小型常時起動機を想定。Mac では systemd ではなく launchd で常駐する。
最小構成なら既存 `./scripts/aila.sh up`（caffeinate でスリープ抑止 + connect-web + serve + tunnel を冪等起動）
に倣い、cloudflared だけ **named tunnel + config.yml** で起動するように差し替える:

```bash
# trycloudflare の使い捨て URL ではなく、config.yml の named tunnel を使う:
cloudflared --no-autoupdate --config ~/.cloudflared/config.yml tunnel run
# caffeinate -dimsu と併用してスリープ中の切断を防ぐ（aila.sh up と同じ思想）。
```

恒久運用に移す際は Linux 常時起動機 + systemd へ寄せるのが望ましい（SECURITY.md の「恒久化方針」参照）。
