"""朝メールAgent CLI。既定は --dry-run（副作用ゼロ・ダイジェストを stdout）。

例:
  python -m aiia.scripts.run_morning_email                 # dry-run（既定・安全）
  python -m aiia.scripts.run_morning_email --no-dry-run    # 下書き作成＋Slack下書き送付
  python -m aiia.scripts.run_morning_email --profile heuristic --user example_user
"""
from __future__ import annotations

import argparse
from typing import Optional

from aiia.delivery import render_digest_text
from aiia.orchestrator import run_morning_email


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="朝のメール確認サポートAgent")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                   help="副作用ゼロ（既定）。下書き/ラベル/Slack送付をしない")
    p.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                   help="実際に下書き作成＋Slack下書き送付を行う（送信はしない）")
    p.add_argument("--profile", default=None, help="LLMプロファイル (heuristic / bedrock / api)")
    p.add_argument("--user", default=None, help="対象ユーザーID（config/users/<id>.yaml）")
    args = p.parse_args(argv)

    res = run_morning_email(user=args.user, dry_run=args.dry_run, profile=args.profile)
    print(render_digest_text(res.digest))
    print(
        f"\n[dry_run={res.dry_run}] processed={res.digest.processed} "
        f"drafts_created={res.drafts_created} labels={res.labels_applied} redactions={res.redactions}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
