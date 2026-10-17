"""Shared test helpers: a fake LLM so tests run offline, free, and instantly."""
import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from software_team import prompts
@pytest.fixture(autouse=True)
def no_network_for_prices(monkeypatch):
    """Tests must never download the online price list."""
    monkeypatch.setenv("PRICE_LOOKUP", "off")

def make_fake_llm(supervisor_choices=None):
    """A fake model that answers based on which system prompt it receives."""
    choices = iter(supervisor_choices or [])

    def respond(messages):
        system = messages[0].content
        if system == prompts.SUPERVISOR_SYSTEM:
            decision = {"next": next(choices, "FINISH"), "reason": "test"}
            return AIMessage(content=json.dumps(decision))
        if system == prompts.PM_ANALYZE_SYSTEM:
            return AIMessage(content="- goal: fake analysis")
        if system == prompts.PM_SPEC_SYSTEM:
            return AIMessage(content="# Fake Spec\n## Overview\nA fake spec.")
        if system == prompts.DEV_PLAN_SYSTEM:
            return AIMessage(content="plan: one file")
        if system == prompts.DEV_CODE_SYSTEM:
            return AIMessage(content="### app.py\n```python\nprint('hi')\n```")
        if system == prompts.REVIEW_SYSTEM:
            return AIMessage(content="## Summary\nLooks fine.\n\nVERDICT: APPROVED")
        raise AssertionError(f"unexpected system prompt: {system[:40]!r}")

    return RunnableLambda(respond)


@pytest.fixture
def make_factory():
    """make_factory(["product_manager", ...]) -> llm_factory for build_team_graph."""

    def _make(supervisor_choices=None):
        llm = make_fake_llm(supervisor_choices)
        return lambda agent_name: llm

    return _make
