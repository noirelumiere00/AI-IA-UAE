# SECURITY — connect.vectorinc.co.jp（AiLa 初回連携の入口）のセキュリティ設計

対象読者: 情シス / 経営にも提示できる説明。社員の Google Workspace と Slack を各自が自分で認可する
「初回連携の入口」(connect-web) を、会社ドメイン `connect.vectorinc.co.jp` で安全に公開するための設計。
採用方式 = **Plan C（Cloudflare Tunnel + Cloudflare Access）**。

---

## 1. なぜこの構成が堅いか（4点）

### ① 受信ポート 0（cloudflared 外向きのみ）
オリジン（connect-web を動かすホスト）は **受信ポートを一切開きません**。
cloudflared が Cloudflare のエッジへ**外向きに接続**してトンネルを張り、
利用者のアクセスはエッジ → トンネル → localhost:8788 と内側へ流れます。
ホストへの直接の着信経路が存在しないため、ポートスキャン・直接攻撃の対象になりません
（ルータのポート開放・固定 IP・インバウンド許可も不要）。

### ② Access による本人確認（ゼロトラスト）
`connect.vectorinc.co.jp` への到達には **Cloudflare Access** を前段に置き、
**`@vectorinc.co.jp` のメールを持つ社員だけ**を通します（One-time PIN または会社 Google SSO）。
会社の人間以外は connect-web のページ自体に到達できません。
連携の本人確認（Google/Slack の同意）に加えて、入口でもう一段の会社本人確認をかける **二重の本人確認**です。

### ③ 会社ドメイン表示（反フィッシング）
利用者が見る URL は会社の `connect.vectorinc.co.jp`、証明書も同ドメインの正規証明書です。
「見慣れない URL で Google ログインを求められる」状態を作らないため、
偽サイトへ誘導するフィッシングと区別しやすく、利用者が安心して連携できます。

### ④ WAF / DDoS（Cloudflare エッジ）
すべてのアクセスは Cloudflare エッジを通過するため、Cloudflare の WAF / DDoS 緩和・
レート制限・Bot 対策の保護下に入ります。オリジンが直接攻撃トラフィックを浴びることはありません。

> connect-web 自体も多層防御を実装済み（出典 `src/aiia/connect_web/app.py`）:
> CSP `default-src 'none'`、`X-Content-Type-Options: nosniff`、出口での `html.escape`（反射型XSS対策）、
> OAuth state の HMAC 署名（CSRF 対策）、`/reply` の署名トークン検証。

---

## 2. 残リスク（正直な記載）

完璧ではない点を明示します。隠さず時限運用で管理します。

- **連携の瞬間、各社員の OAuth トークンがこのオリジン上で一度扱われる。**
  Google/Slack の同意後に発行される認可コードをトークンに交換する処理が connect-web 上で走ります。
  交換後のトークンは**会社 AWS の DynamoDB に KMS 暗号化で保存**され（本人束縛・EncryptionContext={user_email}）、
  オリジンのホストにはトークンを永続化しません。とはいえ「交換の一瞬」だけはこのオリジンが取り扱います。
- **使い捨て認可コードは Cloudflare エッジで TLS 終端される。**
  Plan C ではエッジが HTTPS を終端するため、認可コード（短命・1回使い切り）がエッジを通過します。
  これは Cloudflare for SaaS / Tunnel を使う構成の性質です。コードは短命かつ単回利用のため悪用窓は限定的ですが、
  「自社 AWS 内で完結する構成」と比べると経路に Cloudflare が入る分の信頼前提が増えます。

### 二段構えの方針
1. **パイロットは時限運用**: 当面（10 名規模の連携期間）はこの構成で進め、
   連携が一巡したら cloudflared を止める（受信ポートゼロなので止めれば外部到達も即ゼロ）。
   恒常的に開けっぱなしにしない。
2. **恒久化は将来 会社 AWS 内へ移設**: 組織 SCP の公開 ingress 全面禁止・社内→VPC 経路なし、という現制約が
   緩和でき次第（VPC 経路の用意、または公開例外の承認が取れ次第）、connect-web を会社 AWS 内へ移し、
   認可コード／トークンの取り扱いを自社境界内で完結させる。

---

## 3. 過度な不安を避けるための補足（OAuth の性質）

- **社員のパスワードはこのオリジンに渡りません。**
  OAuth は「パスワードを渡さずに、本人が Google/Slack の画面でアプリへ権限だけを与える」仕組みです。
  パスワード入力は Google/Slack の正規ログイン画面でのみ行われ、connect-web はそれを受け取りません。
  connect-web が受け取るのは、本人が同意した結果としての**認可コード**だけです。
- 与えられる権限の範囲は Google/Slack の同意画面に明示され、本人がいつでも各サービス側で取り消せます。
- 保存されるトークンは本人のものに束縛され、会社 AWS の KMS で暗号化されます。

---

## 4. 構成図（テキスト）

```
[社員ブラウザ]
   │ HTTPS（会社ドメイン connect.vectorinc.co.jp / 正規証明書）
   ▼
[Cloudflare エッジ]  ── WAF / DDoS / TLS終端
   │  Cloudflare Access：@vectorinc.co.jp のみ通過（One-time PIN / Google SSO）
   ▼  （Tunnel：エッジ↔ホストは cloudflared が張った外向き接続）
[常時起動ホスト（Shogo管理）]
   └ cloudflared ──(localhost:8788)──> connect-web
                                          │ 認可コード→トークン交換（一瞬）
                                          ▼
                          [会社AWS] DynamoDB(KMS暗号化) にトークン保存（本人束縛）
受信ポート: ホスト側は 0（インバウンド経路なし）
```
