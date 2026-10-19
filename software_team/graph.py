"""Wires everything together into the main supervisor graph."""
from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from .agents import (
    make_developer_node,
    make_product_manager_node,
    make_repo_analyst_node,
    make_reviewer_node,
)
from .config import get_settings
from .llm import get_llm
from .state import TeamState, initial_state
from .supervisor import FINISH, make_supervisor_node, route_from_supervisor


def recursion_limit_for(max_iterations: int) -> int:
    """Hard safety net: LangGraph stops after this many steps.

    A run with no rework takes 7 steps and every extra Developer attempt adds 4
    (developer, supervisor, reviewer, supervisor). The margin of 7 extra steps
    leaves room for the normal run, but a runaway loop still gets stopped.
    """
    return 10 + 4 * max_iterations


def build_team_graph(
    llm_factory: Callable[[str], Any] = get_llm,
    supervisor_mode: str | None = None,
    repo=None,
):
    """Build and compile the team graph.

    llm_factory: given an agent name, returns the chat model for that agent
                 (tests pass a fake one, so no API key is needed).
    supervisor_mode: "llm" or "rules"; defaults to the SUPERVISOR_MODE setting.
    repo: a RepoHandle to read an existing GitHub repo first (adds the Repo Analyst step).
          None = the normal graph, exactly as before.
    """
    mode = supervisor_mode or get_settings().supervisor_mode

    def tagged(agent_name: str):
        """The agent's model, tagged so the UsageTracker knows who made each call."""
        return llm_factory(agent_name).with_config(metadata={"agent": agent_name})

    supervisor_llm = tagged("supervisor") if mode == "llm" else None

    graph = StateGraph(TeamState)
    graph.add_node("supervisor", make_supervisor_node(supervisor_llm, mode))
    graph.add_node("product_manager", make_product_manager_node(tagged("product_manager")))
    graph.add_node("developer", make_developer_node(tagged("developer")))
    graph.add_node("reviewer", make_reviewer_node(tagged("reviewer")))

    if repo is None:
        graph.add_edge(START, "supervisor")
    else:  # repo mode: the analyst reads the repo once, then the usual team takes over
        graph.add_node("repo_analyst", make_repo_analyst_node(tagged("repo_analyst"), repo))
        graph.add_edge(START, "repo_analyst")
        graph.add_edge("repo_analyst", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "product_manager": "product_manager",
            "developer": "developer",
            "reviewer": "reviewer",
            FINISH: END,
        },
    )
    # After any worker finishes, control always returns to the supervisor.
    for worker in ("product_manager", "developer", "reviewer"):
        graph.add_edge(worker, "supervisor")

    return graph.compile()


def run_team(
    request: str, graph=None, config: dict | None = None, max_iterations: int | None = None
) -> TeamState:
    """Run the whole team on one feature request and return the final state."""
    graph = graph or build_team_graph()
    max_iterations = max_iterations or get_settings().max_iterations
    run_config = {"recursion_limit": recursion_limit_for(max_iterations), **(config or {})}
    return graph.invoke(initial_state(request, max_iterations), run_config)
