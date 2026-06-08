"""orchestrator 層: 設定読込→LLM/道具を組んで pipeline を起動する入口。"""
from __future__ import annotations

from aiia.orchestrator.orchestrator import run_morning_email

__all__ = ["run_morning_email"]
