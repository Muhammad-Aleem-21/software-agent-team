"""A small GitHub REST client (read a repo, open a pull request) plus the login helpers.

Only what the app needs. Uses the GitHub App "user access token" from the login flow,
or no token at all to read a public repo.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import requests

API = "https://api.github.com"
TIMEOUT = 30


class GitHubError(RuntimeError):
    """A GitHub problem, worded so it can be shown to the user as it is."""


# --------------------------------------------------------------------------- repo links
_SHORT = re.compile(r"^([\w.-]+)/([\w.-]+?)(?:\.git)?$")
_URL = re.compile(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#?].*)?$")


def parse_repo_url(text: str) -> tuple[str, str]:
    """'https://github.com/o/r', 'git@github.com:o/r.git' or 'o/r'  ->  ('o', 'r')."""
    text = (text or "").strip()
    match = _URL.search(text) or _SHORT.match(text)
    if not match:
        raise GitHubError("That doesn't look like a GitHub repo link. Use https://github.com/owner/repo")
    return match.group(1), match.group(2)


# --------------------------------------------------------------------------- login (OAuth)
def sign_state(secret: str) -> str:
    """A tamper-proof 'state' value. The login redirect starts a fresh Streamlit session,
    so we can't remember the state in session memory; we sign it instead."""
    nonce, stamp = secrets.token_urlsafe(8), str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{nonce}.{stamp}".encode(), hashlib.sha256).hexdigest()[:24]
    return f"{nonce}.{stamp}.{sig}"


def verify_state(state: str, secret: str, max_age: int = 600) -> bool:
    try:
        nonce, stamp, sig = state.split(".")
        good = hmac.new(secret.encode(), f"{nonce}.{stamp}".encode(), hashlib.sha256).hexdigest()[:24]
        return hmac.compare_digest(sig, good) and 0 <= time.time() - int(stamp) <= max_age
    except (ValueError, AttributeError):
        return False


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "state": state})
    return f"https://github.com/login/oauth/authorize?{query}"


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> str:
    """Swap the one-time code GitHub sent back for the user's access token."""
    try:
        resp = requests.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise GitHubError(f"Could not reach GitHub to finish the login: {exc}") from exc
    token = data.get("access_token")
    if not token:
        raise GitHubError(f"GitHub login failed: {data.get('error_description') or data.get('error') or 'unknown error'}")
    return token


# --------------------------------------------------------------------------- the client
class GitHubClient:
    def __init__(self, token: str | None = None):
        self.token = token or None
        self.session = requests.Session()
        self.session.headers.update(
            {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        )
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    # -- plumbing ----------------------------------------------------------------
    def _request(self, method: str, path: str, ok: tuple[int, ...] = (200, 201), **kwargs) -> Any:
        try:
            resp = self.session.request(method, f"{API}{path}", timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise GitHubError(f"Could not reach GitHub: {exc}") from exc
        if resp.status_code in ok:
            if kwargs.get("headers", {}).get("Accept", "").endswith("raw+json"):
                return resp.text
            return resp.json() if resp.content else {}
        raise GitHubError(self._explain(resp))

    @staticmethod
    def _explain(resp: requests.Response) -> str:
        try:
            message = resp.json().get("message", "")
        except ValueError:
            message = ""
        if resp.status_code == 401:
            return "GitHub says the login expired. Please connect GitHub again."
        if resp.status_code == 403 and "rate limit" in message.lower():
            return "GitHub rate limit reached. Connect GitHub (higher limit) or try again later."
        if resp.status_code == 403:
            return f"GitHub refused (403): the app is not allowed to do that on this repo. {message}"
        if resp.status_code == 404:
            return "Repo or file not found, or the app has no access to it (is it installed on that repo?)."
        if resp.status_code == 409 and "empty" in message.lower():
            return "empty"
        return f"GitHub error {resp.status_code}: {message}"

    # -- who am I, which repos ---------------------------------------------------
    def current_user(self) -> str:
        return self._request("GET", "/user")["login"]

    def list_repos(self, limit: int = 100) -> list[str]:
        """'owner/repo' for every repo the logged-in user gave the app access to."""
        names: list[str] = []
        installs = self._request("GET", "/user/installations?per_page=100").get("installations", [])
        for inst in installs:
            page = self._request(
                "GET", f"/user/installations/{inst['id']}/repositories?per_page=100"
            ).get("repositories", [])
            names += [r["full_name"] for r in page]
            if len(names) >= limit:
                break
        return sorted(set(names))[:limit]

    # -- reading -----------------------------------------------------------------
    def get_repo(self, owner: str, repo: str) -> dict:
        return self._request("GET", f"/repos/{owner}/{repo}")

    def list_branches(self, owner: str, repo: str) -> list[str]:
        data = self._request("GET", f"/repos/{owner}/{repo}/branches?per_page=100")
        return [b["name"] for b in data]

    def get_tree(self, owner: str, repo: str, ref: str) -> list[dict]:
        """Every file in the repo: [{'path': ..., 'size': ...}]."""
        data = self._request("GET", f"/repos/{owner}/{repo}/git/trees/{ref}?recursive=1")
        return [
            {"path": item["path"], "size": item.get("size", 0)}
            for item in data.get("tree", [])
            if item.get("type") == "blob"
        ]

    def get_file_text(self, owner: str, repo: str, path: str, ref: str) -> str | None:
        """The file as text, or None when it can't be read as text."""
        try:
            text = self._request(
                "GET",
                f"/repos/{owner}/{repo}/contents/{path}?ref={ref}",
                headers={"Accept": "application/vnd.github.raw+json"},
            )
        except GitHubError:
            return None
        return None if "\x00" in text else text

    # -- writing -----------------------------------------------------------------
    def publish(
        self,
        owner: str,
        repo: str,
        base_branch: str,
        files: list[dict],
        title: str,
        body: str,
        empty: bool = False,
    ) -> dict:
        """Save the files. Normal repo: new branch + pull request. Empty repo: commit to main.

        files: [{'path': ..., 'content': ...}]. Returns {'kind': 'pr'|'commit', 'url': ...}.
        """
        if not files:
            raise GitHubError("There are no file changes to publish.")
        if empty:
            return self._publish_empty(owner, repo, files, title)

        ref = self._request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{base_branch}")
        base_sha = ref["object"]["sha"]
        base_tree = self._request("GET", f"/repos/{owner}/{repo}/git/commits/{base_sha}")["tree"]["sha"]
        tree = self._new_tree(owner, repo, base_tree, files)
        commit = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/commits",
            json={"message": title, "tree": tree, "parents": [base_sha]},
        )
        branch = self._create_branch(owner, repo, commit["sha"])
        pr = self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={"title": title, "head": branch, "base": base_branch, "body": body},
        )
        return {"kind": "pr", "url": pr["html_url"], "branch": branch}

    def _new_tree(self, owner: str, repo: str, base_tree: str, files: list[dict]) -> str:
        entries = [
            {"path": f["path"], "mode": "100644", "type": "blob", "content": f["content"]}
            for f in files
        ]
        data = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/trees",
            json={"base_tree": base_tree, "tree": entries},
        )
        return data["sha"]

    def _create_branch(self, owner: str, repo: str, sha: str) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for attempt in range(3):
            name = f"ai-team/{stamp}" + (f"-{attempt}" if attempt else "")
            try:
                self._request(
                    "POST",
                    f"/repos/{owner}/{repo}/git/refs",
                    json={"ref": f"refs/heads/{name}", "sha": sha},
                )
                return name
            except GitHubError as exc:
                if "422" not in str(exc):
                    raise
        raise GitHubError("Could not create a new branch name.")

    def _publish_empty(self, owner: str, repo: str, files: list[dict], title: str) -> dict:
        """An empty repo has no branch yet, so the first file goes in through the Contents API
        (which creates the first commit); the rest are added in one more commit."""
        first, rest = files[0], files[1:]
        put = self._request(
            "PUT",
            f"/repos/{owner}/{repo}/contents/{first['path']}",
            json={
                "message": title,
                "content": base64.b64encode(first["content"].encode()).decode(),
            },
        )
        url = put["commit"]["html_url"]
        if rest:
            branch = self.get_repo(owner, repo)["default_branch"]
            ref = self._request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
            sha = ref["object"]["sha"]
            base_tree = self._request("GET", f"/repos/{owner}/{repo}/git/commits/{sha}")["tree"]["sha"]
            tree = self._new_tree(owner, repo, base_tree, rest)
            commit = self._request(
                "POST",
                f"/repos/{owner}/{repo}/git/commits",
                json={"message": title, "tree": tree, "parents": [sha]},
            )
            self._request(
                "PATCH",
                f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
                json={"sha": commit["sha"]},
            )
            url = commit["html_url"]
        return {"kind": "commit", "url": url}
