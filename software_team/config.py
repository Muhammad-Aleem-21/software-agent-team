"""Central configuration.

Everything you may want to change (API key, model, temperature, supervisor mode)
is read from environment variables / the .env file, so no code edits are needed
to switch models.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "openai/gpt-oss-20b"
DEFAULT_MAX_ITERATIONS = 3

# Every agent that can call an LLM. Each one may use its own model
# (see the *_MODEL variables in .env.example).
AGENT_NAMES = ("supervisor", "product_manager", "developer", "reviewer", "repo_analyst")


@dataclass(frozen=True)
class Settings:
    groq_api_key: str | None
    model_name: str
    temperature: float
    supervisor_mode: str  # "llm" = the LLM picks the next agent, "rules" = plain Python
    agent_models: dict[str, str]  # agent name -> model name
    max_tokens: int = 8192  # output limit per call; "thinking" tokens count too
    reasoning_effort: str = ""  # "low" / "medium" / "high"; empty = don't send it
    max_iterations: int = DEFAULT_MAX_ITERATIONS  # max versions the Developer may write
    price_lookup: bool = True  # look model prices up online (cached); False = built-in table only
    # --- GitHub repo mode (all optional; Quick mode needs none of this) ---
    repo_max_files: int = 8  # most files the Repo Analyst may read
    repo_max_file_chars: int = 8000  # each file is cut to this many characters
    repo_max_context_chars: int = 24000  # total characters of repo code sent to the agents
    github_client_id: str = ""  # GitHub App client id ("Login with GitHub")
    github_client_secret: str = ""
    github_app_slug: str = ""  # the app's URL name, used for the "install on more repos" link
    app_url: str = "http://localhost:8501"  # this app's public address = the GitHub callback URL
    # --- accounts and saved history ---
    database_url: str = ""  # hosted Postgres link; empty = a local SQLite file (.cache/history.db)
    session_secret: str = ""  # signs the "stay signed in" cookie; falls back to the GitHub secret
    # --- usage limits (per signed-in user, per UTC day; 0 = no limit) ---
    daily_run_limit: int = 10
    daily_cost_limit_usd: float = 0.0
    # --- checking the generated code: "off" | "syntax" (never executes anything) | "run" ---
    code_check: str = "syntax"


def _code_check_mode(app_url: str) -> str:
    """CODE_CHECK=off|syntax|run. Default: run on a local machine, syntax-only when hosted,
    because running AI-written code on a public server needs a real sandbox."""
    value = os.getenv("CODE_CHECK", "").strip().lower()
    if value in ("off", "syntax", "run"):
        return value
    local = app_url.startswith("http://localhost") or app_url.startswith("http://127.0.0.1")
    return "run" if local else "syntax"


def get_settings() -> Settings:
    """Read settings from the environment (not cached, so tests can override it)."""
    default_model = os.getenv("MODEL_NAME", DEFAULT_MODEL)
    app_url = os.getenv("APP_URL", "http://localhost:8501").strip().rstrip("/")
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY"),
        model_name=default_model,
        temperature=float(os.getenv("TEMPERATURE", "0.2")),
        supervisor_mode=os.getenv("SUPERVISOR_MODE", "llm").strip().lower(),
        agent_models={
            name: os.getenv(f"{name.upper()}_MODEL", default_model) for name in AGENT_NAMES
        },
        max_tokens=int(os.getenv("MAX_TOKENS", "8192")),
        reasoning_effort=os.getenv("REASONING_EFFORT", "low").strip().lower(),
        max_iterations=max(1, int(os.getenv("MAX_ITERATIONS", str(DEFAULT_MAX_ITERATIONS)))),
        price_lookup=os.getenv("PRICE_LOOKUP", "on").strip().lower() not in ("off", "false", "0", "no"),
        repo_max_files=max(1, int(os.getenv("REPO_MAX_FILES", "8"))),
        repo_max_file_chars=max(500, int(os.getenv("REPO_MAX_FILE_CHARS", "8000"))),
        repo_max_context_chars=max(2000, int(os.getenv("REPO_MAX_CONTEXT_CHARS", "24000"))),
        github_client_id=os.getenv("GITHUB_CLIENT_ID", "").strip(),
        github_client_secret=os.getenv("GITHUB_CLIENT_SECRET", "").strip(),
        github_app_slug=os.getenv("GITHUB_APP_SLUG", "").strip(),
        app_url=app_url,
        database_url=os.getenv("DATABASE_URL", "").strip(),
        session_secret=os.getenv("SESSION_SECRET", "").strip(),
        daily_run_limit=max(0, int(os.getenv("DAILY_RUN_LIMIT", "10"))),
        daily_cost_limit_usd=max(0.0, float(os.getenv("DAILY_COST_LIMIT_USD", "0"))),
        code_check=_code_check_mode(app_url),
    )
