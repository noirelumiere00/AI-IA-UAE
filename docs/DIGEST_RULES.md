# 朝ダイジェスト 判定・確認・実行フロー（整理）

AiLa の朝メールダイジェストが「**何を出し/何を畳み/どう送るか**」の確定仕様。実装と一致。

## 1. 取得 Fetch
- `gmail_query`: `is:unread newer_than:1d -category:promotions -category:social`（既定・`config/agents/morning_email.yaml`）, `max_threads=50`
- 取得ヘッダ: `From / To / Cc / Subject / Date / List-Unsubscribe / List-Id / Precedence`（本文は snippet＝軽量・privacy）

## 2. 分類 Classify（2段：ヒューリスティック → 自信<0.8 のみ LLM/Haiku）
**カテゴリ（先勝ち順）**:
`FINANCE_LEGAL` → `VIP` → `PRESS_MEDIA`(**社外のみ**) → `CLIENT` → `VENDOR_PARTNER` → `NEWSLETTER`(List-Unsubscribe / **List-Id** / Precedence:list,bulk / noreply) → `INTERNAL`(社内ドメイン) → 既定 `CLIENT_NORMAL`(要確認)
**付帯判定**:
- `is_actionable`（要返信/対応）: **本人名指し(display_name)** は NL/ML でも True に昇格（安全弁）/ 汎用依頼語(ご確認・ご返信…)・緊急語は **NL/ML 以外**で True / 「終了/完了しました」等の完了連絡は False
- `recipient_kind`: `to`(直接宛) / `cc`(情報共有のみ) / `unknown`
- `is_vip`, `amount_jpy`, `key_person`

## 3. 表示ポリシー Display
- **個別表示** = `is_actionable` **or** `is_vip` **or** 重要cat(`CLIENT_URGENT/CLIENT_NORMAL/PRESS_MEDIA/FINANCE_LEGAL`)
- **一般メール（件数のみ畳み）** = 上記以外（NEWSLETTER / ML / 社内FYI / VENDOR / SNS通知 / 完了連絡）
- 個別表示を2グループに分離:
  - **📥 あなた宛（To・要対応）** … `to` ＋ 要返信Cc(昇格) ＋ `unknown`
  - **👥 CC（情報共有・参考）** … 非アクションの `cc` のみ
- ラベル `AIIA/<cat>` は**個別表示分のみ** Gmail に付与（一般メールには付けない＝Gmailもクリーン）
- **Recall 優先**（見落とし ＜ 出し過ぎ）。迷ったら出す。
- **表示順（Block Kit ≤49・予算先取り）**：ヘッダ＋意思決定サマリ → 🔔リマインド → 📥To → 📅今日の予定 → 👥CC → 📭一般(件数) → フッタ。**溢れはカレンダー/CC側から件数化に degrade**（最重要のリマインド/Toを潰さない）。

## 3.5 カレンダー（今日の予定・読み取り専用）
- 本人 `primary` を `events.list(今日JST境界・singleEvents・orderBy=startTime)`。書込(insert/delete/update)は**toolsetに出さない**＝コード規律で読み取り専用。
- 表示：**⏭次の予定ハイライト**＋**終日別出し**／`declined`除外・`needsAction`は「❓未応答」／**≥6件は「次＋直近+ほかN件」に件数fold**（常に1ブロック）。**0件＝「予定なし」と取得失敗を区別**（連携切れを暇と誤認させない）。
- 件名 privacy：`show_titles=false` で「HH:MM 予定あり（件名非表示）」。`redact()` は秘密(鍵/カード)用で機微語は消えない旨を誤認しない。

## 3.6 返信リマインド（重要×未返信×N営業日）
- **未返信判定（誤検知最小化）** = 最新メッセージが ①`SENT`ラベル無し（本人未送信＝From文字列に非依存）②`Auto-Submitted`無し（OOO除外）③`List-Id`/`Precedence:bulk`/`noreply`無し（メルマガ除外）。
- **母集団** = 重要cat × `recipient_kind==to` × `is_actionable`（Cc/FYIは催促しない）。
- **しきい値（営業日）** = CLIENT_URGENT/PRESS=**1**、CLIENT_NORMAL/FINANCE=**3**（土日除外）。
- **追跡** = 朝バッチで `reminder_store`(DynamoDB・PK=email,SK=thread) に記録、毎朝 `get_thread` で**再取得**して判定（日次ウィンドウ非依存）。**返信済み(SENT)/スヌーズ3回/14営業日 で自動解除**（永久催促禁止）。
- **UX** = 最上段「🔔未返信」＋要約＋[✏️対応する/✅対応済み/⏰後で/🔕通知しない]。解除は**論理削除＋↩取り消す**（誤解除復活）。`dry_run` は状態を変えない。

## 4. 下書き Draft
- `draft_categories = [CLIENT_URGENT, PRESS_MEDIA]` かつ既存下書き無のみ
- プレビューは常に生成・Gmail 保存は**非 dry_run 時のみ**・全工程 **Haiku**
- 予算ガード `max_budget_usd` 超過で draft を skip（監査記録）

## 5. 送信 確認フロー（誤送信ゼロ）
```
[📤送信] → ① Slackネイティブ確認(1/2) → ② モーダル再確認(2/2) → drafts.send
[📝編集] → モーダル編集 → drafts.update
[🗑削除] → drafts.delete
```
- 送信専用 `WorkspaceGmailSender` は **MCPToolset の外**（pipeline から到達不可）
- 送信/破壊系は toolset に出さない・**全送信を監査**・redaction 適用・本人にのみ ephemeral 配信

## 6. 実行フロー Run
- **朝バッチ**: `run_for_all_users(dry_run=False)` → 連携済み全員を**並行処理** → 各自の Slack DM。**1人失敗は隔離**して継続。`EventBridge→Lambda` 化可（常駐不要）。
  - CLI: `./scripts/aila.sh batch`
- **対話常駐**: `serve`（Socket Mode）→ 編集/削除/送信ボタン。CLI: `./scripts/aila.sh serve`
- **連携**: `/connect`（or `connect-link <email>`）→ `connect-web`(localhost:8788) → **DynamoDB+KMS** にトークン保存（本人束縛）

## 7. 多人数（40–50人）
- per-user トークン（DynamoDB/KMS・**本人のデータのみ**触れる）/ per-user 予算ガード / ユーザー間で状態共有ゼロ＝**線形スケール**
- オンボーディング: 既存TeamAgent連携者は `migrate_tokens`(RDS→DynamoDB) で再連携不要、新規は `/connect`
