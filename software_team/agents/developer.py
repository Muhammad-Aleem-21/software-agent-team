"""Developer agent = a small sub-graph with two paths.

    first attempt :  plan -> implement
    after a review:  revise   (fix the previous code using the reviewer's feedback)

The choice is made at the start of the sub-graph by `route_start`.
"""
from __future__ import annotations

from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from ..llm import content_text, require_text
from ..prompts import DEV_CODE_SYSTEM, DEV_PLAN_SYSTEM, DEV_REVISE_SYSTEM
from ..state import TeamState, make_event

NAME = "developer"


class DevState(TypedDict, total=False):
    request: str
    spec: str
    plan: str
    code: str
    previous_code: str  # only set when this is a revision
    feedback: str  # the reviewer's text; only set when this is a revision


def build_developer_graph(llm):
    def route_start(state: DevState) -> str:
        return "revise" if state.get("feedback") else "plan"

    def plan(state: DevState, config: RunnableConfig) -> dict:
        reply = llm.invoke(
            [SystemMessage(DEV_PLAN_SYSTEM), HumanMessage(state["spec"])], config=config
        )
        return {"plan": require_text(content_text(reply), "implementation plan")}

    def implement(state: DevState, config: RunnableConfig) -> dict:
        prompt = f"Specification:\n{state['spec']}\n\nImplementation plan:\n{state['plan']}"
        reply = llm.invoke([SystemMessage(DEV_CODE_SYSTEM), HumanMessage(prompt)], config=config)
        return {"code": require_text(content_text(reply), "code")}

    def revise(state: DevState, config: RunnableConfig) -> dict:
        prompt = (
            f"Specification:\n{state['spec']}\n\n"
            f"Your previous code:\n{state['previous_code']}\n\n"
            f"Reviewer feedback:\n{state['feedback']}"
        )
        reply = llm.invoke([SystemMessage(DEV_REVISE_SYSTEM), HumanMessage(prompt)], config=config)
        return {"code": require_text(content_text(reply), "revised code")}

    graph = StateGraph(DevState)
    graph.add_node("plan", plan)
    graph.add_node("implement", implement)
    graph.add_node("revise", revise)
    graph.add_conditional_edges(START, route_start, {"plan": "plan", "revise": "revise"})
    graph.add_edge("plan", "implement")
    graph.add_edge("implement", END)
    graph.add_edge("revise", END)
    return graph.compile()


def make_developer_node(llm):
    subgraph = build_developer_graph(llm)

    def developer_node(state: TeamState, config: RunnableConfig) -> dict:
        iteration = state.get("iteration", 0) + 1
        sub_input = {"request": state["request"], "spec": state["spec"]}

        # A non-empty review next to existing code means: the reviewer sent it back.
        is_revision = bool(state.get("review") and state.get("code"))
        if is_revision:
            sub_input["previous_code"] = state["code"]
            sub_input["feedback"] = state["review"]

        result = subgraph.invoke(sub_input, config)
        action = "code_revised" if is_revision else "code_written"
        return {
            "code": result["code"],
            "iteration": iteration,
            # The old review was about the old code, so clear it. This is how the
            # supervisor knows the new code still needs a review.
            "review": "",
            "verdict": "pending",
            "versions": [{"kind": "code", "iteration": iteration, "text": result["code"]}],
            "history": [
                make_event(NAME, action, f"attempt {iteration}, {len(result['code'])} chars")
            ],
        }

    return developer_node