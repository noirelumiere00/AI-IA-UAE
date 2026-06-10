# AiLa 配布・登録 手順書（管理者向け）

> 🚀 **5名に配布する具体手順（公開化→常駐→朝バッチ→連携→検証）は [`DEPLOY_5USERS.md`](DEPLOY_5USERS.md) に runbook 化済み。** 本書は連携の考え方・トラブル対処の補足。

営業に AiLa を配る＝**各人が自分のGoogleを一度連携する**だけ。まず **上位レイヤー5名で先行**、問題なければ拡大、が安全。

## 0. 前提（一度だけ・構築済み）
- Slack アプリ **AiLa**（Socket Mode・`/connect`・Bot Token）→ `.env.aila` に設定済。
- AWS（ap-northeast-1）：DynamoDB `aiia-oauth-tokens` / `aiia-reminder-state`、KMS、Bedrock（Haiku）→ 構築済。
- Google OAuth：**pgd1（TeamAgent Connect・web型）クライアントを流用**。
- ローカル：`cd ~/Documents/AI-IA-UAE && uv pip install -e '.[multiuser,bedrock]'`、`source .env.aila`、`./scripts/aila.sh check` が ✅。

## 1. 5名の登録（2通り・どちらか）

### A. 既にTeamAgentでGoogle連携済みの人 → **トークン移行（再連携ゼロ・推奨）**
pgd1クライアント流用なので、TeamAgentのトークンをそのまま使えます。
```bash
source .env.aila
export DATABASE_URL='postgresql://...@teamagent-dev...:5432/teamagent?sslmode=require'  # SSM踏み台等でRDS到達
./scripts/aila.sh migrate --dry-run   # 誰が移行対象か件数だけ確認（書込なし）
./scripts/aila.sh migrate             # 本移行 → DynamoDBへ
./scripts/aila.sh smoke               # 連携済みメール一覧に5名が出れば完了
```

### B. 新規連携 → 各自 `/connect`
各人がSlackで `/connect` → 出たURLでGoogle許可。
- **前提**：OAuthコールバック受け **connect-web を“公開URL”で常駐**させる必要があります（`localhost:8788` は構築者のMac限定で、他端末からは不可）。
  - 小規模なら一時的に：構築者が `./scripts/aila.sh connect-web` を立て、対象者を**画面共有/その場**で連携。
  - 本番は **小型の公開エンドポイント（Fargate or Lambda+API Gateway）** に connect-web を置き、その `https://.../oauth2/callback` を **GCP pgd1 のリダイレクトURIに追加** → 各自どこからでも `/connect` 可能。
- 管理者が一括でリンクを配る場合：`./scripts/aila.sh connect-link <email1> <email2> …`（生成したURLを本人に送る）。

> **おすすめ**：5名がTeamAgent連携済みなら **A（移行）が一瞬**。未済が混ざるなら **B（公開connect-web）** をタスクとして用意。

## 2. 連携確認
```bash
source .env.aila && ./scripts/aila.sh smoke   # → 連携済み: ['a@vectorinc.co.jp', ...]
```

## 3. 起動・配信
```bash
./scripts/aila.sh batch    # 連携済み全員へ「📬 メールサマリー」をSlack DM配信（朝・1回）
./scripts/aila.sh serve    # 対話常駐（ボタン: 対応する/対応済み/後で/編集/削除/送信2段）
```
- **毎朝自動化**：`batch` を **EventBridge→Lambda**（例 平日 7:30 JST）でスケジュール＝常駐不要。
- `serve` はボタン操作のため常駐（当面 Mac／本番は小型EC2 or Fargate）。
- リマインドは**毎朝 batch が回るほど精度が上がる**（追跡が貯まる）ので、自動化を推奨。

## 4. 使い方の周知
営業には **`docs/USER_GUIDE.md`（メールサマリー使い方）** を共有。要点：
- 一度だけ `/connect`、朝のサマリーの読み方、未返信の[対応する]、**送信は必ず2段確認＝誤送信ゼロ**、見えるのは自分のメールだけ。

## 5. 拡大
5名で1〜2週間運用 → 問題なければ同手順で全営業へ（A/B同じ）。設定は per-user 独立なので**人数に比例して増やすだけ**。

## トラブル時
- `check` が ✗ → `.env.aila` の該当値を確認。
- `batch` で `delivered=False` の人 → Slackメール解決失敗（users:read.email）or 未連携。
- `migrate` が `DATABASE_URL未設定` → RDS到達（SSM踏み台）を用意。
