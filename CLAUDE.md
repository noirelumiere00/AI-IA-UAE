# CLAUDE.md — AI-IA-UAE プロジェクト指示

## プロジェクト概要
広告PR会社(NewsTV)向けのマルチエージェントAI基盤。第1弾は「朝のメール確認サポートAgent」。
最終形は Slackネイティブの営業AIアシスタント（モードA=対話＋RAG＋資料生成 / モードB=朝メール定時処理）。
設計図の正典: `/root/.claude/plans/shimmying-foraging-ullman.md`（要件定義 §0–49）。

## 開発の鉄則
- **安全第一**: 誤送信ゼロ。メール送信・Slack実送信は自動化しない（**下書きのみ**）。送信は常に人間が最終実行。
- 機密データはBedrock(VPC/IAM)経由。秘密情報(キー/トークン)は**コミットしない**。
- Phase1は**全同期・YAGNI**（§49）。runtime/storeは作らない。
- ブランチ: `claude/vibrant-galileo-lXVG6`。モデル識別子は成果物に含めない。

## 学習モード
このプロジェクトのオーナーは非エンジニア。`~/.claude/CLAUDE.md` の「学習モード」を必ず守り、
教えながら1歩ずつ・理解チェックリスト更新・小テスト(AskUserQuestion)・日本語 で進める。

## 現在の到達点
- 実装済(src/aiia): schemas / triage / config / llm(HeuristicLLM) / safety(hitl,redaction,audit)
- AWS Bedrock 接続実証済（`us.anthropic.claude-…` 推論プロファイル / 最小権限IAM）
- 次: §49の確定修正に沿って mcp(Protocol/Fake) から段階実装
