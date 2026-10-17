"""Streamlit UI for the AI software team.

    streamlit run app.py
"""
from __future__ import annotations

import time

import streamlit as st

from main import describe_outcome, format_event
from software_team.config import get_settings
from software_team.graph import build_team_graph, recursion_limit_for
from software_team.observability import finish_tracing, trace_config, tracing_enabled
from software_team.state import initial_state
from software_team.tracking import PriceBook, UsageTracker
from ui_style import CSS, HERO, cards_html, pipeline_html, verdict_html

st.set_page_config(page_title="AI Software Team", page_icon="🧑‍💻", layout="wide")

settings = get_settings()


def money(cost: float | None) -> str:
    return "n/a" if cost is None else f"${cost:.5f}"


def run_team_live(request: str, max_iterations: int, log) -> dict:
    """Run the graph, writing each new history event to `log`. Returns the result dict."""
    graph = build_team_graph()  # raises RuntimeError if GROQ_API_KEY is missing
    tracker = UsageTracker(PriceBook(online=settings.price_lookup))
    final_state, seen, started = None, 0, time.time()
    try:
        for state in graph.stream(
            initial_state(request, max_iterations),
            {
                "recursion_limit": recursion_limit_for(max_iterations),
                "callbacks": [tracker],
                **trace_config(request, max_iterations, settings),
            },
            stream_mode="values",
        ):
            for event in state["history"][seen:]:
                log(format_event(event), event)
            seen = len(state["history"])
            final_state = state
    finally:
        finish_tracing()
    return {
        "state": final_state,
        "usage": tracker.summary(),
        "seconds": time.time() - started,
    }


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    max_iterations = st.slider(
        "Max developer attempts", 1, 5, settings.max_iterations,
        help="How many times the Reviewer may send the code back to the Developer.",
    )
    st.caption(f"Model: `{settings.model_name}`")
    st.caption("Tracing: " + ("on (LangSmith)" if tracing_enabled() else "off"))

st.markdown(CSS, unsafe_allow_html=True)
st.markdown(HERO, unsafe_allow_html=True)

request = st.text_area(
    "Feature request",
    placeholder="e.g. Add a password reset feature with email tokens that expire in 15 minutes",
    height=110,
)
run = st.button("Run the team", type="primary", disabled=not request.strip())

# ---------------------------------------------------------------- run
if run:
    st.session_state.pop("result", None)
    st.session_state.pop("error", None)
    pipe = st.empty()
    pipe.markdown(pipeline_html(None, set()), unsafe_allow_html=True)
    live = {"active": None, "done": set(), "attempt": 0, "lines": []}
    with st.expander("Live log", expanded=False):
        log_box = st.empty()

    def on_event(line: str, event: dict) -> None:
        if event["agent"] == "supervisor":
            nxt = event.get("next")
            live["active"] = nxt if nxt in ("product_manager", "developer", "reviewer") else None
            if nxt == "developer":
                live["attempt"] += 1
        else:
            live["done"].add(event["agent"])
        live["done"].discard(live["active"])
        live["lines"].append(line)
        pipe.markdown(
            pipeline_html(live["active"], live["done"], live["attempt"]), unsafe_allow_html=True
        )
        log_box.code("\n".join(live["lines"]), language=None)

    try:
        st.session_state["result"] = run_team_live(request.strip(), max_iterations, on_event)
        pipe.markdown(
            pipeline_html(None, {"product_manager", "developer", "reviewer"}, live["attempt"]),
            unsafe_allow_html=True,
        )
    except Exception as exc:  # missing key, API error, cut-off output, ...
        st.session_state["error"] = str(exc)
        pipe.empty()

if "error" in st.session_state:
    st.error(st.session_state["error"])

# ---------------------------------------------------------------- results
result = st.session_state.get("result")
if result:
    state, usage = result["state"], result["usage"]
    total = usage["total"]

    st.markdown(
        verdict_html(
            state["outcome"] == "approved",
            f"Reviewer verdict: {state['verdict']} - {describe_outcome(state)}",
        ),
        unsafe_allow_html=True,
    )
    st.markdown(
        cards_html(
            [
                ("Total cost", money(total["cost_usd"])),
                ("Input tokens", f"{total['input_tokens']:,}"),
                ("Output tokens", f"{total['output_tokens']:,}"),
                ("LLM calls", str(total["calls"])),
                ("Time", f"{result['seconds']:.1f}s"),
            ]
        ),
        unsafe_allow_html=True,
    )

    tab_code, tab_spec, tab_review, tab_usage, tab_log = st.tabs(
        ["Code", "Spec", "Review", "Tokens & cost", "Run log"]
    )

    with tab_code:
        st.markdown(state["code"])
        st.download_button("Download code.md", state["code"], file_name="code.md")
        rounds = sorted({v["iteration"] for v in state["versions"]})
        if len(rounds) > 1:
            with st.expander("Earlier attempts and their reviews"):
                pick = st.selectbox("Attempt", rounds, index=0)
                for v in state["versions"]:
                    if v["iteration"] == pick:
                        st.markdown(f"**{v['kind'].title()} (attempt {pick})**")
                        st.markdown(v["text"])

    with tab_spec:
        st.markdown(state["spec"])

    with tab_review:
        st.markdown(state["review"])

    with tab_usage:
        rows = [
            {
                "Agent": name,
                "Calls": s["calls"],
                "Input tokens": s["input_tokens"],
                "Output tokens": s["output_tokens"],
                "Thinking tokens": s["reasoning_tokens"],
                "Cost (USD)": money(s["cost_usd"]),
            }
            for name, s in [*usage["agents"].items(), ("TOTAL", total)]
        ]
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption("Output tokens include thinking tokens. Prices in USD per 1M tokens:")
        for model, p in usage["prices"].items():
            if p is None:
                st.write(f"`{model}`: price unknown")
            else:
                st.write(
                    f"`{model}`: ${p['input_per_m']:g} in / ${p['output_per_m']:g} out "
                    f"- source: {p['source']}"
                )

    with tab_log:
        for event in state["history"]:
            st.text(format_event(event))