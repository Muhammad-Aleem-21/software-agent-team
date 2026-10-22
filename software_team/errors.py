"""Turn technical errors into messages people understand.

explain(exc) -> {"message": plain-words text, "retry": True if trying again can help,
                 "details": the technical text (secrets removed) for an expander}
"""
from __future__ import annotations

import re

_SECRETS = re.compile(r"(gsk_|sk-|sk-ant-|ghp_|ghu_|github_pat_|AIza)[A-Za-z0-9_\-]{8,}")


def _clean(text: str) -> str:
    text = _SECRETS.sub("[hidden]", text)
    return text if len(text) <= 800 else text[:800] + " ..."


def explain(exc: BaseException) -> dict:
    raw = str(exc) or exc.__class__.__name__
    low = (raw + " " + exc.__class__.__name__).lower()

    def out(message: str, retry: bool) -> dict:
        return {"message": message, "retry": retry, "details": _clean(raw)}

    if "cut off" in low:
        return out("The AI's answer got too long and was cut off. Retrying often works. If it keeps "
                   "happening, ask for something smaller.", True)
    if "empty" in low and "model returned" in low:
        return out("The AI sent back an empty answer. Please retry.", True)
    if any(k in low for k in ("request too large", "413", "context length", "maximum context",
                              "context_length", "too many tokens")):
        return out("This request is too big for the AI model. Ask for something smaller, or pick a "
                   "smaller repository.", False)
    if any(k in low for k in ("rate limit", "rate_limit", "ratelimit", "429", "tokens per minute",
                              "requests per minute", "quota")):
        return out("The AI service is busy right now (its free usage limit was hit). Wait about a "
                   "minute and press Retry.", True)
    if any(k in low for k in ("is not set", "api key", "api_key", "401", "authentication",
                              "unauthorized", "needs the package")):
        return out("The AI service is not set up correctly on the app's side (missing or wrong key). "
                   "This is not your fault. Please tell the app owner.", False)
    if "recursion" in low:
        return out("The team got stuck going in circles. Please retry, or word the request more "
                   "clearly.", True)
    if exc.__class__.__name__ == "GitHubError":
        if any(k in low for k in ("expired", "bad credentials", "401")):
            return out("GitHub says your login expired. Click Connect GitHub again, then retry.", False)
        if any(k in low for k in ("not found", "404", "no access")):
            return out("GitHub could not find that repository, or the app is not installed on it. "
                       "Check the link, or use 'Install the app on it'.", False)
        if any(k in low for k in ("could not reach", "timed out", "connection", "rate limit", "403")):
            return out("Could not talk to GitHub just now. Wait a moment and retry.", True)
        return out(raw, False)  # the app's own GitHub messages are already written for people
    if any(k in low for k in ("timeout", "timed out", "connection", "temporarily", "503", "502",
                              "overloaded", "unavailable")):
        return out("The connection to the AI service dropped. Please retry.", True)
    return out("Something went wrong while running the team. Please retry. If it keeps happening, "
               "try a smaller request.", True)
