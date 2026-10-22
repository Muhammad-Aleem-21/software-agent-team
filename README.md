# AI Software Team

A multi-agent system that turns a feature request into a spec, working code and a code review, and shows what each agent cost.

Built with **LangGraph** and **Groq**. A supervisor coordinates three agents with shared state:

| Agent | Job |
|---|---|
| Product Manager | Turns the request into a short spec |
| Developer | Plans and writes the code (or revises it after a review) |
| Reviewer | Reviews the code and approves it or sends it back |

## Features

- **Supervisor pattern:** an LLM picks the next agent, a rules guardrail rejects illegal moves, and each agent is its own sub-graph.
- **Review loop:** the Reviewer can send code back to the Developer, up to a maximum number of attempts.
- **Token and cost tracking:** input, output and thinking tokens and cost per agent. Model prices are looked up online (cached) with a built-in fallback.
- **Run traces:** full runs in LangSmith (optional).
- **GitHub repo mode:** connect GitHub, pick a repo, and the team reads it (or builds in an empty one), then you preview the file changes and open a pull request.
- **Code check:** before review, Python is parsed and (optionally) run with its tests; failures go back to the Developer.
- **Follow-ups:** ask for a change on a finished result and the team continues from it.
- **Friendly errors:** rate limits, GitHub problems and cut-off answers show a plain message, technical details on demand, and a Retry button (`software_team/errors.py`).
- **Daily limits:** per-user caps on runs (and optionally cost and tokens), shown in the sidebar.
- **Web UI:** a Streamlit app with live agent progress, results and a usage breakdown.

## How it works

```
request -> supervisor -> Product Manager -> supervisor -> Developer -> supervisor -> Reviewer
                              ^                                                         |
                              |              changes requested (until max attempts)     |
                              +----------------------- Developer <----------------------+
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env  (then set GROQ_API_KEY)
```

Run the web UI:

```bash
streamlit run app.py
```

Or the command line:

```bash
python main.py "Add a password reset feature" --show
```

CLI runs are saved to `outputs/<timestamp>/` (spec, code, review, every version and `run.json`).

## Configuration (`.env`)

| Variable | Purpose | Default |
|---|---|---|
| `GROQ_API_KEY` | Groq API key (required) | - |
| `MODEL_NAME` | Model to use | `openai/gpt-oss-20b` |
| `MAX_ITERATIONS` | Max developer attempts | `3` |
| `PRICE_LOOKUP` | Look up model prices online | `on` |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Enable LangSmith traces | off |

See `.env.example` for the full list.

## Project structure

```
app.py               # Streamlit UI
ui_style.py          # UI styling and animations
main.py              # CLI
software_team/
  graph.py           # supervisor graph
  supervisor.py      # routing + guardrail
  agents/            # product_manager, developer, reviewer, repo_analyst sub-graphs
  github_client.py   # GitHub API + login helpers
  db.py              # users and saved run history (SQLAlchemy)
  auth.py            # signed "stay signed in" cookie
  repo_context.py    # reads a repo into limited context, parses file changes
  tracking.py        # tokens and cost per agent
  observability.py   # LangSmith setup
  sandbox.py         # syntax check / limited run of the generated code
  followup.py        # builds a follow-up request from an earlier result
  config.py  llm.py  state.py  prompts.py
tests/               # offline tests with a fake LLM
```

## Accounts and history

The GitHub login is the account. Each run is saved per user in a database (`software_team/db.py`), and the sidebar has a **History** page to reopen or delete runs, plus **Delete my data**. A signed cookie (`software_team/auth.py`) keeps users signed in; it holds only the GitHub user id, never a token.

- **Production:** create a free Postgres database (Supabase or Neon) and set `DATABASE_URL` to its pooler link. Set `SESSION_SECRET` to a long random string.
- **Local development:** leave `DATABASE_URL` empty and a SQLite file (`.cache/history.db`) is used.
- If GitHub login is not configured (`GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` empty), the app runs in guest mode with no sign-in and no history.

## Changing the model or provider

Set `MODEL_NAME` (or `DEVELOPER_MODEL`, `REVIEWER_MODEL`, ... per agent). No prefix means Groq. For other providers use `openai:gpt-4o`, `anthropic:claude-sonnet-4-5` or `google:gemini-2.5-flash`, add the matching key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) and install its package (`langchain-openai`, `langchain-anthropic`, `langchain-google-genai`). Leave `REASONING_EFFORT` empty for models that don't support it.

## GitHub repo mode

Register a GitHub App (Contents and Pull requests: read & write), set its callback URL to your app's address, and put `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_SLUG` and `APP_URL` in `.env` (see `.env.example`). Changes always go to a new branch as a pull request; nothing touches the main branch except the first commit of an empty repo. 

## Usage limits

Signed-in users get `DAILY_RUN_LIMIT` runs per UTC day (default 10, `0` = unlimited) and optionally `DAILY_COST_LIMIT_USD` and `DAILY_TOKEN_LIMIT`. Counters live in the `usage` table, so deleting runs does not refund them. Limits need the database; guests have none.

## Code check

`CODE_CHECK` is `off`, `syntax` or `run`. `syntax` parses the Python and never executes it. `run` also executes the code (or its tests, if present) in a child process with a 15 s timeout, CPU/memory/file limits, an empty environment and a temp folder. It is **not a full sandbox** (the code can still read files the server can read, and use the network), so it defaults to `run` only on localhost and `syntax` when hosted. For a public app, run it inside a container or an external sandbox service first.

## Deploy (free)

Deploy on **Streamlit Community Cloud**: push to GitHub, create an app with `app.py` as the main file, and add `GROQ_API_KEY` under Secrets. Do not commit `.env`.

## Roadmap

- Usage billing
- Bring your own API key
- A real isolated sandbox for hosted runs
- More agents (security reviewer)
