"""Deliverable 3: token and cost tracking per agent."""
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.runnables import RunnableLambda

import main as cli
from conftest import make_fake_llm
from software_team import prompts
from software_team.graph import build_team_graph, run_team
from software_team.llm import _invoke_checked
from software_team.tracking import PRICES, UsageTracker, cost_of

MODEL = "openai/gpt-oss-20b"


class FakeUsageChat(BaseChatModel):
    """A chat model that reports token usage the way Groq does (so callbacks fire)."""

    inner: Any  # a make_fake_llm(...) runnable that decides the reply text
    model_name: str = MODEL
    in_tokens: int = 100
    out_tokens: int = 50
    thinking: int = 0
    finish: str = "stop"

    @property
    def _llm_type(self) -> str:
        return "fake-usage"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = self.inner.invoke(messages).content
        usage = {
            "input_tokens": self.in_tokens,
            "output_tokens": self.out_tokens,
            "total_tokens": self.in_tokens + self.out_tokens,
        }
        if self.thinking:
            usage["output_token_details"] = {"reasoning": self.thinking}
        message = AIMessage(
            content=text,
            usage_metadata=usage,
            response_metadata={"model_name": self.model_name, "finish_reason": self.finish},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def team(review_verdicts=None, mode="rules", choices=None, **chat_kwargs):
    chat = FakeUsageChat(inner=make_fake_llm(choices, review_verdicts), **chat_kwargs)
    return build_team_graph(llm_factory=lambda name: chat, supervisor_mode=mode)


def test_cost_arithmetic():
    # 1M input tokens + 1M output tokens at (0.075, 0.30) per million
    assert cost_of(MODEL, 1_000_000, 1_000_000) == pytest.approx(0.375)
    assert cost_of(MODEL, 2000, 500) == pytest.approx((2000 * 0.075 + 500 * 0.30) / 1e6)
    assert cost_of("some/unknown-model", 10, 10) is None


def test_tokens_are_attributed_to_the_right_agent():
    tracker = UsageTracker()
    run_team("x", graph=team(), config={"callbacks": [tracker]})
    agents = tracker.summary()["agents"]

    # rules mode: no supervisor calls. PM = 2 calls, Developer = 2, Reviewer = 1.
    assert list(agents) == ["product_manager", "developer", "reviewer"]
    assert [a["calls"] for a in agents.values()] == [2, 2, 1]
    assert agents["product_manager"]["input_tokens"] == 200
    assert agents["developer"]["output_tokens"] == 100
    assert agents["reviewer"]["input_tokens"] == 100
    assert tracker.summary()["total"]["calls"] == 5


def test_supervisor_calls_are_tracked_in_llm_mode():
    tracker = UsageTracker()
    graph = team(mode="llm", choices=["product_manager", "developer", "reviewer", "FINISH"])
    run_team("x", graph=graph, config={"callbacks": [tracker]})
    summary = tracker.summary()
    assert summary["agents"]["supervisor"]["calls"] == 4
    assert summary["total"]["calls"] == 9  # 4 supervisor + 2 PM + 2 developer + 1 reviewer


def test_review_loop_costs_more_and_is_visible_per_agent():
    tracker = UsageTracker()
    run_team("x", graph=team(["CHANGES_REQUESTED", "APPROVED"]), config={"callbacks": [tracker]})
    agents = tracker.summary()["agents"]
    assert agents["developer"]["calls"] == 3  # plan + implement + revise
    assert agents["reviewer"]["calls"] == 2


def test_total_cost_matches_the_per_agent_costs():
    tracker = UsageTracker()
    run_team("x", graph=team(), config={"callbacks": [tracker]})
    summary = tracker.summary()
    assert summary["total"]["cost_usd"] == pytest.approx(
        sum(a["cost_usd"] for a in summary["agents"].values())
    )
    assert summary["total"]["cost_usd"] == pytest.approx(cost_of(MODEL, 500, 250))


def test_thinking_tokens_are_reported_but_not_added_twice():
    tracker = UsageTracker()
    run_team("x", graph=team(thinking=20), config={"callbacks": [tracker]})
    total = tracker.summary()["total"]
    assert total["reasoning_tokens"] == 100  # 5 calls x 20
    assert total["output_tokens"] == 250  # unchanged: thinking is already inside the output


def test_unknown_model_is_counted_but_cost_is_na():
    tracker = UsageTracker()
    run_team("x", graph=team(model_name="brand/new-model"), config={"callbacks": [tracker]})
    assert tracker.summary()["total"]["cost_usd"] is None
    assert tracker.summary()["total"]["input_tokens"] == 500
    table = tracker.format_table()
    assert "n/a" in table and "brand/new-model" in table


def test_a_new_tracker_per_run_keeps_runs_separate():
    graph = team()
    first, second = UsageTracker(), UsageTracker()
    run_team("x", graph=graph, config={"callbacks": [first]})
    run_team("x", graph=graph, config={"callbacks": [second]})
    assert first.summary()["total"]["calls"] == second.summary()["total"]["calls"] == 5


def test_no_tracker_means_no_effect():
    assert run_team("x", graph=team())["outcome"] == "approved"


def test_retried_and_cut_off_calls_are_still_counted():
    """Every real API call costs money, even one we throw away."""
    messages = [SystemMessage(prompts.PM_ANALYZE_SYSTEM), HumanMessage("hi")]

    tracker = UsageTracker()
    cut_off = FakeUsageChat(inner=make_fake_llm(), finish="length")
    with pytest.raises(RuntimeError, match="MAX_TOKENS"):
        _invoke_checked(cut_off, messages, {"callbacks": [tracker]})
    assert tracker.summary()["total"]["calls"] == 1  # tokens were spent before we raised
    assert tracker.summary()["total"]["input_tokens"] == 100

    # an empty answer is retried once: that is two paid calls, and both must show up
    answers = iter(["", "second try works"])
    flaky = FakeUsageChat(inner=RunnableLambda(lambda m: AIMessage(content=next(answers))))
    tracker = UsageTracker()
    assert _invoke_checked(flaky, messages, {"callbacks": [tracker]}).content == "second try works"
    assert tracker.summary()["total"]["calls"] == 2


def test_table_layout_and_empty_tracker():
    assert UsageTracker().format_table() == ""
    tracker = UsageTracker()
    run_team("x", graph=team(), config={"callbacks": [tracker]})
    lines = tracker.format_table().splitlines()
    assert lines[0].split() == ["Agent", "Calls", "Input", "Output", "(thinking)", "Cost"]
    assert lines[-1].startswith("TOTAL") and "$" in lines[-1]
    assert [l.split()[0] for l in lines[1:-2]] == ["product_manager", "developer", "reviewer"]


def test_handler_survives_odd_responses():
    tracker = UsageTracker()
    tracker.on_llm_end(LLMResult(generations=[]), run_id=uuid4())  # no generations at all
    assert tracker.calls[0]["agent"] == "unknown"
    assert tracker.calls[0]["input_tokens"] == 0


def test_prices_table_has_the_default_model():
    assert MODEL in PRICES


def test_cli_prints_table_and_saves_usage(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_team_graph", lambda: team())
    assert cli.main(["a page", "--output-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "TOTAL" in out and "product_manager" in out

    import json

    info = json.loads((next(tmp_path.iterdir()) / "run.json").read_text(encoding="utf-8"))
    assert info["usage"]["total"]["calls"] == 5
    assert set(info["usage"]["agents"]) == {"product_manager", "developer", "reviewer"}


def test_cli_still_shows_the_cost_when_a_model_call_fails(tmp_path, monkeypatch, capsys):
    """The cut-off error stops the run, but the money already spent must be visible."""
    # PM answers fine, then the Developer's model is cut off (finish_reason=length).
    class CutOffAfterSpec(FakeUsageChat):
        def _generate(self, messages, *args, **kwargs):
            if "implementation plan" in messages[0].content:  # DEV_PLAN_SYSTEM
                self.finish = "length"
            return super()._generate(messages, *args, **kwargs)

    chat = CutOffAfterSpec(inner=make_fake_llm())
    # use the real wrapper so the finish_reason check runs
    from langchain_core.runnables import RunnableLambda

    wrapped = RunnableLambda(lambda messages, config: _invoke_checked(chat, messages, config))
    graph = build_team_graph(llm_factory=lambda name: wrapped, supervisor_mode="rules")
    monkeypatch.setattr(cli, "build_team_graph", lambda: graph)

    with pytest.raises(RuntimeError, match="MAX_TOKENS"):
        cli.main(["a page", "--output-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "TOTAL" in out and "product_manager" in out and "developer" in out