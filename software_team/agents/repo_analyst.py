"""Repo Analyst agent = a small sub-graph:  select files -> read them -> summarize.

Only used in GitHub repo mode. `select` and `summarize` are LLM calls; `read` is plain
Python that fetches the chosen files from GitHub (with the size limits from the settings).
"""
from __future__ import annotations

from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from ..llm import content_text, require_text
from ..prompts import REPO_SELECT_SYSTEM, REPO_SUMMARY_SYSTEM
from ..repo_context import RepoHandle, format_files, format_tree, parse_selection
from ..state import TeamState, make_event

NAME = "repo_analyst"


class AnalystState(TypedDict, total=False):
    request: str
    chosen: list
    files: dict
    summary: str


def build_repo_analyst_graph(llm, repo: RepoHandle):
    def select(state: AnalystState, config: RunnableConfig) -> dict:
        prompt = f"Feature request:\n{state['request']}\n\nFiles in the repository:\n{format_tree(repo.paths)}"
        reply = llm.invoke(
            [SystemMessage(REPO_SELECT_SYSTEM.format(limit=repo.max_files)), HumanMessage(prompt)],
            config=config,
        )
        chosen = parse_selection(content_text(reply), set(repo.paths), repo.max_files)
        return {"chosen": chosen}

    def read(state: AnalystState) -> dict:
        return {"files": repo.read(state.get("chosen", []))}

    def summarize(state: AnalystState, config: RunnableConfig) -> dict:
        prompt = f"Feature request:\n{state['request']}\n\nFiles:\n{format_files(state['files'])}"
        reply = llm.invoke(
            [SystemMessage(REPO_SUMMARY_SYSTEM), HumanMessage(prompt)], config=config
        )
        return {"summary": require_text(content_text(reply), "repo summary")}

    graph = StateGraph(AnalystState)
    graph.add_node("select", select)
    graph.add_node("read", read)
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "select")
    graph.add_edge("select", "read")
    graph.add_edge("read", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile()


def make_repo_analyst_node(llm, repo: RepoHandle):
    subgraph = build_repo_analyst_graph(llm, repo)

    def repo_analyst_node(state: TeamState, config: RunnableConfig) -> dict:
        result = subgraph.invoke({"request": state["request"]}, config)
        files = result["files"]
        return {
            "repo_summary": result["summary"],
            "repo_files": files,
            "history": [make_event(NAME, "repo_read", f"{len(files)} files read")],
        }

    return repo_analyst_node
