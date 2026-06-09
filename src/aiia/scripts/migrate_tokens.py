"""TeamAgent RDS oauth_tokens → AI-IA-UAE DynamoDB へ per-user refresh token を移行（再連携不要化）。

token は client_id 束縛・redirect_uri 非束縛なので、同じ pgd1 client を使えば移行後そのまま有効。
**順序厳守**: ①本スクリプト(RDS読取+KMS復号→DynamoDB再暗号化put) → ②件数照合 → ③AiLa疎通 → ④RDS teardown。

要 env（ユーザーが AWS creds + RDS到達経路で実行）:
  DATABASE_URL        TeamAgent RDS (postgresql://teamagent:***@host:5432/teamagent?sslmode=require)
  SOURCE_KMS_KEY_ID   TeamAgent の oauth-tokens KMS 鍵（復号用）
  TARGET_KMS_KEY_ID   移行先 KMS 鍵（既存流用なら SOURCE と同じでよい）
  AIIA_DDB_TABLE      移行先 DynamoDB テーブル名（例 aiia-oauth-tokens）
  AWS_REGION          ap-northeast-1
`--dry-run` で件数とメールだけ表示（書込なし）。
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from aiia.auth.token_store import DynamoDbTokenStore, KmsCipher, OAuthToken, TokenCipher, TokenStore


def migrate(rows: list[dict[str, Any]], *, src_cipher: TokenCipher, store: TokenStore) -> int:
    """RDS 行（user_email / refresh_token_enc / scopes）を復号→再暗号化put。移行件数を返す。"""
    n = 0
    for r in rows:
        email = str(r["user_email"])
        plain = src_cipher.decrypt(bytes(r["refresh_token_enc"]), context={"user_email": email})
        scopes = tuple(r.get("scopes") or ())
        store.put(email, OAuthToken(refresh_token=plain, scopes=scopes))
        n += 1
        print(f"  migrated: {email}  scopes={list(scopes)}")
    return n


def _read_rds(database_url: str) -> list[dict[str, Any]]:
    import psycopg2  # 遅延 import（移行実行環境のみ）
    import psycopg2.extras

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET app.user_role = 'admin'")  # RLS bypass（admin ポリシー）
            cur.execute("SELECT user_email, refresh_token_enc, scopes FROM oauth_tokens")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TeamAgent RDS → AI-IA-UAE DynamoDB token 移行")
    ap.add_argument("--dry-run", action="store_true", help="件数とメールのみ表示（書込なし）")
    args = ap.parse_args(argv)

    db = os.environ.get("DATABASE_URL")
    if not db:
        print("DATABASE_URL が未設定です", file=sys.stderr)
        return 2
    rows = _read_rds(db)
    print(f"RDS oauth_tokens: {len(rows)} 件")
    if args.dry_run:
        for r in rows:
            print(" -", r["user_email"], r.get("scopes"))
        return 0

    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    src_key = os.environ["SOURCE_KMS_KEY_ID"]
    tgt_key = os.environ.get("TARGET_KMS_KEY_ID", src_key)
    table = os.environ["AIIA_DDB_TABLE"]
    src_cipher = KmsCipher(src_key, region=region)
    store = DynamoDbTokenStore(table, KmsCipher(tgt_key, region=region), region=region)

    n = migrate(rows, src_cipher=src_cipher, store=store)
    dst = store.list_emails()
    print(f"DynamoDB {table} へ {n} 件移行完了 / 現在件数 {len(dst)}")
    if n != len(dst):
        print("⚠ 件数不一致：照合してください", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
