"""The shared state: the "whiteboard" every agent reads from and writes to."""
from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from .config import DEFAULT_MAX_ITERATIONS


class TeamState(TypedDict, total=False):
    request: str  # the feature request from the user
    spec: str  # written by the Product Manager
    code: str  # the Developer's latest version
    review: str  # the Reviewer's verdict text on the latest code ("" = not reviewed yet)
    verdict: str  # "pending" | "approved" | "changes_requested"
    next_agent: str  # set by the Supervisor, read by the router
    iteration: int  # how many times the Developer has written code so far
    max_iterations: int  # the cap: the Developer never writes more versions than this
    outcome: str  # set when the run ends: "approved" | "max_iterations_reached"
    # GitHub repo mode only (all empty in Quick mode):
    repo_mode: str  # "" = Quick mode | "existing" = improve a repo | "empty" = build in an empty repo
    repo_summary: str  # written by the Repo Analyst
    repo_files: dict[str, str]  # path -> current content of the files the analyst selected
    # Append-only lists. `operator.add` tells LangGraph to *append* new entries
    # instead of overwriting the list.
    versions: Annotated[list[dict[str, Any]], operator.add]  # every code/review version
    history: Annotated[list[dict[str, Any]], operator.add]  # log of what happened


def initial_state(
    request: str, max_iterations: int = DEFAULT_MAX_ITERATIONS, repo_mode: str = ""
) -> TeamState:
    return {
        "request": request,
        "spec": "",
        "code": "",
        "review": "",
        "verdict": "pending",
        "next_agent": "",
        "iteration": 0,
        "max_iterations": max_iterations,
        "outcome": "",
        "repo_mode": repo_mode,
        "repo_summary": "",
        "repo_files": {},
        "versions": [],
        "history": [],
    }


def make_event(agent: str, action: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    """One entry for the history log."""
    return {
        "agent": agent,
        "action": action,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **extra,
    }
