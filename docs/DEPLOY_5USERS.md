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
cloudflared tunnel --url http://localhost:8788 --protocol http2
#    ⚠️ 会社NW/プロキシは QUIC(UDP) を塞ぐことが多い → 必ず `--protocol http2`（TCP443）。
#       （QUICのままだと "Failed to dial quic ... timeout" で 530 になる。実測で確認済）
#    本番運用は「名前付きトンネル」で固定ドメイン推奨（cloudflared tunnel login → create → route dns）
#    ※ trycloudflare の試用URLは再起動で変わる＝固定化するなら named tunnel か案B(EC2)へ。
```
→ 公開URL（例 `https://xxxx.trycloudflare.com`）が手に入る。**ホスト(Mac)が起きている間だけ有効**。

### 案B：小型EC2＋Caddy（堅牢・Mac非依存・本番）← **deploy.sh でほぼ1コマンド**
あなたがやるのは **4つだけ**（残りは `deploy/deploy.sh` が自動）:
1. Ubuntu 22.04+ の小型EC2(t3.small程度)を起動し、SGで **22/80/443** を開放。
2. ドメイン（例 `connect-aila.vectorinc.co.jp`）の **A/AAAA を EC2 のIP** に向ける。
3. このリポジトリ一式を EC2 に置く（`git clone` / `rsync` / `scp` いずれか）。
4. 本物の **`.env.aila`（perm600）をリポジトリ直下** に置く（秘密・git禁止）。

その後サーバーで1回:
```bash
sudo bash deploy/deploy.sh connect-aila.vectorinc.co.jp
#   → aila専用ユーザー作成 / /opt/aila配置 / venv+依存 / Caddy(自動HTTPS) /
#     systemd で connect-web・serve 常駐 + 朝バッチ(平日9:30 JST) 有効化（冪等）。
```
→ 公開URL = `https://<DOMAIN>`（Caddyが自動でHTTPS）。serve常駐＋朝バッチも同時に有効。
完了後にスクリプトが「残りの3手（env本番URL化 / GCP redirect_uri追加 / 共通リンク発行）」を表示する。

---

## STEP 2: 公開URLを env と GCP に反映（🧑‍💼）
```bash
# .env.aila（127.0.0.1:8788 → 公開URLに）
#   OAUTH_REDIRECT_URI=https://<公開URL>/oauth2/callback
#   CONNECT_BASE_URL=https://<公開URL>
#   AIIA_CONNECT_ALLOWED_DOMAINS=vectorinc.co.jp   # 共通リンクの連携を社用ドメインに限定（推奨）
#     ※複数なら "vectorinc.co.jp jock.co.jp" のように空白/カンマ区切り。
#     未設定でも AIIA_DEFAULT_INTERNAL_DOMAIN(=vectorinc.co.jp) に自動で絞られる。
source .env.aila && ./scripts/aila.sh check     # https で ✓ になるか
```
- **GCP（pgd1 OAuthクライアント）**：APIs&Services → 認証情報 → pgd1 → 「承認済みのリダイレクトURI」に
  `https://<公開URL>/oauth2/callback` を**追加**して保存（既存の localhost は残してOK）。
  ※ **共通1リンク連携(STEP3 案C)を使うなら、この登録が必須**（未登録だと redirect_uri_mismatch）。

---

## STEP 3: 5名を連携する（🧑‍💼＋🤖）― 3案。**おすすめは案C（共通1リンク）**
### 案C：共通1リンクをSlackに貼る（全員タップで連携・本人はGoogleで確定）★推奨
1本のURLを発行し、Slackのチームチャンネル/DMに貼るだけ。各人がタップ→自分のGoogleで「許可」→完了。
誰が踏んでも**自分のメール専用**に連携される（本人は Google ログイン結果=id_token で確定）。
```bash
source .env.aila && ./scripts/aila.sh connect-url   # 共通1リンク(URL1行)を出力
#  → このURLをSlackに貼る。各人タップ→「許可」→「✅ 連携が完了しました」。
./scripts/aila.sh smoke                             # 連携済みに人数が増えるのを確認
```
- **安全**：`AIIA_CONNECT_ALLOWED_DOMAINS`（or `AIIA_DEFAULT_INTERNAL_DOMAIN`）で**社用ドメイン以外は自動で弾く**。
- **前提**：STEP2 の GCP redirect_uri 登録が必須（公開URLの `/oauth2/callback`）。
- URLは公開URL（redirect_uri）に紐づく＝**公開先を変えたら再発行**。

### 案A：既にTeamAgentで連携済み → 移行（誰のクリックも不要・一瞬）
```bash
export DATABASE_URL='postgresql://...teamagent...'   # SSM踏み台等でRDS到達
./scripts/aila.sh migrate --dry-run                  # 誰が来るか件数だけ（書込なし）
./scripts/aila.sh migrate                            # 本移行（RDS→DynamoDB）
./scripts/aila.sh smoke                              # → 連携済みに5名出ればOK
```
### 案B：個別リンク（本人専用URLを1人ずつ配る）
```bash
./scripts/aila.sh connect-link a@vectorinc.co.jp b@... ...   # email<TAB>URL を発行
./scripts/aila.sh smoke                                       # 人数を確認
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
