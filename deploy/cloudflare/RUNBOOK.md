# RUNBOOK — connect.vectorinc.co.jp を Cloudflare Tunnel + Access で安全公開する

採用方式 = **Plan C（Cloudflare Tunnel + Cloudflare Access）**。
オリジン（connect-web）は **Shogo 管理の常時起動ホスト**（当面は個人 Mac / 小型常時起動機）に置き、
cloudflared が**外向き接続のみ**でトンネルを張る。会社 AWS には公開 URL を置かない。

上から順に実行する。各ステップの担当を【】で示す。

---

## ステップ0: 前提【Shogo】

- [ ] 常時起動ホストがある（スリープしない。Mac なら `caffeinate -dimsu` / `aila.sh up` でスリープ抑止）。
- [ ] そのホストで connect-web が動く。`./scripts/aila.sh check` が緑、`./scripts/aila.sh connect-web` で
      `curl -sf http://localhost:8788/healthz` が `{"ok":true}` を返す。
- [ ] Cloudflare の無料アカウントを作成済み（Zero Trust も無料枠で可）。
- [ ] `cloudflared` をインストール済み（Mac: `brew install cloudflared` / Linux: 公式 deb/rpm）。
- [ ] DynamoDB トークンテーブル・KMS キー（`AIIA_DDB_TABLE` / `OAUTH_KMS_KEY_ID`）は会社 AWS 側に用意済み
      （トークン保存先。Plan C では変更不要）。

> このホストでは受信ポートを開かない。ルータのポート開放・固定IP・インバウンド許可は**一切不要**。

---

## ステップ1: Tunnel 作成 → cloudflared 常駐【Shogo / 自動】

1. Cloudflare にログインしてトンネルを作る:
   ```bash
   cloudflared tunnel login                     # ブラウザで Cloudflare アカウント認可
   cloudflared tunnel create aila-connect        # → Tunnel UUID と credentials JSON が生成される
   ```
   出力された **UUID** と credentials JSON のパスを控える（秘密情報。コミット禁止）。

2. 設定ファイルを配置（このリポジトリの `deploy/cloudflare/cloudflared-config.yml` を使う）:
   ```bash
   # Linux
   sudo mkdir -p /etc/cloudflared
   sudo cp deploy/cloudflare/cloudflared-config.yml /etc/cloudflared/config.yml
   # <TUNNEL_UUID> と credentials-file を実値に置換
   sudo $EDITOR /etc/cloudflared/config.yml
   # macOS なら ~/.cloudflared/config.yml に置く
   ```

3. 常駐化:
   - **Linux（推奨）**: `aila-cloudflared.service` で常駐（既存 systemd 流儀に準拠）:
     ```bash
     sudo cp deploy/cloudflare/systemd/aila-cloudflared.service /etc/systemd/system/
     sudo cp deploy/systemd/aila-connect.service /etc/systemd/system/   # connect-web 本体は既存ユニット流用
     sudo systemctl daemon-reload
     sudo systemctl enable --now aila-connect.service
     sudo systemctl enable --now aila-cloudflared.service
     ```
   - **macOS 暫定**: `cloudflared --no-autoupdate --config ~/.cloudflared/config.yml tunnel run` を
     `caffeinate -dimsu` と併用して常駐（`aila.sh up` と同じ思想。詳細は `systemd/README.md`）。

> この時点ではまだ DNS が無いので `connect.vectorinc.co.jp` では繋がらない。次のステップで紐づける。

---

## ステップ2: Cloudflare for SaaS（カスタムホスト名）で connect.vectorinc.co.jp を載せる【Shogo / 自動】

`vectorinc.co.jp` ゾーンは Cloudflare 管理外（情シス側 DNS）。**直接トンネルの Public Hostname に
`connect.vectorinc.co.jp` は登録できない**（Public Hostname は自分の Cloudflare ゾーンのみ）。
そこで **Cloudflare for SaaS（カスタムホスト名）** を使う。仕組みは「自分のゾーン（例: `vseoanalytics.com`）に
トンネル用オリジン名を1つ作り、それを for SaaS の **Fallback Origin** に指定 → その上に
`connect.vectorinc.co.jp` をカスタムホスト名として載せる」。証明書は Cloudflare が自動発行する。

**2-1. トンネル用オリジン名を自ゾーンに作り、トンネルへルーティング**（ゾーン名は読み替え）:
```bash
cloudflared tunnel route dns aila-connect connect-origin.vseoanalytics.com
```
→ `vseoanalytics.com` に proxied CNAME が作られ、`connect-origin.vseoanalytics.com` がこのトンネルを指す。
（`cloudflared-config.yml` の ingress には保険として `connect-origin.vseoanalytics.com` 行を追加済み＝あなたのゾーン名に読み替えること。）

**2-2. Fallback Origin を設定**:
ゾーン `vseoanalytics.com` → **SSL/TLS → Custom Hostnames** で Cloudflare for SaaS を有効化し、
**Fallback Origin = `connect-origin.vseoanalytics.com`** を設定。

**2-3. カスタムホスト名を追加**:
同画面 **Add Custom Hostname** に `connect.vectorinc.co.jp` を入力、証明書検証方式 = **TXT（DNS）** を選択。

**2-4. 表示される2つの値を控える（← これが情シスに渡す値）**:
- **CNAME ターゲット**（`connect` を向ける先。`xxxx.cdn.cloudflare.net` 形式 等）
- **検証用 TXT**（ホスト `_xxxx.connect` と 値の文字列）

> CNAME / TXT の**実値はこの 2-4 で初めて確定**する。次のステップで情シスに渡す。
> ※ カスタムホスト名は無料枠内（1件）。UI が課金プランを促す場合があるが、1ホスト名は無料範囲。

---

## ステップ3: 2レコードを情シスへ依頼 → 反映待ち → 証明書 Active 確認【Shogo → 情シス → 自動】

1. 【Shogo】`deploy/cloudflare/it-dns-request.md` を開き、ステップ2で控えた
   **CNAME ターゲット**と**検証用 TXT**の実値を差し込む（〔　〕を埋める）。
2. 【Shogo】その文面を情シスへ送付。お願いするのは **2レコードの追加のみ**。
3. 【情シス】`connect  CNAME  <ターゲット>` と `_xxxx.connect  TXT  <検証文字列>` を追加。
4. 【自動 / Shogo】反映を待つ（数分〜数十分）。Cloudflare の Custom Hostnames 画面で
   ステータスが **Active**（証明書発行済）になるのを確認。
   ```bash
   curl -sI https://connect.vectorinc.co.jp/healthz   # 証明書が載れば TLS エラーなく応答
   ```

---

## ステップ4: Cloudflare Access ポリシー適用【Shogo / 自動】

`deploy/cloudflare/cloudflare-access-policy.md` の手順で、`connect.vectorinc.co.jp` を Access で保護する。

- [ ] Self-hosted アプリ `AiLa connect`（domain=`connect.vectorinc.co.jp`）を追加。
- [ ] ポリシー `Allow vectorinc staff` = Action **Allow** / Include **Emails ending in `@vectorinc.co.jp`**。
- [ ] Login method = One-time PIN（または会社 Google SSO）。
- [ ] 確認: 別アカウント / 未ログインで `https://connect.vectorinc.co.jp/healthz` を開くと Access のログイン画面で止まる。

---

## ステップ5: Google / Slack に Redirect URI 登録 → env 差し替え → connect-web 再起動【Shogo】

`deploy/cloudflare/redirect-uris.md` の値を使う。

1. 【Google】OAuth クライアント（**Internal** タイプ）の「承認済みリダイレクト URI」に
   `https://connect.vectorinc.co.jp/oauth2/callback` を追加（Internal なので審査不要）。
2. 【Slack】（使う場合）App の Redirect URLs に
   `https://connect.vectorinc.co.jp/oauth2/slack/callback` を追加。
3. 【env】`.env.aila` を差し替え:
   ```sh
   OAUTH_REDIRECT_URI=https://connect.vectorinc.co.jp/oauth2/callback
   CONNECT_BASE_URL=https://connect.vectorinc.co.jp
   SLACK_OAUTH_REDIRECT_URI=https://connect.vectorinc.co.jp/oauth2/slack/callback   # Slack 使用時
   ```
4. 【再起動】`./scripts/aila.sh check` が緑なのを確認し、connect-web を再起動:
   - Linux: `sudo systemctl restart aila-connect`
   - Mac: `./scripts/aila.sh down && ./scripts/aila.sh up`（または connect-web プロセスのみ再起動）

---

## ステップ6: 自分のアカウントで連携 E2E【Shogo】

1. connect リンクを生成: `./scripts/aila.sh connect-url`（Google 共通 1 リンク） /
   必要なら `./scripts/aila.sh connect-url-slack`（Slack 用）。
2. ブラウザでそのリンクを開く →
   - [ ] **Access** のログイン（`@vectorinc.co.jp` で通過）
   - [ ] **Google** 同意画面 → 許可 → `/oauth2/callback` が「連携できました」を表示
   - [ ] （Slack 使用時）**Slack** 同意 → `/oauth2/slack/callback` が成功表示
3. トークン保存を確認: `./scripts/aila.sh smoke`（DynamoDB+KMS から連携済みメール一覧が出る ＝ 自分が載っていればOK）。

---

## ステップ7: 10名へ connect リンク配布 → 連携率確認【Shogo】

1. ステップ6で確定した connect リンクを対象 10 名へ配布（Slack 等）。
   各自はリンクを開くと Access → Google（→ Slack）同意の順で 1〜2 分で完了。
2. 連携率を確認: `./scripts/aila.sh smoke` の一覧件数（または `list_emails`）で連携済み人数を数える。
3. 全員緑になったら、朝バッチ（`aila-batch.timer`）/ serve（`aila-serve.service`）が
   連携済み全員へ配信を始める。

---

## 切り戻し（必要時）
- env を `redirect-uris.md` のローカル検証用に戻し connect-web 再起動 → 会社ドメイン経路を一旦止める。
- `sudo systemctl stop aila-cloudflared`（または cloudflared プロセス停止）でトンネルを落とすと公開が止まる。
  受信ポートを開いていないので、止めれば外部到達は完全にゼロに戻る。
