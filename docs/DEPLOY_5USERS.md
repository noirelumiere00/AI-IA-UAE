# AiLa 5名配布 runbook（公開化 → 常駐 → 朝バッチ → 5名連携）

ゴール：localhost依存を脱し、**5名が各自のメールで毎朝サマリーを受け取り、[対応する]1クリックで動く**状態。
凡例：🧑‍💼=あなた（AWS/GCP/Slack操作）／🤖=用意済みスクリプト/資材（実行するだけ）。

---

## 全体像（公開が要るのは1つだけ）
| 要素 | 公開要否 | 置き場 |
|---|---|---|
| **connect-web**（`/oauth2/callback`＋`/reply`） | 🌐 **公開HTTPS必須**（ブラウザが叩く） | トンネル or EC2 |
| **serve**（Slackボタン応答・Socket Mode） | 不要（アウトバウンド接続） | 常駐できればどこでも |
| **batch**（朝の配信） | 不要 | 定期実行できればどこでも |

→ **公開connect-webの立て方**で2案。まず動かすなら **A（トンネル）**、本番堅牢は **B（EC2）**。

---

## STEP 0: 事前確認（🤖 すぐ）
```bash
cd ~/Documents/AI-IA-UAE && source .env.aila && ./scripts/aila.sh check
```
全部 ✓ ならOK。`OAUTH_REDIRECT_URI` `CONNECT_BASE_URL` も検査対象（公開URLにしたら再check）。

---

## STEP 1: connect-web を公開する（🧑‍💼）

### 案A：cloudflared トンネル（最速・まず動かす）
Macやサーバーの connect-web を、固定の公開URLに通す。
```bash
# 1) cloudflared 導入（Mac）
brew install cloudflared
# 2) connect-web を起動（別ターミナル・常駐）
cd ~/Documents/AI-IA-UAE && source .env.aila && ./scripts/aila.sh connect-web   # 127.0.0.1:8788
# 3) トンネルを張る（無料の試用URL。表示された https://xxxx.trycloudflare.com を控える）
cloudflared tunnel --url http://localhost:8788
#    本番運用は「名前付きトンネル」で固定ドメイン推奨（cloudflared tunnel login → create → route dns）
```
→ 公開URL（例 `https://xxxx.trycloudflare.com`）が手に入る。**ホスト(Mac)が起きている間だけ有効**。

### 案B：小型EC2＋Caddy（堅牢・Mac非依存・本番）
```bash
# EC2(Ubuntu, t3.small程度) を用意し、22/80/443 を開放、ドメインのA/AAAAを向ける。
# サーバーで：
sudo useradd -m -d /opt/aila aila            # 専用ユーザー
sudo git clone <repo> /opt/aila && cd /opt/aila
sudo -u aila python3 -m venv .venv && sudo -u aila .venv/bin/pip install -e '.[multiuser,bedrock]'
sudo -u aila cp <あなたの.env.aila> /opt/aila/.env.aila && sudo chmod 600 /opt/aila/.env.aila
# Caddy（自動HTTPS）
sudo apt install -y caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # <DOMAIN> を置換
sudo systemctl reload caddy
# 常駐（systemd）
sudo cp deploy/systemd/aila-*.service deploy/systemd/aila-batch.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aila-connect aila-serve aila-batch.timer
```
→ 公開URL = `https://<DOMAIN>`（Caddyが自動でHTTPS）。serve常駐＋朝バッチ(平日9:30 JST)も同時に有効。

---

## STEP 2: 公開URLを env と GCP に反映（🧑‍💼）
```bash
# .env.aila（127.0.0.1:8788 → 公開URLに）
#   OAUTH_REDIRECT_URI=https://<公開URL>/oauth2/callback
#   CONNECT_BASE_URL=https://<公開URL>
source .env.aila && ./scripts/aila.sh check     # https で ✓ になるか
```
- **GCP（pgd1 OAuthクライアント）**：APIs&Services → 認証情報 → pgd1 → 「承認済みのリダイレクトURI」に
  `https://<公開URL>/oauth2/callback` を**追加**して保存（既存の localhost は残してOK）。

---

## STEP 3: 5名を連携する（🧑‍💼＋🤖）
### 既にTeamAgentで連携済み → 移行（/connect不要・一瞬）
```bash
export DATABASE_URL='postgresql://...teamagent...'   # SSM踏み台等でRDS到達
./scripts/aila.sh migrate --dry-run                  # 誰が来るか件数だけ
./scripts/aila.sh migrate                            # 本移行（RDS→DynamoDB）
./scripts/aila.sh smoke                              # → 連携済みに5名出ればOK
```
### 新規（未連携） → 各自 /connect（1クリックAllow）
```bash
./scripts/aila.sh connect-link a@vectorinc.co.jp b@... ...   # email<TAB>URL を発行
#  各人にURLを送る or Slackで /connect → 出たURLで「許可」→ 「✅連携完了」
./scripts/aila.sh smoke                                       # 5名出現を確認
```

---

## STEP 4: 配信を動かす（🤖）
- **EC2(案B)** なら `aila-serve`(常駐) と `aila-batch.timer`(平日9:30) が既に動く。初回は手動で1回：
  ```bash
  sudo systemctl start aila-batch     # いま一度配信して確認
  journalctl -u aila-batch -n 50      # 結果ログ
  ```
- **トンネル(案A)** なら Mac で：
  ```bash
  ./scripts/aila.sh batch             # 朝サマリーを5名に配信（初回）
  ./scripts/aila.sh serve             # ボタン応答常駐（別ターミナル）
  #  定期化は Mac の launchd/cron で 9:30 に `aila.sh batch`（任意）
  ```

---

## STEP 5: 検証（🧑‍💼）
1. 5名の Slack DM に「📬 メールサマリー」が届く
2. 🔔未返信の **[対応する]** を押す → ブラウザが開き、Gmailの該当スレッドに **本文＋署名入りの全返信下書き**がインラインで開く（1クリック）
3. **[対応済み][後で]** が反応（serve常駐の確認）

---

## チェックリスト
- [ ] connect-web 公開（A or B）→ `https://.../healthz` が `{"ok":true}`
- [ ] OAUTH_REDIRECT_URI / CONNECT_BASE_URL を公開URLに（check ✓）
- [ ] GCP pgd1 に公開 redirect_uri 追加
- [ ] 5名 connected（smoke で5名）
- [ ] serve 常駐 ／ batch（初回配信OK・定期化）
- [ ] [対応する]1クリック動作 ／ [対応済み]反応

## 補足
- **安全**：AWS secret読み書き・SSM・EC2構築・GCP変更は🧑‍💼。私(🤖)はスクリプト/systemd/Caddy/runbookまで。
- **トンネル→EC2移行**：案Aで体験→定着したら案Bへ。差し替えは `OAUTH_REDIRECT_URI`/`CONNECT_BASE_URL` と GCP redirect_uri の3点だけ。
- **per-user**：全員それぞれ自分のメールだけ。他人のは構造的に見えない（KMS EncryptionContext束縛）。
