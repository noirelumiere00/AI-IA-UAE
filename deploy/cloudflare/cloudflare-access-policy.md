# Cloudflare Access ポリシー — connect.vectorinc.co.jp の保護

## なぜ Access をかけるのか（1段落）

connect-web は「各社員が初めて AiLa に自分の Google / Slack を連携する入口」です。
この入口は OAuth の仕様上、本人確認自体は Google / Slack の同意画面が担います。
しかし **同意画面に辿り着く前**に、会社の人間だけがこのページを開ける状態にしておけば、
外部の第三者がこの URL を踏むこと自体を入口で遮断できます。
つまり「連携の入口に、Google/Slack の本人確認とは別に、もう一段の会社本人確認（@vectorinc.co.jp のみ）」を
重ねることで、フィッシング誘導・無差別スキャン・部外者の到達をゼロトラストで止めます（＝二重の本人確認）。

---

## ポリシー定義（確定値）

| 項目 | 値 |
| --- | --- |
| 保護対象アプリ名 | `AiLa connect` |
| アプリタイプ | Self-hosted |
| Application domain | `connect.vectorinc.co.jp`（path は空＝全パス保護） |
| Session Duration | 24h（連携作業の間だけ有効。長期セッション不要） |
| ポリシー名 | `Allow vectorinc staff` |
| Action | **Allow** |
| Include ルール | Emails ending in `@vectorinc.co.jp` |
| 認証方式（Login methods） | 第一候補=会社 Google SSO（Google Workspace）／併用可=One-time PIN |

### 認証方式の選び方
- **会社 Google SSO**（推奨）: 会社 Workspace を IdP として登録すれば、社員は普段の Google ログインで通過でき、UX が滑らか。
- **One-time PIN**（最小構成）: IdP 連携なしでも、`@vectorinc.co.jp` 宛にワンタイム PIN をメールし、入力できた人だけ通す。
  IdP 設定を待たずに即運用できるので、パイロット初期はこちらでもよい。
- どちらでも Include ルール（`@vectorinc.co.jp`）が効くので、到達できるのは会社アドレス保有者のみ。

> 注意: ここで保護するのは「connect-web のページに到達できる人」です。
> 連携で実際に保存される Google/Slack トークンは、各社員が Google/Slack の同意画面で
> 自分のアカウントを認可した本人のものになります（Access はあくまで入口ガード）。

---

## 設定手順（Cloudflare Zero Trust ダッシュボード）

1. Zero Trust ダッシュボード → **Access → Applications → Add an application** → **Self-hosted**。
2. Application Configuration:
   - Application name: `AiLa connect`
   - Session Duration: `24 hours`
   - Application domain: `connect.vectorinc.co.jp`（Subdomain=`connect` / Domain=`vectorinc.co.jp` / Path=空）
     - ※ `vectorinc.co.jp` を Cloudflare for SaaS のカスタムホスト名として先に追加しておくこと（RUNBOOK ステップ2）。
3. **Add a policy**:
   - Policy name: `Allow vectorinc staff`
   - Action: **Allow**
   - Configure rules → Include → Selector=**Emails ending in** → Value=`@vectorinc.co.jp`
4. **Identity providers / Login methods**:
   - One-time PIN を有効化（既定で利用可）。
   - 会社 Google Workspace を IdP 登録できる場合は Google を追加し、こちらを優先。
5. Save。以降 `https://connect.vectorinc.co.jp/...` への全アクセスに Access のログイン画面が前段で挟まる。

### 通過しなかった場合の挙動
- `@vectorinc.co.jp` 以外のアドレスでログインした場合や未ログインの場合、connect-web には一切到達せず Access のブロック画面で止まる。
- そのため Google/Slack の同意画面・`/oauth2/callback` 等は会社の人間にしか見えない。

---

## （参考）コード / API での同等表現

ダッシュボード設定が一次手段ですが、IaC 化したい場合の同等表現を併記します（実行は任意）。

### Terraform（cloudflare provider）

```hcl
# 実値（account_id / zone）は秘密ではないが、トークンは環境変数で渡すこと。
resource "cloudflare_access_application" "aila_connect" {
  account_id       = var.cf_account_id
  name             = "AiLa connect"
  domain           = "connect.vectorinc.co.jp"
  type             = "self_hosted"
  session_duration = "24h"
}

resource "cloudflare_access_policy" "allow_vectorinc" {
  application_id = cloudflare_access_application.aila_connect.id
  account_id     = var.cf_account_id
  name           = "Allow vectorinc staff"
  precedence     = 1
  decision       = "allow"

  include {
    email_domain = ["vectorinc.co.jp"]
  }
}
```

### cloudflared 側の補足
`cloudflared` 自身は Access ポリシーを「作成」しません（トンネルのデータプレーン担当）。
Access のポリシー作成は上記ダッシュボード or Terraform / API（`/accounts/{account_id}/access/apps`）で行います。
cloudflared 側で追加できるのは、必要なら **オリジン側でも Access JWT を検証する**多層化です
（connect-web の前段にさらに JWT 検証を挟む構成。パイロットでは必須ではない）。
