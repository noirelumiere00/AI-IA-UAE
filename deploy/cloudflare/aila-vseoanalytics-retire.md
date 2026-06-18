# AiLa を connect.vectorinc.co.jp へ統合し、vseoanalytics.com を廃止する

## 方針
連携URLを会社ドメイン `connect.vectorinc.co.jp` に一本化する（Plan C）。
これに伴い、現在 `aila.vseoanalytics.com` で動いている **朝メールAiLaの連携（OAuthコールバック・/reply リンク）も
`connect.vectorinc.co.jp` に巻き取り**、`vseoanalytics.com` は廃止（自動更新OFF→失効）する。

connect-web は同一アプリ（`localhost:8788`）なので、**URLを差し替えるだけ**で
「朝メールAiLa」と「社員の初回連携」が同じ会社ドメインに乗る（裏ゾーンの安いドメインは社員非表示のまま）。

## 現状（vseoanalytics.com 依存箇所）
| 箇所 | 現在の値 |
|---|---|
| `~/.cloudflared/config.yml` | tunnel `aila`(be4e04e8) → `aila.vseoanalytics.com` → localhost:8788 |
| `.env.aila` OAUTH_REDIRECT_URI | `https://aila.vseoanalytics.com/oauth2/callback` |
| `.env.aila` CONNECT_BASE_URL | `https://aila.vseoanalytics.com` |
| `.env.aila` SLACK_OAUTH_REDIRECT_URI | `https://aila.vseoanalytics.com/oauth2/slack/callback` |
| Google OAuth クライアント | 承認済みリダイレクトURIに `aila.vseoanalytics.com/oauth2/callback` |
| (Slack使用時) Slack app | Redirect URLs に `aila.vseoanalytics.com/oauth2/slack/callback` |

## ★ 実施順序（先に connect.vectorinc.co.jp を完成させてから）
重要: **先に `connect.vectorinc.co.jp` を Active（証明書発行・疎通OK）にしてから切り替える。**
先に vseoanalytics を止めると朝メールAiLaが停止する。

### ステップA — cutover（connect.vectorinc.co.jp ライブ後）
1. `.env.aila` の3つのURLを差し替え:
   ```sh
   OAUTH_REDIRECT_URI=https://connect.vectorinc.co.jp/oauth2/callback
   CONNECT_BASE_URL=https://connect.vectorinc.co.jp
   SLACK_OAUTH_REDIRECT_URI=https://connect.vectorinc.co.jp/oauth2/slack/callback
   ```
2. Google OAuth クライアントに `https://connect.vectorinc.co.jp/oauth2/callback` を追加（Internal=審査不要）。
   ※ 社員連携(RUNBOOK ステップ5)で既に追加済みなら重複不要。
3. (Slack使用時) Slack app に `https://connect.vectorinc.co.jp/oauth2/slack/callback` を追加。
4. `./scripts/aila.sh check` が緑を確認 → connect-web 再起動（`aila.sh down && aila.sh up`）。
5. 動作確認: 朝メールの reply リンク／connect リンクが `connect.vectorinc.co.jp` になり、OAuth往復が通る。

### ステップB — vseoanalytics.com 廃止
1. 旧 `aila` トンネル停止: `aila.vseoanalytics.com` 用の cloudflared 常駐を止める。
   接続0を確認後に削除してよい: `cloudflared tunnel delete aila`（UUID be4e04e8）。
2. 旧 `~/.cloudflared/config.yml`（aila.vseoanalytics.com 用）は廃止。運用は `aila-connect-config.yml` に一本化。
3. Google／Slack の **旧 `aila.vseoanalytics.com` リダイレクトURIを削除**（connect.vectorinc.co.jp 確認後）。
4. `vseoanalytics.com` の**自動更新をOFF**にして失効させる（更新しない＝ドメイン費ゼロ）。
5. （任意）既に配信済みDMの旧 reply リンクは順次失効。新規配信は connect.vectorinc.co.jp。

## 完了後の姿
- 朝メールAiLa ＋ 社員の初回連携 が **`connect.vectorinc.co.jp` に一本化**。
- 表に出るのは会社ドメインのみ。裏ゾーンは新しい安いドメイン1個だけ（社員非表示）。
- `vseoanalytics.com` 廃止でそのドメイン費はゼロ。

> このステップA/Bは私（エージェント）が cutover 時に実行を主導します（connect.vectorinc.co.jp が Active になってから）。
> 関連: 立ち上げ手順は [RUNBOOK.md](RUNBOOK.md)、情シス向けは [it-heads-up.md](it-heads-up.md) → [it-dns-request.md](it-dns-request.md)。
