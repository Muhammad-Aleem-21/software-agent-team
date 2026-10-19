"""Product Manager agent = a small sub-graph:  analyze -> write_spec."""
from __future__ import annotations

from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from ..llm import content_text, require_text
from ..prompts import PM_ANALYZE_SYSTEM, PM_SPEC_SYSTEM, REPO_PM_NOTE
from ..repo_context import repo_block
from ..state import TeamState, make_event

NAME = "product_manager"


class PMState(TypedDict, total=False):
    """The sub-graph's own private state ('analysis' never leaks into the team state)."""

    request: str
    analysis: str
    spec: str
    repo_mode: str  # repo mode only
    repo_summary: str


def build_product_manager_graph(llm):
    def repo_text(state: PMState) -> str:  # "" in Quick mode
        return repo_block(state.get("repo_mode", ""), state.get("repo_summary", ""))

    def system_for(base: str, state: PMState) -> str:
        return base + (REPO_PM_NOTE if state.get("repo_mode") == "existing" else "")

    def analyze(state: PMState, config: RunnableConfig) -> dict:
        reply = llm.invoke(
            [
                SystemMessage(system_for(PM_ANALYZE_SYSTEM, state)),
                HumanMessage(state["request"] + repo_text(state)),
            ],
            config=config,
        )
        return {"analysis": require_text(content_text(reply), "analysis")}

    def write_spec(state: PMState, config: RunnableConfig) -> dict:
        prompt = f"Feature request:\n{state['request']}\n\nAnalysis notes:\n{state['analysis']}"
        prompt += repo_text(state)
        reply = llm.invoke(
            [SystemMessage(system_for(PM_SPEC_SYSTEM, state)), HumanMessage(prompt)], config=config
        )
        return {"spec": require_text(content_text(reply), "spec")}

    graph = StateGraph(PMState)
    graph.add_node("analyze", analyze)
    graph.add_node("write_spec", write_spec)
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "write_spec")
    graph.add_edge("write_spec", END)
    return graph.compile()


def make_product_manager_node(llm):
    """Wrap the sub-graph as a node for the main team graph."""
    subgraph = build_product_manager_graph(llm)

    def product_manager_node(state: TeamState, config: RunnableConfig) -> dict:
        result = subgraph.invoke(
            {
                "request": state["request"],
                "repo_mode": state.get("repo_mode", ""),
                "repo_summary": state.get("repo_summary", ""),
            },
            config,
        )
        return {
            "spec": result["spec"],
            "history": [make_event(NAME, "spec_written", f"{len(result['spec'])} chars")],
        }

    return product_manager_node
