"""harness（実データJSON→pipeline供給）。実データは使わず匿名の合成 fixture で検証。"""
from __future__ import annotations

from conftest import REPO_ROOT

from aiia.config import MorningEmailConfig, UserConfig
from aiia.llm import HeuristicLLM
from aiia.mcp.harness import HarnessMCPToolset, load_threads
from aiia.pipeline import PipelineDeps, run
from aiia.schemas import Category

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "harness_sample.json"


def test_load_threads_parses() -> None:
    threads = load_threads(FIXTURE)
    assert [t.thread_id for t in threads] == ["h1", "h2", "h3"]
    assert threads[0].sender_domain == "client.example"
    assert threads[1].messages[0].headers.get("List-Unsubscribe")


def test_from_json_marks_existing_draft() -> None:
    tools = HarnessMCPToolset.from_json(FIXTURE)
    assert tools.gmail.get_thread("h3").has_existing_draft is True
    assert tools.gmail.get_thread("h1").has_existing_draft is False


def test_pipeline_on_harness_dry_run_zero_side_effects() -> None:
    tools = HarnessMCPToolset.from_json(FIXTURE)
    user = UserConfig(
        user_id="t",
        client_domains=["client.example"],
        internal_domain="acme.example",
        quiet_categories=["NEWSLETTER"],
    )
    agent = MorningEmailConfig(draft_categories=["CLIENT_URGENT", "PRESS_MEDIA"], max_threads=50)
    res = run(PipelineDeps(llm=HeuristicLLM(), tools=tools, user=user, agent=agent, dry_run=True))

    assert tools.calls == []  # 読取のみ・副作用ゼロ
    assert res.digest.processed == 3
    # h1=至急のクライアント→CLIENT_URGENT, h2=List-Unsubscribe→NEWSLETTER(quiet畳み), h3=社内→INTERNAL
    cats = {it.category for it in res.digest.items}
    assert Category.CLIENT_URGENT in cats
    assert res.digest.quiet_counts.get(Category.NEWSLETTER) == 1
