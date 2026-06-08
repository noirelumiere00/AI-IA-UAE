"""共有フィクスチャ。実 Gmail/LLM/ネットワークに触れない（Fake + Heuristic のみ）。"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aiia.config import MorningEmailConfig, UserConfig
from aiia.llm import HeuristicLLM
from aiia.mcp.fake import FakeMCPToolset
from aiia.pipeline import PipelineDeps

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
FIXED_NOW = datetime(2026, 6, 8, 7, 0, tzinfo=timezone.utc)  # テスト決定性


@pytest.fixture
def user() -> UserConfig:
    return UserConfig(
        user_id="t",
        display_name="テスト太郎",
        vip_domains=["bigclient.ae"],
        client_domains=["client.co.jp"],
        internal_domain="newstv.co.jp",
        quiet_categories=["NEWSLETTER"],
    )


@pytest.fixture
def agent() -> MorningEmailConfig:
    return MorningEmailConfig(draft_categories=["CLIENT_URGENT", "PRESS_MEDIA"], max_threads=50)


@pytest.fixture
def llm() -> HeuristicLLM:
    return HeuristicLLM()


@pytest.fixture
def tools() -> FakeMCPToolset:
    return FakeMCPToolset()


@pytest.fixture
def make_deps(
    llm: HeuristicLLM, user: UserConfig, agent: MorningEmailConfig
) -> Callable[..., PipelineDeps]:
    def _make(tools: FakeMCPToolset, *, dry_run: bool = True) -> PipelineDeps:
        return PipelineDeps(
            llm=llm, tools=tools, user=user, agent=agent, dry_run=dry_run, now=FIXED_NOW
        )

    return _make
