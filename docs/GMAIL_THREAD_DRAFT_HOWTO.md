# Gmailで「同一スレッドを保持したまま下書きを差し込む」実装ガイド

> AiLa（朝メールアシスタント）で実証済みの方法をまとめたもの。社内の開発者がそのまま再現できる粒度で書いています。

## ゴール
受信メールに対し、**プログラムで返信下書き**を作る。その下書きが
1. **元の会話スレッドに連なる**（新規スレッドにバラけない）
2. Gmailで開くと**スレッド内インライン**に、**本人の署名つき・リッチ書式**で開く
3. （応用）**リンク1クリック**で該当スレッドが開き、下書きが入っている

---

## 3本柱

### 柱1｜スレッド連結（同一スレッド保持）
次の2つを **両方** やるのが堅い：
- 下書き作成時に **`threadId` を指定**（Gmail内部のスレッド結合）
- MIMEに **`In-Reply-To` / `References`** を、返信対象メッセージの **`Message-ID`** で設定（他クライアントでも正しくスレッド化）
- 件名は `Re: <元件名>`

```python
import base64
raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
service.users().drafts().create(
    userId="me",
    body={"message": {"threadId": thread_id, "raw": raw}},
).execute()
```

> `threadId` だけでもGmail内部では同スレ扱いになるが、`In-Reply-To`/`References` を入れるとOutlook等でも正しくスレッド化されて堅牢。

### 柱2｜インライン表示＋署名（ここが一番ハマる）
経験則（実機で確認済み）：
- **プレーンテキストの下書き → 受信トレイ上にポップアップ**で開く（✕）
- **HTMLの下書き → スレッド内にインライン**で開く（◎）

なので本文は必ず `MIMEText(html, "html")`。署名はGmailの **既定署名(HTML)** を取得して末尾に連結：

```python
import html
from email.mime.text import MIMEText

# 既定署名(HTML)を取得 ※ gmail.modify スコープで取得可
sendas = service.users().settings().sendAs().list(userId="me").execute()
sig = next((a.get("signature", "") for a in sendas["sendAs"] if a.get("isDefault")), "")

body_html = html.escape(ai_text).replace("\n", "<br>")
full_html = f'<div dir="ltr">{body_html}</div>' + (f"<br><br>{sig}" if sig else "")
mime = MIMEText(full_html, "html", "utf-8")
```

### 柱3｜1クリックでインラインに開くURL
- `https://mail.google.com/mail/u/0/#all/<threadId>` → 会話を **インライン** で開く（◎）
- `https://mail.google.com/mail/u/0/#inbox/<threadId>` → **ポップアップ** になる（✕）
- そのスレッドに下書きが既にあれば、開いた瞬間に返信欄が下書きで埋まった状態。

---

## 全返信(Reply-All)の宛先組み立て
元メールのヘッダから：
- **To = 元From ＋ 元To（自分を除外）** ／ **Cc = 元Cc（自分を除外）**
- `email.utils.getaddresses` で分解、小文字化で重複排除

```python
from email.utils import getaddresses
pairs = getaddresses([h.get("from", ""), h.get("to", "")])
to = list({a.lower(): a for _, a in pairs if a and a.lower() != me.lower()}.values())
mime["To"] = ", ".join(to)
mime["Subject"] = "Re: " + subject
mime["In-Reply-To"] = target_message_id        # 返信対象の Message-ID
mime["References"]  = target_message_id
```

---

## 返信対象は「最新の実メッセージ」
スレッドの最後が自分のDRAFTだと、相手＝自分と誤認して宛先が壊れる（`Invalid To header`）。
→ **DRAFTを除いた最新**（自分が既に送っていればその送信済みの後を追う）を対象にする。

```python
# DRAFT を除いた最新メッセージを返信対象にする
real = [m for m in thread_messages if "DRAFT" not in m["labelIds"]]
target = real[-1]  # 最新の実メッセージ（自分の送信済み返信を含む）
```

---

## 競合対策（リンクが下書きより先に開く問題）
「ボタン → 下書き作成 → リンク」を別々にやると、URLが先に開いて **下書き未作成** になりがち。
→ サーバー側の **`/reply` エンドポイントで「作ってから302リダイレクト」** する：

```
GET /reply?s=<署名トークン>      # トークン = HMAC(email + threadId)
  1) トークン検証（改竄防止・PIIはemail+threadIdのみ）
  2) 同スレの古い下書きを掃除（重複防止・べき等）
  3) 全返信 HTML+署名 の下書きを作成
  4) 302 → https://mail.google.com/mail/u/0/#all/<threadId>
```

> こうすると Slack 等のボタンを押した瞬間に「下書き入りの該当スレッド」がインラインで開く＝1クリック体験になる。

---

## ハマりどころ早見表
| 症状 | 原因 | 対処 |
|---|---|---|
| 別スレッドで新規作成される | `threadId` / `In-Reply-To` 未設定 | 両方セット＋`Re:`件名 |
| ポップアップで開く | 下書きがプレーンテキスト | HTML(`MIMEText(...,"html")`)にする |
| 署名が出ない | 本文のみ | `sendAs` の既定署名を末尾に連結 |
| 古いメールに返信される | 最後のDRAFT/古いmsgを対象化 | DRAFT除外の最新を対象 |
| リンク先が空 | URLが下書き作成より先に開く | `/reply` で作成後リダイレクト |

---

## 必要スコープ
`https://www.googleapis.com/auth/gmail.modify` 1本で **下書きの作成/更新/削除＋署名取得** まで可能（恒久削除の mail 全権は不要）。
送信スコープは付けず **下書きまで** に留めると、人間の最終確認を担保できて安全。

---

## 参考：AiLa内の実装箇所
- `src/aiia/mcp/workspace_gmail.py` … `create_reply_draft()`（全返信・HTML+署名・threadId/In-Reply-To）、`default_signature()`（既定署名取得・キャッシュ）
- `src/aiia/connect_web/app.py` / `callback.py` … `/reply` エンドポイント（作成→`#all/`へ302）
- `src/aiia/auth/oauth_flow.py` … `make_reply_token` / `make_reply_url`（HMAC署名トークン）
