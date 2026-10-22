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

from ..config import get_settings
from ..llm import content_text, require_text
from ..prompts import CHECK_NOTE, REPO_REVIEW_NOTE, REVIEW_SYSTEM
from ..repo_context import parse_changes, repo_block
from ..sandbox import check_code
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
    repo_mode: str  # repo mode only
    repo_summary: str
    repo_files: dict
    check_status: str
    check_report: str


def build_reviewer_graph(llm):
    def review(state: ReviewState, config: RunnableConfig) -> dict:
        prompt = f"Specification:\n{state['spec']}\n\nCode to review:\n{state['code']}"
        system = REVIEW_SYSTEM
        if state.get("check_report"):
            prompt += f"\n\nAutomatic check of the code ({state.get('check_status')}):\n{state['check_report']}"
            prompt += CHECK_NOTE
        mode = state.get("repo_mode", "")
        if mode:  # repo mode: also show the original versions of the files that were changed
            system += REPO_REVIEW_NOTE
            changed = {c["path"] for c in parse_changes(state["code"])}
            originals = {p: t for p, t in state.get("repo_files", {}).items() if p in changed}
            prompt += repo_block(mode, state.get("repo_summary", ""), originals)
        reply = llm.invoke([SystemMessage(system), HumanMessage(prompt)], config=config)
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
        check = check_code(
            state["code"], state.get("repo_mode", ""), get_settings().code_check,
            state.get("repo_files", {}),
        )
        result = subgraph.invoke(
            {
                "check_status": check["status"],
                "check_report": check["report"],
                "spec": state["spec"],
                "code": state["code"],
                "repo_mode": state.get("repo_mode", ""),
                "repo_summary": state.get("repo_summary", ""),
                "repo_files": state.get("repo_files", {}),
            },
            config,
        )
        iteration = state.get("iteration", 1)
        review_text, verdict = result["review"], result["verdict"]
        if check["status"] == "failed":  # code that does not run is never approved
            verdict = "changes_requested"
            review_text = f"## Automatic code check: FAILED\n```\n{check['report']}\n```\n\n{review_text}"
            review_text = _VERDICT_RE.sub("VERDICT: CHANGES_REQUESTED", review_text)
        return {
            "review": review_text,
            "verdict": verdict,
            "check_status": check["status"],
            "check_report": check["report"],
            "versions": [
                {
                    "kind": "review",
                    "iteration": iteration,
                    "verdict": verdict,
                    "text": review_text,
                }
            ],
            "history": [
                make_event(
                    NAME, "review_written", f"attempt {iteration}, verdict={verdict}, check={check['status']}"
                )
            ],
        }

    return reviewer_node
