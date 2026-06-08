"""CLI（run_morning_email main）。既定 --dry-run でダイジェストを stdout。"""
from __future__ import annotations

import pytest

from conftest import REPO_ROOT

from aiia.scripts.run_morning_email import main


def test_cli_dry_run_prints_digest(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)  # ./config を解決させる
    rc = main(["--user", "example_user"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "朝のダイジェスト" in out
    assert "dry_run=True" in out
