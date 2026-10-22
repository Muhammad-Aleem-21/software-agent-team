"""Staying signed in: a small signed cookie that says WHO the user is (their GitHub id).

It never contains a GitHub token. Plain Python with no Streamlit import, so it is easy to test.
"""
from __future__ import annotations

import hashlib
import hmac
import time

COOKIE = "ast_session"
DAYS = 30


def _sig(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def sign_session(user_id: int, secret: str, days: int = DAYS) -> str:
    """'<user id>.<expiry>.<signature>'. Nobody can forge it without the secret."""
    expires = int(time.time()) + days * 86400
    payload = f"{user_id}.{expires}"
    return f"{payload}.{_sig(secret, payload)}"


def read_session(token: str | None, secret: str) -> int | None:
    """The user id if the cookie is genuine and not expired, else None."""
    if not token or not secret:
        return None
    try:
        user_id, expires, sig = token.split(".")
        if not hmac.compare_digest(sig, _sig(secret, f"{user_id}.{expires}")):
            return None
        return int(user_id) if int(expires) > time.time() else None
    except ValueError:
        return None


def cookie_html(token: str | None, secure: bool, days: int = DAYS) -> str:
    """A tiny script that sets (token) or removes (None) the cookie in the browser.
    Meant for streamlit.components.v1.html(..., height=0)."""
    age = days * 86400 if token else 0
    cookie = f"{COOKIE}={token or ''}; Max-Age={age}; Path=/; SameSite=Lax" + ("; Secure" if secure else "")
    return (
        "<script>(function(){var c=" + repr(cookie) + ";"
        "try{window.parent.document.cookie=c;}catch(e){document.cookie=c;}})();</script>"
    )
