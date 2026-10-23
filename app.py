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
import streamlit.components.v1 as components

from main import describe_outcome, format_event
from software_team import auth, db
from software_team.config import get_settings
from software_team.errors import explain
from software_team.followup import build_followup, short_label
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
NEW_RUN, HISTORY = "New run", "History"


@st.cache_resource(show_spinner=False)
def init_database() -> str:
    """Create the tables once per server process. Returns "" or the error text."""
    try:
        db.init_db(settings.database_url)
        return ""
    except Exception as exc:  # the app still works without history
        return str(exc)


DB_ERROR = init_database()
DB_OK = not DB_ERROR
LOGIN_ON = bool(settings.github_client_id and settings.github_client_secret)
SECRET = settings.session_secret or settings.github_client_secret


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
        token = exchange_code(
            settings.github_client_id, settings.github_client_secret, code, settings.app_url
        )
        st.session_state["gh_token"] = token
        sign_in_user(token)
        st.session_state.pop("gh_error", None)
    except GitHubError as exc:
        st.session_state["gh_error"] = str(exc)


def sign_in_user(token: str) -> None:
    """The GitHub login is the account: save/update the user and remember them in a cookie."""
    info = GitHubClient(token).get_user()
    user = {"id": int(info["id"]), "login": info["login"], "avatar": info.get("avatar_url", "")}
    if DB_OK:
        try:
            db.upsert_user(user["id"], user["login"], user["avatar"])
        except Exception as exc:
            st.session_state["db_warn"] = f"Could not save your account: {exc}"
    st.session_state["user"] = user
    st.session_state["gh_user"] = user["login"]
    st.session_state["set_cookie"] = auth.sign_session(user["id"], SECRET)


def restore_session() -> None:
    """After a page refresh, sign the user in again from the cookie (once per browser session)."""
    if st.session_state.get("auth_checked"):
        return
    st.session_state["auth_checked"] = True
    if "user" in st.session_state or not DB_OK:
        return
    try:
        cookie = st.context.cookies.get(auth.COOKIE)
        user_id = auth.read_session(cookie, SECRET)
        found = db.get_user(user_id) if user_id else None
    except Exception:  # old Streamlit, cookies blocked, database problem: just sign in again
        return
    if found:
        st.session_state["user"] = found
        st.session_state["gh_user"] = found["login"]


def run_script(html: str) -> None:
    """Run a tiny browser script (st.iframe on new Streamlit, components.html on older ones)."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=1)
    else:
        components.html(html, height=0)


def emit_cookie_scripts() -> None:
    secure = settings.app_url.startswith("https")
    if "set_cookie" in st.session_state:
        run_script(auth.cookie_html(st.session_state.pop("set_cookie"), secure))
    if st.session_state.pop("clear_cookie", False):
        run_script(auth.cookie_html(None, secure))


def sign_out() -> None:
    for key in ("user", "gh_token", "gh_user", "gh_repos", "gh_info", "publish", "result", "error"):
        st.session_state.pop(key, None)
    st.session_state["clear_cookie"] = True


def clear_file_ticks() -> None:
    for key in [k for k in st.session_state if str(k).startswith("inc_")]:
        del st.session_state[key]


def open_run(user_id: int, thread_id: str) -> None:
    """Button callback on the History page: load a saved task (all its steps) into the results view."""
    steps = db.get_thread(user_id, thread_id)
    if not steps:
        st.session_state["error"] = "That run was not found."
        return
    for step in steps:
        step.pop("publish", None) if step is not steps[-1] else None
        if step.get("repo"):
            step["repo"]["can_push"] = True  # GitHub itself will refuse if the user can't push
    result = steps[-1]
    publish = result.pop("publish", None)
    clear_file_ticks()
    st.session_state.pop("error", None)
    st.session_state["result"] = result
    st.session_state["thread_prev"] = steps[:-1]
    st.session_state["thread_id"] = thread_id
    if publish:
        st.session_state["publish"] = publish
    else:
        st.session_state.pop("publish", None)
    st.session_state["page"] = NEW_RUN


def new_task() -> None:
    """Button callback: clear the current result and start with an empty page."""
    for key in ("result", "error", "error_details", "retry_job", "publish", "queued",
                "thread_id", "thread_prev"):
        st.session_state.pop(key, None)
    st.session_state["request_text"] = ""
    st.session_state["followup_text"] = ""
    clear_file_ticks()
    st.session_state["page"] = NEW_RUN


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
                "GitHub opens in a new tab. After you approve, continue in that new tab "
                "(you can close this one). Without connecting you can still read a public repo, "
                "but not publish."
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
restore_session()
emit_cookie_scripts()
st.markdown(CSS, unsafe_allow_html=True)
st.markdown(HERO, unsafe_allow_html=True)

user = st.session_state.get("user")
if LOGIN_ON and not user:  # sign-in page: nothing else is shown until the user signs in
    with st.container(border=True):
        st.subheader("Sign in to start")
        st.write(
            "Sign in with GitHub. Your runs are saved to your own private history "
            "and you can delete them at any time."
        )
        st.markdown(
            github_button_html(
                authorize_url(
                    settings.github_client_id, settings.app_url, sign_state(settings.github_client_secret)
                ),
                "Sign in with GitHub",
            ),
            unsafe_allow_html=True,
        )
        st.caption(
            "We store your GitHub name and the runs you make (your requests and the results). "
            "Your GitHub login token is never stored."
        )
    if st.session_state.get("gh_error"):
        st.error(st.session_state["gh_error"])
    st.stop()

quota = {"runs": 0, "cost_usd": 0.0, "tokens": 0}
limit_msg = ""  # set when today's limit is used up; blocks the Run buttons
if user and DB_OK:
    try:
        quota = db.usage_today(user["id"])
    except Exception:
        pass  # can't read the counter: don't block people because of it
    if settings.daily_run_limit and quota["runs"] >= settings.daily_run_limit:
        limit_msg = f"You have used all {settings.daily_run_limit} runs for today. The limit resets at 00:00 UTC."
    elif settings.daily_token_limit and quota["tokens"] >= settings.daily_token_limit:
        limit_msg = f"You have reached today's token limit ({settings.daily_token_limit:,}). It resets at 00:00 UTC."
    elif settings.daily_cost_limit_usd and quota["cost_usd"] >= settings.daily_cost_limit_usd:
        limit_msg = f"You have reached today's spending limit (${settings.daily_cost_limit_usd:g}). It resets at 00:00 UTC."

page = NEW_RUN
with st.sidebar:
    if user:
        if user.get("avatar"):
            st.image(user["avatar"], width=48)
        st.markdown(f"**{user['login']}**")
        st.button("Sign out", on_click=sign_out)
        if DB_OK:
            page = st.radio("Page", [NEW_RUN, HISTORY], key="page")
            if db.is_sqlite() and settings.app_url.startswith("https"):
                st.caption("History is stored temporarily. Set DATABASE_URL for permanent history.")
            with st.expander("Your data"):
                st.caption("Deletes all your saved runs and your account record.")
                sure = st.checkbox("Yes, delete everything", key="confirm_wipe")
                if st.button("Delete my data", disabled=not sure):
                    db.delete_user_data(user["id"])
                    sign_out()
                    st.rerun()
        else:
            st.caption("History is off: the database is not available.")
            with st.expander("Details"):
                st.code(DB_ERROR)
        if DB_OK and (settings.daily_run_limit or settings.daily_cost_limit_usd or settings.daily_token_limit):
            if settings.daily_run_limit:
                st.progress(
                    min(1.0, quota["runs"] / settings.daily_run_limit),
                    text=f"Runs today: {quota['runs']} / {settings.daily_run_limit}",
                )
            if settings.daily_token_limit:
                st.progress(
                    min(1.0, quota["tokens"] / settings.daily_token_limit),
                    text=f"Tokens today: {quota['tokens']:,} / {settings.daily_token_limit:,}",
                )
            if settings.daily_cost_limit_usd:
                st.caption(f"Spent today: ${quota['cost_usd']:.4f} of ${settings.daily_cost_limit_usd:g}")
        if st.session_state.get("db_warn"):
            st.warning(st.session_state["db_warn"])
        st.divider()
    if st.session_state.get("result"):
        st.button("＋ New task", on_click=new_task, key="new_task_side", use_container_width=True)
    st.header("Settings")
    mode_choice = st.radio("Mode", [QUICK, REPO], help="Quick = just code. GitHub repo = work inside a repository.")
    max_iterations = st.slider(
        "Max developer attempts", 1, 5, settings.max_iterations,
        help="How many times the Reviewer may send the code back to the Developer.",
    )
    st.caption(f"Model: `{settings.model_name}`")
    st.caption({"off": "Code check: off", "syntax": "Code check: syntax only",
                "run": "Code check: runs the code"}[settings.code_check])
    st.caption("Tracing: " + ("on (LangSmith)" if tracing_enabled() else "off"))
    if mode_choice == REPO:
        st.caption("In repo mode the files the team reads are sent to Groq.")
        if tracing_enabled():
            st.warning("Tracing is on: repo code will also be stored in LangSmith.")

if page == HISTORY:
    st.subheader("Your past runs")
    try:
        saved = db.list_threads(user["id"])
    except Exception as exc:
        saved = []
        st.error(f"Could not load your history: {exc}")
    if not saved:
        st.info("No saved runs yet. Run the team once and it will appear here.")
        st.stop()
    st.dataframe(
        [
            {
                "Date": r["created_at"].strftime("%Y-%m-%d %H:%M"),
                "Request": r["request"][:90],
                "Steps": r["steps"],
                "Repo": r["repo"] or "-",
                "Verdict": r["verdict"] or "-",
                "Total cost": money(r["cost_usd"]),
                "Published": "yes" if r["published"] else "",
            }
            for r in saved
        ],
        hide_index=True,
    )
    labels = {r["thread_id"]: f"{r['created_at']:%Y-%m-%d %H:%M} - {r['request'][:60]}" for r in saved}
    picked = st.selectbox("Pick a run", list(labels), format_func=labels.get)
    st.button("Open", type="primary", on_click=open_run, args=(user["id"], picked))
    steps = db.list_steps(user["id"], picked)
    if steps:
        step_labels = {
            x["id"]: f"Step {i}: {x['request'][:60]}" for i, x in enumerate(steps, 1)
        }
        with st.expander(f"Steps in this run ({len(steps)})"):
            step_id = st.selectbox("Step", list(step_labels), format_func=step_labels.get)
            if st.button("Delete this step"):
                db.delete_run(user["id"], step_id)
                st.rerun()
    if st.session_state.get("error"):
        st.error(st.session_state["error"])
    st.stop()

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
    key="request_text",
)
run = st.button(
    "Run the team", type="primary",
    disabled=not request.strip() or (mode_choice == REPO and target is None) or bool(limit_msg),
)
if limit_msg:
    st.warning(limit_msg)

# ---------------------------------------------------------------- run
queued = st.session_state.pop("queued", None)
job = None  # (full request text for the team, short text shown to people)
if run:
    job = (request.strip(), request.strip())
    st.session_state["thread_prev"] = []  # a brand-new task starts a new thread
    st.session_state.pop("thread_id", None)
elif queued and not limit_msg:
    job = (queued["full"], queued["short"])

if job:
    for key in ("result", "error", "error_details", "retry_job", "publish"):
        st.session_state.pop(key, None)
    clear_file_ticks()
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

    counted = False
    try:
        outcome = run_team_live(
            job[0], max_iterations, on_event, target if mode_choice == REPO else None
        )
        outcome["request"] = job[1]
        if user and DB_OK:
            try:
                tot = outcome["usage"]["total"]
                db.add_usage(user["id"], tot.get("cost_usd"), (tot.get("input_tokens") or 0) + (tot.get("output_tokens") or 0))
                counted = True
            except Exception:
                pass
            try:
                outcome["run_id"] = db.save_run(
                    user["id"], job[1], outcome, st.session_state.get("thread_id")
                )
                st.session_state.setdefault("thread_id", outcome["run_id"])
            except Exception as exc:  # the run itself succeeded; only the history failed
                st.warning(f"This run could not be saved to your history: {exc}")
        st.session_state["result"] = outcome
        pipe.markdown(
            pipeline_html(
                None, {"repo_analyst", "product_manager", "developer", "reviewer"},
                live["attempt"], repo=is_existing,
            ),
            unsafe_allow_html=True,
        )
    except Exception as exc:  # missing key, API error, GitHub error, cut-off output, ...
        info = explain(exc)
        st.session_state["error"] = info["message"]
        st.session_state["error_details"] = info["details"]
        st.session_state["retry_job"] = (
            {"full": job[0], "short": job[1], "followup": not run,
             "repo": bool(target and mode_choice == REPO)} if info["retry"] else None
        )
        pipe.empty()
        if st.session_state.get("thread_prev"):  # a failed follow-up must not lose the earlier result
            st.session_state["result"] = st.session_state["thread_prev"].pop()
    if counted and "error" not in st.session_state:
        st.rerun()  # refreshes the sidebar counter; the result is kept in session state

def retry_failed() -> None:
    """Button callback: run the failed request again (a failed follow-up is added to its task again)."""
    job_ = st.session_state.pop("retry_job", None)
    if not job_:
        return
    if job_["followup"] and st.session_state.get("result"):
        st.session_state["thread_prev"] = [*st.session_state.get("thread_prev", []), st.session_state["result"]]
    st.session_state["queued"] = {"full": job_["full"], "short": job_["short"]}


if "error" in st.session_state:
    st.error(st.session_state["error"])
    if st.session_state.get("error_details"):
        with st.expander("Technical details"):
            st.code(st.session_state["error_details"], language=None)
    retry = st.session_state.get("retry_job")
    if retry:
        blocked = bool(limit_msg) or (retry["repo"] and (mode_choice != REPO or target is None))
        st.button("Retry", type="primary", on_click=retry_failed, disabled=blocked, key="retry_btn")
        if retry["repo"] and blocked and not limit_msg:
            st.caption("Select the same repository above (Mode: GitHub repo) to retry.")


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

    label = st.session_state.get("result", {}).get("request") or state["request"]
    title = st.text_input("Title", value=f"AI Team: {label[:60]}")
    action = "Commit to the repository" if repo_info["empty"] else "Create pull request"
    if repo_info["empty"]:
        st.caption("The repo is empty, so the first files are committed straight to its default branch.")
    token = st.session_state.get("gh_token")
    ready = bool(chosen) and bool(token) and repo_info["can_push"]
    if st.button(action, type="primary", disabled=not ready):
        body = (
            f"Generated by AI Software Team.\n\n**Request:** {label}\n\n"
            f"**Reviewer verdict:** {state['verdict']} ({describe_outcome(state)})\n\n"
            f"Automatic code check: {state.get('check_status') or 'not done'}. "
            "Please review carefully and test before merging."
        )
        with st.spinner("Writing to GitHub..."):
            try:
                published = GitHubClient(token).publish(
                    repo_info["owner"], repo_info["repo"], repo_info["branch"],
                    chosen, title, body, empty=repo_info["empty"],
                )
                st.session_state["publish"] = published
                run_id = st.session_state.get("result", {}).get("run_id")
                if user and DB_OK and run_id:
                    try:
                        db.set_publish(user["id"], run_id, published)
                    except Exception:
                        pass  # the pull request exists; only the history link is missing
                st.rerun()
            except GitHubError as exc:
                st.error(explain(exc)["message"])
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
    prev_steps = st.session_state.get("thread_prev", [])
    if prev_steps:
        thread_cost = sum((r["usage"]["total"].get("cost_usd") or 0.0) for r in [*prev_steps, result])
        st.caption(
            f"Showing step {len(prev_steps) + 1} of {len(prev_steps) + 1} · "
            f"whole task so far: {money(thread_cost)}"
        )
        for i, prev in enumerate(prev_steps, 1):
            with st.expander(f"Step {i}: {prev.get('request') or prev['state']['request']}"):
                st.markdown(f"**Verdict:** {prev['state']['verdict']} - {describe_outcome(prev['state'])}  ·  "
                            f"**Cost:** {money(prev['usage']['total'].get('cost_usd'))}")
                st.markdown("**Answer**")
                st.markdown(prev["state"]["code"])
                st.markdown("**Review**")
                st.markdown(prev["state"]["review"])
        st.markdown(f"##### Step {len(prev_steps) + 1}: {result.get('request') or state['request']}")

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
        status = state.get("check_status")
        if status and status != "skipped" or state.get("check_report"):
            icon = {"passed": "✅", "failed": "❌", "inconclusive": "⚠️"}.get(status, "ℹ️")
            with st.expander(f"{icon} Automatic code check: {status}", expanded=status == "failed"):
                st.code(state.get("check_report") or "(no output)", language=None)
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
        st.dataframe(rows, hide_index=True)
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

    # ---- follow-up: continue from this result instead of starting over
    def queue_followup(earlier: str) -> None:
        text = st.session_state.get("followup_text", "").strip()
        if text:
            st.session_state["thread_prev"] = [*st.session_state.get("thread_prev", []), result]
            st.session_state["queued"] = {
                "full": build_followup(earlier, state, text),
                "short": short_label(text),
            }
            st.session_state["followup_text"] = ""

    st.divider()
    st.button("＋ Start a new task", on_click=new_task, key="new_task_bottom")
    st.subheader("Ask for a change")
    st.caption("The team continues from the result above instead of starting over. This counts as a new run.")
    prev_steps = st.session_state.get("thread_prev", [])
    earlier_label = (prev_steps[0] if prev_steps else result).get("request") or state["request"]
    follow_ok = (not repo_info) or (mode_choice == REPO and target is not None)
    st.text_area(
        "Follow-up request", key="followup_text", height=90,
        placeholder="e.g. Also add input validation, or: rename the function and add a docstring",
    )
    st.button(
        "Run follow-up", type="primary", on_click=queue_followup, args=(earlier_label,),
        disabled=not follow_ok or bool(limit_msg),
    )
    if repo_info and not follow_ok:
        st.caption("Select the same repository above (Mode: GitHub repo) to continue this run.")