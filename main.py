"""Command-line entry point.

    python main.py "Add a password reset feature"
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from software_team.config import get_settings
from software_team.graph import build_team_graph, recursion_limit_for
from software_team.observability import finish_tracing, trace_config
from software_team.state import initial_state
from software_team.tracking import PriceBook, UsageTracker

def format_event(event: dict) -> str:
    """One readable progress line per history entry (plain ASCII for Windows consoles)."""
    if event["agent"] == "supervisor":
        return f"[supervisor] -> {event['next']} ({event['source']}): {event['detail']}"
    return f"[{event['agent']}] {event['action']} ({event['detail']})"


def describe_outcome(state: dict) -> str:
       attempts = state["iteration"]
       if state["outcome"] == "approved":
           return f"approved after {attempts} attempt(s)"
       return (
           f"stopped at the cap of {state['max_iterations']} attempt(s) "
           "with unresolved review comments"
       )


def save_outputs(state: dict, output_dir: Path, usage: dict | None = None) -> Path:
       """Write the final spec/code/review, every version, and run.json into a fresh folder."""
       folder = output_dir / datetime.now().strftime("%Y%m%d-%H%M%S")
       folder.mkdir(parents=True, exist_ok=True)
       (folder / "spec.md").write_text(state["spec"], encoding="utf-8")
       (folder / "code.md").write_text(state["code"], encoding="utf-8")
       (folder / "review.md").write_text(state["review"], encoding="utf-8")
       # Every round is kept: code_v1.md, review_v1.md, code_v2.md, review_v2.md, ...
       for version in state["versions"]:
           name = f"{version['kind']}_v{version['iteration']}.md"
           (folder / name).write_text(version["text"], encoding="utf-8")
       run_info = {
           "request": state["request"],
           "verdict": state["verdict"],
           "outcome": state["outcome"],
           "iterations": state["iteration"],
           "max_iterations": state["max_iterations"],
           "history": state["history"],
            "usage": usage,
       }
       (folder / "run.json").write_text(json.dumps(run_info, indent=2), encoding="utf-8")
       return folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AI software team on a feature request.")
    parser.add_argument("request", help='The feature request, e.g. "Add a password reset feature"')
    parser.add_argument("--output-dir", default="outputs", help="Where to save results")
    parser.add_argument("--show", action="store_true", help="Print spec, code and review")
    parser.add_argument(
           "--max-iterations",
           type=int,
           default=None,
           help="Max versions the Developer may write (default: MAX_ITERATIONS from .env, else 3)",
    )
    args = parser.parse_args(argv)
    max_iterations = max(1, args.max_iterations or get_settings().max_iterations)

    try:
        graph = build_team_graph()
    except RuntimeError as exc:  # e.g. missing API key
        print(f"Error: {exc}")
        return 1

    print(f"Request: {args.request}  (max {max_iterations} developer attempt(s))\n")
    # counts tokens and cost per agent; prices are looked up online (cached) after the run
    tracker = UsageTracker(PriceBook(online=get_settings().price_lookup))
    final_state = None
    seen = 0
    try:
        # stream_mode="values" gives the full state after every step, so we can
        # print each new history entry as it happens.
        for state in graph.stream(
            initial_state(args.request, max_iterations),
            {
                "recursion_limit": recursion_limit_for(max_iterations),
                "callbacks": [tracker],
                **trace_config(args.request, max_iterations, get_settings()),
               },
            stream_mode="values",
        ):
            for event in state["history"][seen:]:
                print(format_event(event))
            seen = len(state["history"])
            final_state = state
    finally:  # also runs when a model call fails, so you still see what the run cost
        table = tracker.format_table()
        if table:
            print(f"\n{table}\n{tracker.format_prices()}")

    folder = save_outputs(final_state, Path(args.output_dir), tracker.summary())
    print(f"\nDone. Reviewer verdict: {final_state['verdict']}")
    print(f"Outcome: {describe_outcome(final_state)}")
    print(f"Saved to: {folder}")
    print(finish_tracing())
    if args.show:
        for title, key in (("SPEC", "spec"), ("CODE", "code"), ("REVIEW", "review")):
            print(f"\n{'=' * 20} {title} {'=' * 20}\n{final_state[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
