"""Turn "also add X" into a request the team can build on the earlier result."""
from __future__ import annotations

MAX_SPEC_CHARS = 4000
MAX_CODE_CHARS = 14000


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n... (cut for length)"


def build_followup(earlier_request: str, state: dict, text: str) -> str:
    """The full request text sent to the team (the short label shown to people is separate)."""
    return (
        "This is a FOLLOW-UP to earlier work. Change the existing result below to satisfy the new "
        "request. Keep everything that already works, and return the complete updated result.\n\n"
        f"Earlier request:\n{earlier_request}\n\n"
        f"Current specification:\n{_cut(state.get('spec', ''), MAX_SPEC_CHARS)}\n\n"
        f"Current code (the result so far):\n{_cut(state.get('code', ''), MAX_CODE_CHARS)}\n\n"
        f"New request:\n{text}"
    )


def short_label(text: str) -> str:
    return "Follow-up: " + " ".join(text.split())
