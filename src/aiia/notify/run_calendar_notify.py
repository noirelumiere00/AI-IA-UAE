"""5分前カレンダー通知のランチャ（aila.sh notify / systemd timer 毎分）。

連携済み(Google+Slack)全員の直近予定を本人DMへメンション。claim-before-send で1回だけ。
環境変数: OAUTH_KMS_KEY_ID / AIIA_DDB_TABLE / AIIA_NOTIFY_TABLE(既定 aiia-notified-events) /
          AIIA_SLACK_TOKEN_TABLE(指定時=Slack連携必須) / SLACK_BOT_TOKEN / AIIA_NOTIFY_LEAD_MIN(既定5)
"""
from __future__ import annotations

import os


def main() -> None:
    from aiia.adapters.slack_client import SlackDelivery
    from aiia.auth.token_store import DynamoDbTokenStore, KmsCipher
    from aiia.notify.calendar_notifier import run_calendar_notify
    from aiia.state.notified_store import DynamoDbNotifiedStore

    cipher = KmsCipher(os.environ["OAUTH_KMS_KEY_ID"])
    store = DynamoDbTokenStore(os.environ["AIIA_DDB_TABLE"], cipher)
    notified = DynamoDbNotifiedStore(os.environ.get("AIIA_NOTIFY_TABLE", "aiia-notified-events"))

    slack_token_store = None
    slack_table = os.environ.get("AIIA_SLACK_TOKEN_TABLE")
    if slack_table:  # §V: Slack連携必須化（両連携済みのみ通知）
        slack_token_store = DynamoDbTokenStore(slack_table, cipher)

    lead = int(os.environ.get("AIIA_NOTIFY_LEAD_MIN", "5"))
    for r in run_calendar_notify(
        store=store,
        slack=SlackDelivery(),
        notified=notified,
        slack_token_store=slack_token_store,
        lead_minutes=lead,
    ):
        if r.notified or not r.ok:
            print(r)


if __name__ == "__main__":
    main()
