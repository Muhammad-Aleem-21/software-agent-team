"""Run traces in LangSmith (Deliverable 4).

Tracing itself needs no code: with LANGSMITH_TRACING=true and LANGSMITH_API_KEY in .env,
LangGraph/LangChain send every step (supervisor, agents, their sub-graph steps, each
model call with prompt, answer, tokens and time) to LangSmith. This module only
(1) gives the top-level run a findable name, tags and metadata, and
(2) waits for the upload to finish and tells you where to look.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

_TRUE = ("true", "1", "yes", "on")


def tracing_enabled() -> bool:
    flag = os.getenv("LANGSMITH_TRACING") or os.getenv("LANGCHAIN_TRACING_V2") or ""
    return flag.strip().lower() in _TRUE


def project_name() -> str:
    return os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or "default"


def trace_config(request: str, max_iterations: int, settings) -> dict[str, Any]:
    """Name, tags and metadata for the top-level run. Safe to pass when tracing is off.

    Metadata is inherited by every step below it, so each model call in the trace can be
    filtered by request, model or setting (graph.py also tags each call with its agent).
    """
    stamp = datetime.now().strftime("%H:%M:%S")
    return {
        "run_name": f"software-team {stamp}: {request[:50]}",
        "tags": ["software-team", f"supervisor:{settings.supervisor_mode}"],
        "metadata": {
            "request": request[:300],
            "max_iterations": max_iterations,
            "supervisor_mode": settings.supervisor_mode,
            "models": dict(settings.agent_models),
            "reasoning_effort": settings.reasoning_effort,
        },
    }


def finish_tracing() -> str:
    """Wait until queued trace data has been sent, then say where to find the trace."""
    if not tracing_enabled():
        return "Tracing is off. Set LANGSMITH_TRACING=true and LANGSMITH_API_KEY in .env to record traces."
    if not (os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")):
        return "LANGSMITH_TRACING is on but LANGSMITH_API_KEY is missing, so nothing was recorded."
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    except Exception as exc:  # never fail a finished run over tracing
        return f"Tracing is on, but waiting for the upload failed ({type(exc).__name__})."
    return (
        f"Tracing was on: open smith.langchain.com > Tracing > project '{project_name()}' "
        "and look for the newest run (named 'software-team ...'). "
        "If it is missing, check the LangSmith warnings above and your API key."
    )