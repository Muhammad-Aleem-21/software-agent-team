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
AGENT_NAMES = ("supervisor", "product_manager", "developer", "reviewer")


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


def get_settings() -> Settings:
    """Read settings from the environment (not cached, so tests can override it)."""
    default_model = os.getenv("MODEL_NAME", DEFAULT_MODEL)
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
    )
