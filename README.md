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
  agents/            # product_manager, developer, reviewer sub-graphs
  tracking.py        # tokens and cost per agent
  observability.py   # LangSmith setup
  config.py  llm.py  state.py  prompts.py
tests/               # offline tests with a fake LLM
```

## Deploy (free)

Deploy on **Streamlit Community Cloud**: push to GitHub, create an app with `app.py` as the main file, and add `GROQ_API_KEY` under Secrets. Do not commit `.env`.

## Roadmap

- Saved run history (database) and a History tab
- User accounts, per-user limits and usage billing
- More agents (tester, security reviewer)