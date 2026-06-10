"""朝バッチ起動ランチャ（aila.sh batch）。連携済み全員へ朝ダイジェストを Slack DM 配信。

SLACK_CLIENT_ID + AIIA_SLACK_TOKEN_TABLE が揃う時だけ Slack未返信メンションも合流（認可者のみ・fail-safe）。
"""

from __future__ import annotations

import os


def main() -> None:
    from aiia.adapters.slack_client import SlackDelivery
    from aiia.auth.token_store import DynamoDbTokenStore, KmsCipher
    from aiia.orchestrator.multi import run_for_all_users
    from aiia.state.reminder_store import DynamoDbReminderStore

    cipher = KmsCipher(os.environ["OAUTH_KMS_KEY_ID"])
    store = DynamoDbTokenStore(os.environ["AIIA_DDB_TABLE"], cipher)
    rstore = DynamoDbReminderStore(os.environ.get("AIIA_REMINDER_TABLE", "aiia-reminder-state"))

    slack_token_store = None
    slack_table = os.environ.get("AIIA_SLACK_TOKEN_TABLE")
    if os.environ.get("SLACK_CLIENT_ID") and slack_table:
        slack_token_store = DynamoDbTokenStore(slack_table, cipher)

    for r in run_for_all_users(
        store=store,
        slack=SlackDelivery(),
        dry_run=False,
        max_budget_usd=1.0,
        reminder_store=rstore,
        slack_token_store=slack_token_store,
    ):
        print(r)


if __name__ == "__main__":
    main()
