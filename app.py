# """Streamlit UI for the AI software team.

#     streamlit run app.py

# Two modes:
#   Quick        - type a request, get code (no repo involved)
#   GitHub repo  - connect GitHub, pick a repo, the team reads it / builds in it, you preview the
#                  file changes and open a pull request
# """
# from __future__ import annotations

# import time

# import streamlit as st

# from main import describe_outcome, format_event
# from software_team.config import get_settings
# from software_team.github_client import (
#     GitHubClient,
#     GitHubError,
#     authorize_url,
#     exchange_code,
#     parse_repo_url,
#     sign_state,
#     verify_state,
# )
# from software_team.graph import build_team_graph, recursion_limit_for
# from software_team.observability import finish_tracing, trace_config, tracing_enabled
# from software_team.repo_context import RepoHandle, parse_changes, unified_diff
# from software_team.state import initial_state
# from software_team.tracking import PriceBook, UsageTracker
# from ui_style import CSS, HERO, cards_html, github_button_html, pipeline_html, verdict_html

# st.set_page_config(page_title="AI Software Team", page_icon="🧑‍💻", layout="wide")

# settings = get_settings()
# QUICK, REPO = "Quick (no repo)", "GitHub repo"
# PICK_LINK = "- paste a link instead -"


# def money(cost: float | None) -> str:
#     return "n/a" if cost is None else f"${cost:.5f}"


# # ---------------------------------------------------------------- GitHub login
# def handle_login_callback() -> None:
#     """GitHub sends the user back here with ?code=...&state=... after they approve."""
#     params = st.query_params
#     if "code" not in params or "state" not in params:
#         return
#     code, state_value = params["code"], params["state"]
#     st.query_params.clear()  # a code can be used once; don't leave it in the address bar
#     if not verify_state(state_value, settings.github_client_secret):
#         st.session_state["gh_error"] = "The login link expired or was invalid. Please try again."
#         return
#     try:
#         st.session_state["gh_token"] = exchange_code(
#             settings.github_client_id, settings.github_client_secret, code, settings.app_url
#         )
#         st.session_state.pop("gh_error", None)
#     except GitHubError as exc:
#         st.session_state["gh_error"] = str(exc)


# def disconnect_github() -> None:
#     for key in ("gh_token", "gh_user", "gh_repos", "gh_info", "publish"):
#         st.session_state.pop(key, None)


# def github_panel() -> dict | None:
#     """Login + repo + branch pickers. Returns the chosen target, or None if not ready."""
#     token = st.session_state.get("gh_token")
#     client = GitHubClient(token)

#     if st.session_state.get("gh_error"):
#         st.error(st.session_state["gh_error"])

#     if not token:
#         if settings.github_client_id and settings.github_client_secret:
#             url = authorize_url(
#                 settings.github_client_id, settings.app_url, sign_state(settings.github_client_secret)
#             )
#             st.markdown(github_button_html(url), unsafe_allow_html=True)
#             st.caption(
#                 "Connect to pick your own repos and to open pull requests. "
#                 "Without it you can still read a public repo, but not publish."
#             )
#         else:
#             st.info(
#                 "GitHub login isn't set up on this server (GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET). "
#                 "You can still read a public repo; publishing needs the login."
#             )
#     else:
#         if "gh_user" not in st.session_state:
#             try:
#                 st.session_state["gh_user"] = client.current_user()
#             except GitHubError as exc:
#                 disconnect_github()
#                 st.session_state["gh_error"] = str(exc)
#                 st.rerun()
#         left, right = st.columns([4, 1])
#         left.success(f"Connected to GitHub as **{st.session_state['gh_user']}**")
#         right.button("Disconnect", on_click=disconnect_github)

#     options = [PICK_LINK]
#     if token:
#         if "gh_repos" not in st.session_state:
#             try:
#                 st.session_state["gh_repos"] = client.list_repos()
#             except GitHubError as exc:
#                 st.session_state["gh_repos"] = []
#                 st.warning(str(exc))
#         options += st.session_state["gh_repos"]
#     choice = st.selectbox("Your repositories", options) if token else PICK_LINK
#     pasted = st.text_input(
#         "Repository link", placeholder="https://github.com/owner/repo",
#         disabled=choice != PICK_LINK,
#     )
#     if token and settings.github_app_slug:
#         st.caption(
#             f"Don't see your repo? [Install the app on it]"
#             f"(https://github.com/apps/{settings.github_app_slug}/installations/new)"
#         )

#     target_text = pasted if choice == PICK_LINK else choice
#     if not target_text.strip():
#         return None
#     try:
#         owner, repo = parse_repo_url(target_text)
#         cache = st.session_state.setdefault("gh_info", {})
#         key = f"{owner}/{repo}"
#         if key not in cache:
#             info = client.get_repo(owner, repo)
#             branches = client.list_branches(owner, repo)
#             cache[key] = {"info": info, "branches": branches}
#         info, branches = cache[key]["info"], cache[key]["branches"]
#     except GitHubError as exc:
#         st.error(str(exc))
#         return None

#     empty = not branches
#     can_push = bool(token) and bool(info.get("permissions", {}).get("push"))
#     if empty:
#         st.caption("This repository is empty: the team will create all the files from your request.")
#         branch = info.get("default_branch", "main")
#     else:
#         default = info.get("default_branch", branches[0])
#         branch = st.selectbox(
#             "Base branch", branches, index=branches.index(default) if default in branches else 0
#         )
#     if token and not can_push:
#         st.warning("The app can't write to this repo yet (install it on the repo). You can still preview.")
#     return {
#         "client": client, "owner": owner, "repo": repo, "branch": branch,
#         "empty": empty, "can_push": can_push,
#     }


# # ---------------------------------------------------------------- running the team
# def run_team_live(request: str, max_iterations: int, log, target: dict | None = None) -> dict:
#     """Run the graph, writing each new history event to `log`. Returns the result dict."""
#     handle, mode = None, ""
#     if target:
#         if target["empty"]:
#             mode = "empty"
#         else:
#             mode = "existing"
#             handle = RepoHandle(
#                 target["client"], target["owner"], target["repo"], target["branch"],
#                 settings.repo_max_files, settings.repo_max_file_chars, settings.repo_max_context_chars,
#             )
#             handle.load_tree()
#             if not handle.paths:
#                 raise GitHubError("No readable source files found in this repo (or they are too big).")

#     graph = build_team_graph(repo=handle)  # raises RuntimeError if GROQ_API_KEY is missing
#     tracker = UsageTracker(PriceBook(online=settings.price_lookup))
#     final_state, seen, started = None, 0, time.time()
#     try:
#         for state in graph.stream(
#             initial_state(request, max_iterations, repo_mode=mode),
#             {
#                 "recursion_limit": recursion_limit_for(max_iterations),
#                 "callbacks": [tracker],
#                 **trace_config(request, max_iterations, settings),
#             },
#             stream_mode="values",
#         ):
#             for event in state["history"][seen:]:
#                 log(format_event(event), event)
#             seen = len(state["history"])
#             final_state = state
#     finally:
#         finish_tracing()
#     repo_info = None
#     if target:
#         repo_info = {
#             "owner": target["owner"], "repo": target["repo"], "branch": target["branch"],
#             "empty": target["empty"], "can_push": target["can_push"],
#             "paths": handle.paths if handle else [],
#         }
#     return {
#         "state": final_state,
#         "usage": tracker.summary(),
#         "seconds": time.time() - started,
#         "repo": repo_info,
#     }


# # ---------------------------------------------------------------- page
# handle_login_callback()

# with st.sidebar:
#     st.header("Settings")
#     mode_choice = st.radio("Mode", [QUICK, REPO], help="Quick = just code. GitHub repo = work inside a repository.")
#     max_iterations = st.slider(
#         "Max developer attempts", 1, 5, settings.max_iterations,
#         help="How many times the Reviewer may send the code back to the Developer.",
#     )
#     st.caption(f"Model: `{settings.model_name}`")
#     st.caption("Tracing: " + ("on (LangSmith)" if tracing_enabled() else "off"))
#     if mode_choice == REPO:
#         st.caption("In repo mode the files the team reads are sent to Groq.")
#         if tracing_enabled():
#             st.warning("Tracing is on: repo code will also be stored in LangSmith.")

# st.markdown(CSS, unsafe_allow_html=True)
# st.markdown(HERO, unsafe_allow_html=True)

# target = None
# if mode_choice == REPO:
#     with st.container(border=True):
#         target = github_panel()

# request = st.text_area(
#     "Feature request",
#     placeholder=(
#         "e.g. Add a /health endpoint that returns the app version"
#         if mode_choice == REPO
#         else "e.g. Add a password reset feature with email tokens that expire in 15 minutes"
#     ),
#     height=110,
# )
# run = st.button(
#     "Run the team", type="primary",
#     disabled=not request.strip() or (mode_choice == REPO and target is None),
# )

# # ---------------------------------------------------------------- run
# if run:
#     for key in ("result", "error", "publish"):
#         st.session_state.pop(key, None)
#     is_existing = bool(target and not target["empty"])
#     pipe = st.empty()
#     pipe.markdown(pipeline_html(None, set(), repo=is_existing), unsafe_allow_html=True)
#     live = {"active": "repo_analyst" if is_existing else None, "done": set(), "attempt": 0, "lines": []}
#     pipe.markdown(pipeline_html(live["active"], set(), repo=is_existing), unsafe_allow_html=True)
#     with st.expander("Live log", expanded=False):
#         log_box = st.empty()

#     def on_event(line: str, event: dict) -> None:
#         if event["agent"] == "supervisor":
#             nxt = event.get("next")
#             live["active"] = nxt if nxt in ("product_manager", "developer", "reviewer") else None
#             if nxt == "developer":
#                 live["attempt"] += 1
#         else:
#             live["done"].add(event["agent"])
#         live["done"].discard(live["active"])
#         live["lines"].append(line)
#         pipe.markdown(
#             pipeline_html(live["active"], live["done"], live["attempt"], repo=is_existing),
#             unsafe_allow_html=True,
#         )
#         log_box.code("\n".join(live["lines"]), language=None)

#     try:
#         st.session_state["result"] = run_team_live(
#             request.strip(), max_iterations, on_event, target if mode_choice == REPO else None
#         )
#         pipe.markdown(
#             pipeline_html(
#                 None, {"repo_analyst", "product_manager", "developer", "reviewer"},
#                 live["attempt"], repo=is_existing,
#             ),
#             unsafe_allow_html=True,
#         )
#     except Exception as exc:  # missing key, API error, GitHub error, cut-off output, ...
#         st.session_state["error"] = str(exc)
#         pipe.empty()

# if "error" in st.session_state:
#     st.error(st.session_state["error"])


# # ---------------------------------------------------------------- repo changes tab
# def show_changes(state: dict, repo_info: dict) -> None:
#     """File-by-file preview of what the Developer wants to change, plus the publish button."""
#     originals = state.get("repo_files", {})
#     existing = set(repo_info["paths"])
#     files = parse_changes(state["code"])
#     if not files:
#         st.warning("The Developer's answer had no recognizable files. Raw output:")
#         st.markdown(state["code"])
#         return

#     st.caption("Tick the files to include. Nothing is written until you click the button below.")
#     chosen = []
#     for f in files:
#         path, content = f["path"], f["content"]
#         if path in originals:
#             kind, icon, include = "modified", "✏️", True
#         elif path in existing:
#             kind, icon, include = "overwrites a file the team did not read", "⚠️", False
#         else:
#             kind, icon, include = "new", "🆕", True
#         with st.expander(f"{icon} {path}  ·  {kind}", expanded=len(files) <= 3):
#             if st.checkbox("Include in the pull request", value=include, key=f"inc_{path}"):
#                 chosen.append(f)
#             if path in originals:
#                 st.code(unified_diff(originals[path], content, path) or "(no changes)", language="diff")
#             else:
#                 st.code(content, language=path.rsplit(".", 1)[-1] if "." in path else None)

#     st.download_button("Download raw output (code.md)", state["code"], file_name="code.md")
#     publish = st.session_state.get("publish")
#     if publish:
#         label = "Open the pull request" if publish["kind"] == "pr" else "View the commit"
#         st.success("Done! " + ("Review it on GitHub, then merge." if publish["kind"] == "pr" else "Files committed."))
#         st.link_button(label, publish["url"])
#         return

#     title = st.text_input("Title", value=f"AI Team: {state['request'][:60]}")
#     action = "Commit to the repository" if repo_info["empty"] else "Create pull request"
#     if repo_info["empty"]:
#         st.caption("The repo is empty, so the first files are committed straight to its default branch.")
#     token = st.session_state.get("gh_token")
#     ready = bool(chosen) and bool(token) and repo_info["can_push"]
#     if st.button(action, type="primary", disabled=not ready):
#         body = (
#             f"Generated by AI Software Team.\n\n**Request:** {state['request']}\n\n"
#             f"**Reviewer verdict:** {state['verdict']} ({describe_outcome(state)})\n\n"
#             "The code was not run or tested. Please review carefully before merging."
#         )
#         with st.spinner("Writing to GitHub..."):
#             try:
#                 st.session_state["publish"] = GitHubClient(token).publish(
#                     repo_info["owner"], repo_info["repo"], repo_info["branch"],
#                     chosen, title, body, empty=repo_info["empty"],
#                 )
#                 st.rerun()
#             except GitHubError as exc:
#                 st.error(str(exc))
#     if not ready:
#         st.caption("Connect GitHub with write access to the repo and tick at least one file to publish.")


# # ---------------------------------------------------------------- results
# result = st.session_state.get("result")
# if result:
#     state, usage, repo_info = result["state"], result["usage"], result.get("repo")
#     total = usage["total"]

#     st.markdown(
#         verdict_html(
#             state["outcome"] == "approved",
#             f"Reviewer verdict: {state['verdict']} - {describe_outcome(state)}",
#         ),
#         unsafe_allow_html=True,
#     )
#     st.markdown(
#         cards_html(
#             [
#                 ("Total cost", money(total["cost_usd"])),
#                 ("Input tokens", f"{total['input_tokens']:,}"),
#                 ("Output tokens", f"{total['output_tokens']:,}"),
#                 ("LLM calls", str(total["calls"])),
#                 ("Time", f"{result['seconds']:.1f}s"),
#             ]
#         ),
#         unsafe_allow_html=True,
#     )

#     tab_code, tab_spec, tab_review, tab_usage, tab_log = st.tabs(
#         ["Changes" if repo_info else "Code", "Spec", "Review", "Tokens & cost", "Run log"]
#     )

#     with tab_code:
#         if repo_info:
#             show_changes(state, repo_info)
#         else:
#             st.markdown(state["code"])
#             st.download_button("Download code.md", state["code"], file_name="code.md")
#         rounds = sorted({v["iteration"] for v in state["versions"]})
#         if len(rounds) > 1:
#             with st.expander("Earlier attempts and their reviews"):
#                 pick = st.selectbox("Attempt", rounds, index=0)
#                 for v in state["versions"]:
#                     if v["iteration"] == pick:
#                         st.markdown(f"**{v['kind'].title()} (attempt {pick})**")
#                         st.markdown(v["text"])

#     with tab_spec:
#         if repo_info and state.get("repo_summary"):
#             with st.expander("What the Repo Analyst found in your repo"):
#                 st.markdown(state["repo_summary"])
#         st.markdown(state["spec"])

#     with tab_review:
#         st.markdown(state["review"])

#     with tab_usage:
#         rows = [
#             {
#                 "Agent": name,
#                 "Calls": s["calls"],
#                 "Input tokens": s["input_tokens"],
#                 "Output tokens": s["output_tokens"],
#                 "Thinking tokens": s["reasoning_tokens"],
#                 "Cost (USD)": money(s["cost_usd"]),
#             }
#             for name, s in [*usage["agents"].items(), ("TOTAL", total)]
#         ]
#         st.dataframe(rows, hide_index=True, use_container_width=True)
#         st.caption("Output tokens include thinking tokens. Prices in USD per 1M tokens:")
#         for model, p in usage["prices"].items():
#             if p is None:
#                 st.write(f"`{model}`: price unknown")
#             else:
#                 st.write(
#                     f"`{model}`: ${p['input_per_m']:g} in / ${p['output_per_m']:g} out "
#                     f"- source: {p['source']}"
#                 )

#     with tab_log:
#         for event in state["history"]:
#             st.text(format_event(event))




"""Streamlit UI for the AI software team.

    streamlit run app.py

Two modes:
  Quick        - type a request, get code (no repo involved)
  GitHub repo  - connect GitHub, pick a repo, the team reads it / builds in it, you preview the
                 file changes and open a pull request
"""
from __future__ import annotations

import time

import streamlit as st

from main import describe_outcome, format_event
from software_team.config import get_settings
from software_team.github_client import (
    GitHubClient,
    GitHubError,
    authorize_url,
    exchange_code,
    parse_repo_url,
    sign_state,
    verify_state,
)
from software_team.graph import build_team_graph, recursion_limit_for
from software_team.observability import finish_tracing, trace_config, tracing_enabled
from software_team.repo_context import RepoHandle, parse_changes, unified_diff
from software_team.state import initial_state
from software_team.tracking import PriceBook, UsageTracker
from ui_style import CSS, HERO, cards_html, github_button_html, pipeline_html, verdict_html

st.set_page_config(page_title="AI Software Team", page_icon="🧑‍💻", layout="wide")

settings = get_settings()
QUICK, REPO = "Quick (no repo)", "GitHub repo"
PICK_LINK = "- paste a link instead -"


def money(cost: float | None) -> str:
    return "n/a" if cost is None else f"${cost:.5f}"


# ---------------------------------------------------------------- GitHub login
def handle_login_callback() -> None:
    """GitHub sends the user back here with ?code=...&state=... after they approve."""
    params = st.query_params
    if "code" not in params or "state" not in params:
        return
    code, state_value = params["code"], params["state"]
    st.query_params.clear()  # a code can be used once; don't leave it in the address bar
    if not verify_state(state_value, settings.github_client_secret):
        st.session_state["gh_error"] = "The login link expired or was invalid. Please try again."
        return
    try:
        st.session_state["gh_token"] = exchange_code(
            settings.github_client_id, settings.github_client_secret, code, settings.app_url
        )
        st.session_state.pop("gh_error", None)
    except GitHubError as exc:
        st.session_state["gh_error"] = str(exc)


def disconnect_github() -> None:
    for key in ("gh_token", "gh_user", "gh_repos", "gh_info", "publish"):
        st.session_state.pop(key, None)


def github_panel() -> dict | None:
    """Login + repo + branch pickers. Returns the chosen target, or None if not ready."""
    token = st.session_state.get("gh_token")
    client = GitHubClient(token)

    if st.session_state.get("gh_error"):
        st.error(st.session_state["gh_error"])

    if not token:
        if settings.github_client_id and settings.github_client_secret:
            url = authorize_url(
                settings.github_client_id, settings.app_url, sign_state(settings.github_client_secret)
            )
            st.markdown(github_button_html(url), unsafe_allow_html=True)
            st.caption(
                "Connect to pick your own repos and to open pull requests. "
                "Without it you can still read a public repo, but not publish."
            )
        else:
            st.info(
                "GitHub login isn't set up on this server (GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET). "
                "You can still read a public repo; publishing needs the login."
            )
    else:
        if "gh_user" not in st.session_state:
            try:
                st.session_state["gh_user"] = client.current_user()
            except GitHubError as exc:
                disconnect_github()
                st.session_state["gh_error"] = str(exc)
                st.rerun()
        left, right = st.columns([4, 1])
        left.success(f"Connected to GitHub as **{st.session_state['gh_user']}**")
        right.button("Disconnect", on_click=disconnect_github)

    options = [PICK_LINK]
    if token:
        if "gh_repos" not in st.session_state:
            try:
                st.session_state["gh_repos"] = client.list_repos()
            except GitHubError as exc:
                st.session_state["gh_repos"] = []
                st.warning(str(exc))
        options += st.session_state["gh_repos"]
    choice = st.selectbox("Your repositories", options) if token else PICK_LINK
    pasted = st.text_input(
        "Repository link", placeholder="https://github.com/owner/repo",
        disabled=choice != PICK_LINK,
    )
    if token and settings.github_app_slug:
        st.caption(
            f"Don't see your repo? [Install the app on it]"
            f"(https://github.com/apps/{settings.github_app_slug}/installations/new)"
        )

    target_text = pasted if choice == PICK_LINK else choice
    if not target_text.strip():
        return None
    try:
        owner, repo = parse_repo_url(target_text)
        cache = st.session_state.setdefault("gh_info", {})
        key = f"{owner}/{repo}"
        if key not in cache:
            info = client.get_repo(owner, repo)
            branches = client.list_branches(owner, repo)
            cache[key] = {"info": info, "branches": branches}
        info, branches = cache[key]["info"], cache[key]["branches"]
    except GitHubError as exc:
        st.error(str(exc))
        return None

    empty = not branches
    # GitHub App tokens often don't report "permissions"; only block when it says push is False.
    perms = info.get("permissions")
    can_push = bool(token) and (perms is None or bool(perms.get("push")))
    if empty:
        st.caption("This repository is empty: the team will create all the files from your request.")
        branch = info.get("default_branch", "main")
    else:
        default = info.get("default_branch", branches[0])
        branch = st.selectbox(
            "Base branch", branches, index=branches.index(default) if default in branches else 0
        )
    if token and not can_push:
        st.warning("The app can't write to this repo yet (install it on the repo). You can still preview.")
    return {
        "client": client, "owner": owner, "repo": repo, "branch": branch,
        "empty": empty, "can_push": can_push,
    }


# ---------------------------------------------------------------- running the team
def run_team_live(request: str, max_iterations: int, log, target: dict | None = None) -> dict:
    """Run the graph, writing each new history event to `log`. Returns the result dict."""
    handle, mode = None, ""
    if target:
        if target["empty"]:
            mode = "empty"
        else:
            mode = "existing"
            handle = RepoHandle(
                target["client"], target["owner"], target["repo"], target["branch"],
                settings.repo_max_files, settings.repo_max_file_chars, settings.repo_max_context_chars,
            )
            handle.load_tree()
            if not handle.paths:
                raise GitHubError("No readable source files found in this repo (or they are too big).")

    graph = build_team_graph(repo=handle)  # raises RuntimeError if GROQ_API_KEY is missing
    tracker = UsageTracker(PriceBook(online=settings.price_lookup))
    final_state, seen, started = None, 0, time.time()
    try:
        for state in graph.stream(
            initial_state(request, max_iterations, repo_mode=mode),
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
    repo_info = None
    if target:
        repo_info = {
            "owner": target["owner"], "repo": target["repo"], "branch": target["branch"],
            "empty": target["empty"], "can_push": target["can_push"],
            "paths": handle.paths if handle else [],
        }
    return {
        "state": final_state,
        "usage": tracker.summary(),
        "seconds": time.time() - started,
        "repo": repo_info,
    }


# ---------------------------------------------------------------- page
handle_login_callback()

with st.sidebar:
    st.header("Settings")
    mode_choice = st.radio("Mode", [QUICK, REPO], help="Quick = just code. GitHub repo = work inside a repository.")
    max_iterations = st.slider(
        "Max developer attempts", 1, 5, settings.max_iterations,
        help="How many times the Reviewer may send the code back to the Developer.",
    )
    st.caption(f"Model: `{settings.model_name}`")
    st.caption("Tracing: " + ("on (LangSmith)" if tracing_enabled() else "off"))
    if mode_choice == REPO:
        st.caption("In repo mode the files the team reads are sent to Groq.")
        if tracing_enabled():
            st.warning("Tracing is on: repo code will also be stored in LangSmith.")

st.markdown(CSS, unsafe_allow_html=True)
st.markdown(HERO, unsafe_allow_html=True)

target = None
if mode_choice == REPO:
    with st.container(border=True):
        target = github_panel()

request = st.text_area(
    "Feature request",
    placeholder=(
        "e.g. Add a /health endpoint that returns the app version"
        if mode_choice == REPO
        else "e.g. Add a password reset feature with email tokens that expire in 15 minutes"
    ),
    height=110,
)
run = st.button(
    "Run the team", type="primary",
    disabled=not request.strip() or (mode_choice == REPO and target is None),
)

# ---------------------------------------------------------------- run
if run:
    for key in ("result", "error", "publish"):
        st.session_state.pop(key, None)
    is_existing = bool(target and not target["empty"])
    pipe = st.empty()
    pipe.markdown(pipeline_html(None, set(), repo=is_existing), unsafe_allow_html=True)
    live = {"active": "repo_analyst" if is_existing else None, "done": set(), "attempt": 0, "lines": []}
    pipe.markdown(pipeline_html(live["active"], set(), repo=is_existing), unsafe_allow_html=True)
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
            pipeline_html(live["active"], live["done"], live["attempt"], repo=is_existing),
            unsafe_allow_html=True,
        )
        log_box.code("\n".join(live["lines"]), language=None)

    try:
        st.session_state["result"] = run_team_live(
            request.strip(), max_iterations, on_event, target if mode_choice == REPO else None
        )
        pipe.markdown(
            pipeline_html(
                None, {"repo_analyst", "product_manager", "developer", "reviewer"},
                live["attempt"], repo=is_existing,
            ),
            unsafe_allow_html=True,
        )
    except Exception as exc:  # missing key, API error, GitHub error, cut-off output, ...
        st.session_state["error"] = str(exc)
        pipe.empty()

if "error" in st.session_state:
    st.error(st.session_state["error"])


# ---------------------------------------------------------------- repo changes tab
def show_changes(state: dict, repo_info: dict) -> None:
    """File-by-file preview of what the Developer wants to change, plus the publish button."""
    originals = state.get("repo_files", {})
    existing = set(repo_info["paths"])
    files = parse_changes(state["code"])
    if not files:
        st.warning("The Developer's answer had no recognizable files. Raw output:")
        st.markdown(state["code"])
        return

    st.caption("Tick the files to include. Nothing is written until you click the button below.")
    chosen = []
    for f in files:
        path, content = f["path"], f["content"]
        if path in originals:
            kind, icon, include = "modified", "✏️", True
        elif path in existing:
            kind, icon, include = "overwrites a file the team did not read", "⚠️", False
        else:
            kind, icon, include = "new", "🆕", True
        with st.expander(f"{icon} {path}  ·  {kind}", expanded=len(files) <= 3):
            if st.checkbox("Include in the pull request", value=include, key=f"inc_{path}"):
                chosen.append(f)
            if path in originals:
                st.code(unified_diff(originals[path], content, path) or "(no changes)", language="diff")
            else:
                st.code(content, language=path.rsplit(".", 1)[-1] if "." in path else None)

    st.download_button("Download raw output (code.md)", state["code"], file_name="code.md")
    publish = st.session_state.get("publish")
    if publish:
        label = "Open the pull request" if publish["kind"] == "pr" else "View the commit"
        st.success("Done! " + ("Review it on GitHub, then merge." if publish["kind"] == "pr" else "Files committed."))
        st.link_button(label, publish["url"])
        return

    title = st.text_input("Title", value=f"AI Team: {state['request'][:60]}")
    action = "Commit to the repository" if repo_info["empty"] else "Create pull request"
    if repo_info["empty"]:
        st.caption("The repo is empty, so the first files are committed straight to its default branch.")
    token = st.session_state.get("gh_token")
    ready = bool(chosen) and bool(token) and repo_info["can_push"]
    if st.button(action, type="primary", disabled=not ready):
        body = (
            f"Generated by AI Software Team.\n\n**Request:** {state['request']}\n\n"
            f"**Reviewer verdict:** {state['verdict']} ({describe_outcome(state)})\n\n"
            "The code was not run or tested. Please review carefully before merging."
        )
        with st.spinner("Writing to GitHub..."):
            try:
                st.session_state["publish"] = GitHubClient(token).publish(
                    repo_info["owner"], repo_info["repo"], repo_info["branch"],
                    chosen, title, body, empty=repo_info["empty"],
                )
                st.rerun()
            except GitHubError as exc:
                st.error(str(exc))
    if not ready:
        why = (
            "Connect GitHub first (button at the top)." if not token
            else "Tick at least one file above." if not chosen
            else "GitHub says you can't push to this repo."
        )
        st.caption(f"Button is disabled: {why}")


# ---------------------------------------------------------------- results
result = st.session_state.get("result")
if result:
    state, usage, repo_info = result["state"], result["usage"], result.get("repo")
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
        ["Changes" if repo_info else "Code", "Spec", "Review", "Tokens & cost", "Run log"]
    )

    with tab_code:
        if repo_info:
            show_changes(state, repo_info)
        else:
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
        if repo_info and state.get("repo_summary"):
            with st.expander("What the Repo Analyst found in your repo"):
                st.markdown(state["repo_summary"])
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