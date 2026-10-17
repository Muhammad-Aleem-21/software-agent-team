"""Reviewer agent = a small sub-graph:  review -> decide.

`review` is an LLM call. `decide` is plain Python that reads the verdict line out of
the review text, so the supervisor gets a clean value instead of free text.
"""
from __future__ import annotations

import re
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from ..llm import content_text, require_text
from ..prompts import REVIEW_SYSTEM
from ..state import TeamState, make_event

NAME = "reviewer"

_VERDICT_RE = re.compile(r"VERDICT:\s*\**\s*(APPROVED|CHANGES_REQUESTED)", re.IGNORECASE)


def parse_verdict(review_text: str) -> str:
    """Return "approved" or "changes_requested".

    If the model forgets the verdict line we play safe and say "changes_requested".
    """
    matches = _VERDICT_RE.findall(review_text)
    if matches and matches[-1].upper() == "APPROVED":
        return "approved"
    return "changes_requested"


class ReviewState(TypedDict, total=False):
    spec: str
    code: str
    review: str
    verdict: str


def build_reviewer_graph(llm):
    def review(state: ReviewState, config: RunnableConfig) -> dict:
        prompt = f"Specification:\n{state['spec']}\n\nCode to review:\n{state['code']}"
        reply = llm.invoke([SystemMessage(REVIEW_SYSTEM), HumanMessage(prompt)], config=config)
        #print("DEBUG:", reply.response_metadata, reply.additional_kwargs)
        return {"review": require_text(content_text(reply), "review")}

    def decide(state: ReviewState) -> dict:
        return {"verdict": parse_verdict(state["review"])}

    graph = StateGraph(ReviewState)
    graph.add_node("review", review)
    graph.add_node("decide", decide)
    graph.add_edge(START, "review")
    graph.add_edge("review", "decide")
    graph.add_edge("decide", END)
    return graph.compile()


def make_reviewer_node(llm):
    subgraph = build_reviewer_graph(llm)

    def reviewer_node(state: TeamState, config: RunnableConfig) -> dict:
        result = subgraph.invoke({"spec": state["spec"], "code": state["code"]}, config)
        iteration = state.get("iteration", 1)
        return {
            "review": result["review"],
            "verdict": result["verdict"],
            "versions": [
                {
                    "kind": "review",
                    "iteration": iteration,
                    "verdict": result["verdict"],
                    "text": result["review"],
                }
            ],
            "history": [
                make_event(
                    NAME, "review_written", f"attempt {iteration}, verdict={result['verdict']}"
                )
            ],
        }

    return reviewer_node
