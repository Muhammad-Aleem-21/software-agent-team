import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from conftest import make_fake_llm
from software_team.agents.product_manager import build_product_manager_graph
from software_team.agents.reviewer import parse_verdict
from software_team.graph import build_team_graph, run_team
from software_team.supervisor import legal_moves, parse_decision

FULL_ORDER = [
    "supervisor", "product_manager",
    "supervisor", "developer",
    "supervisor", "reviewer",
    "supervisor",
]


def agents_in_order(state):
    return [event["agent"] for event in state["history"]]


def supervisor_events(state):
    return [e for e in state["history"] if e["agent"] == "supervisor"]


def test_full_run_with_llm_supervisor(make_factory):
    graph = build_team_graph(
        llm_factory=make_factory(["product_manager", "developer", "reviewer", "FINISH"]),
        supervisor_mode="llm",
    )
    state = run_team("Add a password reset feature", graph=graph)

    assert state["spec"].startswith("# Fake Spec")
    assert "app.py" in state["code"]
    assert "VERDICT: APPROVED" in state["review"]
    assert state["verdict"] == "approved"
    assert agents_in_order(state) == FULL_ORDER
    assert all(e["source"] == "llm" for e in supervisor_events(state))


def test_guardrail_overrides_illegal_llm_choice(make_factory):
    # The fake supervisor stubbornly says "reviewer" every time.
    graph = build_team_graph(llm_factory=make_factory(["reviewer"] * 10), supervisor_mode="llm")
    state = run_team("Add a password reset feature", graph=graph)

    assert agents_in_order(state) == FULL_ORDER  # workflow still correct
    first = supervisor_events(state)[0]
    assert first["next"] == "product_manager"
    assert first["source"] == "rules"
    assert "not allowed" in first["detail"]


def test_rules_mode_needs_no_supervisor_llm(make_factory):
    worker_factory = make_factory()

    def factory(agent_name):
        assert agent_name != "supervisor", "rules mode must not create a supervisor LLM"
        return worker_factory(agent_name)

    state = run_team("x", graph=build_team_graph(llm_factory=factory, supervisor_mode="rules"))
    assert agents_in_order(state) == FULL_ORDER
    assert all(e["source"] == "rules" for e in supervisor_events(state))


def test_supervisor_survives_llm_errors():
    def broken_supervisor(messages):
        raise ConnectionError("network down")

    working = make_fake_llm()

    def factory(agent_name):
        return RunnableLambda(broken_supervisor) if agent_name == "supervisor" else working

    state = run_team("x", graph=build_team_graph(llm_factory=factory, supervisor_mode="llm"))
    assert agents_in_order(state) == FULL_ORDER
    assert "ConnectionError" in supervisor_events(state)[0]["detail"]


def test_empty_model_answer_stops_the_run():
    empty = RunnableLambda(lambda messages: AIMessage(content=""))
    graph = build_team_graph(llm_factory=lambda name: empty, supervisor_mode="rules")
    with pytest.raises(RuntimeError, match="empty"):
        run_team("x", graph=graph)


def test_product_manager_subgraph_runs_alone():
    graph = build_product_manager_graph(make_fake_llm())
    result = graph.invoke({"request": "anything"})
    assert result["analysis"].startswith("- goal")
    assert result["spec"].startswith("# Fake Spec")


def test_legal_moves_follow_the_workflow():
    assert legal_moves({}) == ["product_manager"]
    assert legal_moves({"spec": "s"}) == ["developer"]
    assert legal_moves({"spec": "s", "code": "c"}) == ["reviewer"]
    assert legal_moves({"spec": "s", "code": "c", "review": "r"}) == ["FINISH"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"next": "developer", "reason": "spec ready"}', ("developer", "spec ready")),
        ('Sure! {"next": "reviewer", "reason": "x"} done', ("reviewer", "x")),
        ('{"next": "finish", "reason": "all done"}', ("FINISH", "all done")),
        ("no json here", (None, "")),
        ("{broken json}", (None, "")),
    ],
)
def test_parse_decision(text, expected):
    assert parse_decision(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("looks good\nVERDICT: APPROVED", "approved"),
        ("bugs\nVERDICT: CHANGES_REQUESTED", "changes_requested"),
        ("**VERDICT: APPROVED**", "approved"),
        ("verdict: approved", "approved"),
        ("I forgot the verdict line", "changes_requested"),
    ],
)
def test_parse_verdict(text, expected):
    assert parse_verdict(text) == expected
