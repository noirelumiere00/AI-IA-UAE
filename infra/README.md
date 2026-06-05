# infra — AWS / Bedrock セットアップ手順（小俣さん側の作業）

> 私（AI）が代行できない「AWSアカウント操作」と「環境設定へのキー登録」の2点をまとめた手順書です。
> アプリ本体は AWS 無しでも `AIIA_PROFILE=heuristic` で動きます。本番Bedrockにする時だけ以下を実施してください。

## 0. 方針（推奨）
- **レガシーGA Bedrock / us-east-1**。アカウントIDをどこかに送る必要はありません（自社アカウント内で完結）。

## 1. Bedrockでモデル有効化
1. AWSコンソール → **Amazon Bedrock** → リージョンを **us-east-1** に。
2. 左メニュー **Model access** → **Anthropic Claude**（Opus / Sonnet / Haiku）を **Request/Enable**。
3. 有効化後、利用可能なモデルID（`anthropic.claude-…`）を控える。

## 2. IAM（最小権限）
1. **IAM** → ポリシー作成 → JSON に [`iam-bedrock-policy.json`](./iam-bedrock-policy.json) を貼り付け。
2. 推奨: **IAMロール＋短期STS**。簡易には IAMユーザーを作成しこのポリシーをアタッチ → アクセスキー発行。
   - ⚠ 長期キーは最終手段。本番は Lambda/ECS の実行ロール（キーレス）を推奨。

## 3. このセッション（画面）の環境設定に登録 ※キーはチャットに貼らない
Claude Code on the web の **環境設定**で以下を登録（参照: https://code.claude.com/docs/en/claude-code-on-the-web ）:
- 環境変数（シークレット）:
  - `AIIA_PROFILE=bedrock`
  - `AWS_REGION=us-east-1`
  - `AWS_ACCESS_KEY_ID=...` / `AWS_SECRET_ACCESS_KEY=...`（短期なら `AWS_SESSION_TOKEN` も）
- **ネットワークポリシー**: `bedrock-runtime.us-east-1.amazonaws.com`（および `bedrock.us-east-1.amazonaws.com`）への送信を許可。
- setupスクリプト（任意）: `pip install 'anthropic[bedrock]'`

## 4. 疎通確認（私が実施）
登録後に教えてください。私が次を実行します:
- 認証確認（`sts get-caller-identity` 相当）
- Bedrockへ1トークンのテスト推論
- `AIIA_PROFILE=bedrock` で `--dry-run` を実行し、Bedrock経由の分類/要約/下書きを確認。

## 5. （任意）Google Workspace 連携（20名・DWD）
- 管理者がGCPサービスアカウントに**ドメイン全体委任**を付与し、スコープ `gmail.readonly` `gmail.compose` `gmail.labels` `calendar.readonly` を許可。
- SA鍵は Secrets Manager/環境シークレットへ。`gmail.send` は付与しない（誤送信防止）。

## 補足: 新「Amazon Bedrock の Claude」(Mantleプレビュー) を使う場合のみ
- 研究プレビュー・us-east-1・**専用AWSアカウント要**。AWSアカウントIDは **Anthropicの担当窓口(AE)** へ提出してホワイトリスト登録（※私宛ではありません）。
