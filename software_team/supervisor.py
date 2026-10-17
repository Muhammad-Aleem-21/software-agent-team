"""The supervisor: looks at the shared state and decides who works next.

The LLM makes the decision, but a guardrail checks it. If the LLM picks something
illegal (e.g. "reviewer" before any code exists) or its answer can't be parsed, we
fall back to simple rules, so a confused model can never break the workflow.
"""
from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from .config import DEFAULT_MAX_ITERATIONS
from .llm import content_text
from .prompts import SUPERVISOR_SYSTEM
from .state import TeamState, make_event

NAME = "supervisor"
FINISH = "FINISH"
WORKERS = ("product_manager", "developer", "reviewer")


def needs_rework(state: TeamState) -> bool:
       """True when the Reviewer asked for changes AND the Developer still has attempts left.

       This is THE iteration cap. It lives in plain code (not in a prompt), so a
       confused LLM can never push the loop past the limit.
       """
       max_iterations = state.get("max_iterations", DEFAULT_MAX_ITERATIONS)
       return (
           state.get("verdict") == "changes_requested"
           and state.get("iteration", 0) < max_iterations
       )


def outcome_for(state: TeamState) -> str:
       """Why the run ended: the code was approved, or we hit the cap."""
       return "approved" if state.get("verdict") == "approved" else "max_iterations_reached"


def legal_moves(state: TeamState) -> list[str]:
       """The moves that make sense right now (also our rule-based fallback)."""
       if not state.get("spec"):
           return ["product_manager"]
       if not state.get("code"):
           return ["developer"]
       if not state.get("review"):
           return ["reviewer"]
       if needs_rework(state):
           return ["developer"]
       return [FINISH]


def describe_status(state: TeamState) -> str:
       max_iterations = state.get("max_iterations", DEFAULT_MAX_ITERATIONS)
       if state.get("code"):
           code_status = f"written (developer attempts used: {state.get('iteration', 0)} of {max_iterations})"
       else:
           code_status = "missing"
       if state.get("review"):
           review_status = f"written, verdict = {state.get('verdict')}"
       else:
           review_status = "missing"

       return (
           f"Feature request: {state.get('request', '')}\n\n"
           "Workspace status:\n"
           f"- specification: {'written' if state.get('spec') else 'missing'}\n"
           f"- code: {code_status}\n"
           f"- review: {review_status}\n\n"
           "Who works next?"
       )


def parse_decision(text: str) -> tuple[str | None, str]:
    """Pull {"next": ..., "reason": ...} out of the model's answer."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None, ""
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, ""
    choice = str(data.get("next", "")).strip()
    if choice.upper() == FINISH:
        choice = FINISH
    return choice or None, str(data.get("reason", "")).strip()


def make_supervisor_node(llm, mode: str = "llm"):
    def supervisor_node(state: TeamState, config: RunnableConfig) -> dict:
        legal = legal_moves(state)
        choice: str | None = None
        reason = ""
        source = "rules"

        if mode == "llm" and llm is not None:
            try:
                reply = llm.invoke(
                    [SystemMessage(SUPERVISOR_SYSTEM), HumanMessage(describe_status(state))],
                    config=config,
                )
                choice, reason = parse_decision(content_text(reply))
                source = "llm"
                if choice not in legal:
                    reason = f"LLM chose {choice!r}, which is not allowed now; using rules"
                    choice = None
            except Exception as exc:  # network error, bad output, ... -> don't crash the run
                reason = f"LLM routing failed ({type(exc).__name__}); using rules"

        if choice is None:
            choice = legal[0]
            source = "rules"
            reason = reason or "rule-based routing"

        update = {
               "next_agent": choice,
               "history": [make_event(NAME, "route", reason, next=choice, source=source)],
           }
        if choice == FINISH:
               update["outcome"] = outcome_for(state)
        return update

    return supervisor_node


def route_from_supervisor(state: TeamState) -> str:
    """Used by the conditional edge: just reads the supervisor's decision."""
    return state["next_agent"]
