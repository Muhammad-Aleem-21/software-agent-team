"""LLM helpers. This is the only file that knows we are using Groq."""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig, RunnableLambda

from .config import get_settings


def get_llm(agent_name: str):
    """Create the chat model for one agent (model name comes from the settings)."""
    from langchain_groq import ChatGroq  # imported here so tests don't need the package configured

    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    extra = {}
    if settings.reasoning_effort:  # leave REASONING_EFFORT empty for models that don't support it
        extra["reasoning_effort"] = settings.reasoning_effort
    chat = ChatGroq(
        model=settings.agent_models[agent_name],
        temperature=settings.temperature,
        api_key=settings.groq_api_key,
        max_tokens=settings.max_tokens,
        **extra,
    )

    def call(messages, config: RunnableConfig):
        return _invoke_checked(chat, messages, config)

    return RunnableLambda(call)


def content_text(message: Any) -> str:
    """Return the plain text of a model response (handles str or content-block lists)."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts).strip()


def require_text(text: str, what: str) -> str:
    """Fail loudly on an empty answer.

    Without this, an empty spec would make the supervisor think the spec is still
    missing and call the Product Manager again and again, burning tokens.
    """
    if not text:
        raise RuntimeError(
            f"The model returned an empty {what}. Try again, or use a different MODEL_NAME."
        )
    return text



def _invoke_checked(chat, messages, config):
    """Call the model. Raise a clear error if the answer was cut off; retry once if it is empty."""
    reply = None
    for _ in range(2):
        reply = chat.invoke(messages, config=config)
        finish_reason = (getattr(reply, "response_metadata", None) or {}).get("finish_reason")
        if finish_reason == "length":
            raise RuntimeError(
                "The model's answer was cut off because it hit the output token limit "
                "(thinking tokens count too). Raise MAX_TOKENS or lower REASONING_EFFORT in .env."
            )
        if content_text(reply):
            return reply
    return reply  # still empty after one retry: require_text() in the agent explains it
