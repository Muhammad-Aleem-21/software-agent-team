"""LLM helpers. This is the only file that knows which AI provider is used.

MODEL_NAME (and the per-agent *_MODEL settings) pick the provider:
    openai/gpt-oss-20b          no prefix        -> Groq (the default)
    groq:llama-3.3-70b-versatile
    openai:gpt-4o               prefix "openai:"    needs OPENAI_API_KEY + langchain-openai
    anthropic:claude-sonnet-4-5 prefix "anthropic:" needs ANTHROPIC_API_KEY + langchain-anthropic
    google:gemini-2.5-flash     prefix "google:"    needs GOOGLE_API_KEY + langchain-google-genai
"""
from __future__ import annotations

import os
from typing import Any

from langchain_core.runnables import RunnableConfig, RunnableLambda

from .config import get_settings

# provider -> (key variable, module, class, pip package)
OTHER_PROVIDERS = {
    "openai": ("OPENAI_API_KEY", "langchain_openai", "ChatOpenAI", "langchain-openai"),
    "anthropic": ("ANTHROPIC_API_KEY", "langchain_anthropic", "ChatAnthropic", "langchain-anthropic"),
    "google": ("GOOGLE_API_KEY", "langchain_google_genai", "ChatGoogleGenerativeAI", "langchain-google-genai"),
}
ALIASES = {"google_genai": "google", "gemini": "google"}


def split_model(name: str) -> tuple[str, str]:
    """'openai:gpt-4o' -> ('openai', 'gpt-4o'). No known prefix means Groq."""
    prefix, sep, rest = name.partition(":")
    provider = ALIASES.get(prefix.strip().lower(), prefix.strip().lower())
    if sep and (provider == "groq" or provider in OTHER_PROVIDERS):
        return provider, rest.strip()
    return "groq", name.strip()


def _build_chat(provider: str, model: str, settings):
    if provider == "groq":
        from langchain_groq import ChatGroq  # imported here so tests don't need the package configured

        if not settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        extra = {}
        if settings.reasoning_effort:  # leave REASONING_EFFORT empty for models that don't support it
            extra["reasoning_effort"] = settings.reasoning_effort
        return ChatGroq(
            model=model,
            temperature=settings.temperature,
            api_key=settings.groq_api_key,
            max_tokens=settings.max_tokens,
            **extra,
        )

    key_var, module_name, class_name, package = OTHER_PROVIDERS[provider]
    if not os.getenv(key_var):
        raise RuntimeError(f"{key_var} is not set. Add it to .env (or to the app's Secrets).")
    try:
        module = __import__(module_name, fromlist=[class_name])
    except ImportError as exc:
        raise RuntimeError(f"Using {provider} models needs the package: pip install {package}") from exc
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
    }
    if provider == "openai" and model.startswith(("o1", "o3", "o4", "gpt-5")):
        kwargs.pop("temperature")  # OpenAI reasoning models only accept their default temperature
        if settings.reasoning_effort:
            kwargs["reasoning_effort"] = settings.reasoning_effort
    return getattr(module, class_name)(**kwargs)  # the key is read from the environment


def get_llm(agent_name: str):
    """Create the chat model for one agent (model name comes from the settings)."""
    settings = get_settings()
    provider, model = split_model(settings.agent_models.get(agent_name, settings.model_name))
    chat = _build_chat(provider, model, settings)

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
        meta = getattr(reply, "response_metadata", None) or {}
        # "length" = Groq/OpenAI, "max_tokens" = Anthropic / Google (stop_reason or finish_reason)
        reason = str(meta.get("finish_reason") or meta.get("stop_reason") or "").lower()
        if reason in ("length", "max_tokens"):
            raise RuntimeError(
                "The model's answer was cut off because it hit the output token limit "
                "(thinking tokens count too). Raise MAX_TOKENS or lower REASONING_EFFORT in .env."
            )
        if content_text(reply):
            return reply
    return reply  # still empty after one retry: require_text() in the agent explains it
