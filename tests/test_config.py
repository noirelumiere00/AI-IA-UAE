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
