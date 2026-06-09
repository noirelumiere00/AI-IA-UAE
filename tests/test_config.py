"""設定3層（platform / agent / user）の読込。"""
from __future__ import annotations

from conftest import CONFIG_DIR

from aiia.config import load_agent_config, load_platform, load_user


def test_load_platform() -> None:
    p = load_platform(CONFIG_DIR)
    assert p.default_profile == "heuristic"


def test_load_agent() -> None:
    a = load_agent_config("morning_email", CONFIG_DIR)
    assert "CLIENT_URGENT" in a.draft_categories
    assert "PRESS_MEDIA" in a.draft_categories
    assert a.max_threads == 50


def test_load_user() -> None:
    u = load_user("example_user", CONFIG_DIR)
    assert u.user_id == "example_user"
    assert "bigclient.ae" in u.vip_domains
    assert "NEWSLETTER" in u.quiet_categories


def test_load_user_missing_uses_default() -> None:
    u = load_user("no_such_user", CONFIG_DIR)
    assert u.user_id == "no_such_user"  # yaml が無くても id を保持


def test_load_user_org_default_internal_domain(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # yaml 無しユーザーでも env で社内ドメインを補完（全員同一org運用）
    monkeypatch.setenv("AIIA_DEFAULT_INTERNAL_DOMAIN", "vectorinc.co.jp")
    u = load_user("s-komata@vectorinc.co.jp", CONFIG_DIR)
    assert u.internal_domain == "vectorinc.co.jp"


def test_load_user_yaml_internal_domain_wins(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # per-user yaml の internal_domain は env 既定より優先
    monkeypatch.setenv("AIIA_DEFAULT_INTERNAL_DOMAIN", "vectorinc.co.jp")
    u = load_user("example_user", CONFIG_DIR)  # yaml で newstv.co.jp 指定済
    assert u.internal_domain == "newstv.co.jp"
