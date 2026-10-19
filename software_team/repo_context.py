"""Turn a GitHub repo into the small, size-limited context the agents can work with,
and turn the Developer's answer back into a list of files.

No LLM calls here; just reading, filtering, formatting and parsing.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field

from .github_client import GitHubClient

SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv", "env", ".next",
    ".idea", ".vscode", "vendor", "target", "coverage", ".cache", ".pytest_cache", ".mypy_cache",
}
SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf", ".zip", ".gz", ".tar",
    ".mp3", ".mp4", ".mov", ".woff", ".woff2", ".ttf", ".eot", ".exe", ".dll", ".so", ".bin",
    ".pyc", ".class", ".jar", ".lock", ".min.js", ".map", ".db", ".sqlite", ".parquet", ".npy",
}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock"}
KEY_FILES = (
    "README.md", "readme.md", "package.json", "requirements.txt", "pyproject.toml",
    "setup.py", "pom.xml", "go.mod", "Cargo.toml", "Gemfile", "composer.json",
)
MAX_TREE_LISTED = 400  # paths shown to the Repo Analyst


def usable_paths(tree: list[dict], max_file_chars: int) -> list[dict]:
    """Drop folders/files that are never worth reading (dependencies, binaries, too-big files)."""
    keep = []
    for item in tree:
        path = item["path"]
        parts = path.split("/")
        name = parts[-1]
        if any(p in SKIP_DIRS for p in parts[:-1]) or name in SKIP_NAMES:
            continue
        if any(name.lower().endswith(ext) for ext in SKIP_EXT):
            continue
        # Files too big to send whole are left out: a file that was cut could be
        # rewritten from half its content and then overwrite the real one.
        if item.get("size", 0) > max_file_chars:
            continue
        keep.append(item)
    return keep


def format_tree(paths: list[str]) -> str:
    shown = paths[:MAX_TREE_LISTED]
    more = f"\n... and {len(paths) - len(shown)} more files" if len(paths) > len(shown) else ""
    return "\n".join(shown) + more


def parse_selection(text: str, valid: set[str], limit: int) -> list[str]:
    """Pick the file paths out of the model's answer (a JSON list, or a {"files": [...]} object).
    Anything not in the repo is ignored."""
    chosen: list[str] = []
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            chosen = [p for p in data if isinstance(p, str)]
        except ValueError:
            chosen = []
    if not chosen:  # fall back: any known path mentioned in the text
        chosen = [p for p in valid if p in text]
    seen, result = set(), []
    for path in chosen:
        path = path.strip().removeprefix("./")
        if path in valid and path not in seen:
            seen.add(path)
            result.append(path)
    return result[:limit]


@dataclass
class RepoHandle:
    """One repo + branch the team is allowed to read."""

    client: GitHubClient
    owner: str
    repo: str
    ref: str
    max_files: int = 8
    max_file_chars: int = 8000
    max_context_chars: int = 24000
    tree: list[dict] = field(default_factory=list)

    def load_tree(self) -> None:
        self.tree = usable_paths(self.client.get_tree(self.owner, self.repo, self.ref), self.max_file_chars)

    @property
    def paths(self) -> list[str]:
        return [item["path"] for item in self.tree]

    def key_files(self) -> list[str]:
        have = set(self.paths)
        return [p for p in KEY_FILES if p in have][:3]

    def read(self, wanted: list[str]) -> dict[str, str]:
        """Read the files (README / manifests first), cut each to a limit, stop at the total limit."""
        order: list[str] = []
        for path in [*self.key_files(), *wanted]:
            if path not in order:
                order.append(path)
        files: dict[str, str] = {}
        used = 0
        for path in order[: self.max_files + len(self.key_files())]:
            text = self.client.get_file_text(self.owner, self.repo, path, self.ref)
            if text is None:
                continue
            if len(text) > self.max_file_chars:
                continue  # never send a partial file (see usable_paths)
            if used + len(text) > self.max_context_chars:
                break
            files[path] = text
            used += len(text)
        return files


# --------------------------------------------------------------------------- prompt blocks
def format_files(files: dict[str, str]) -> str:
    """The same block format the Developer is asked to answer in."""
    return "\n".join(f"=== FILE: {p} ===\n{c}\n=== END FILE ===" for p, c in files.items())


def repo_block(mode: str, summary: str, files: dict[str, str] | None = None) -> str:
    """Extra text appended to an agent's prompt in repo mode ('' in Quick mode)."""
    if not mode:
        return ""
    if mode == "empty":
        return "\n\nREPOSITORY CONTEXT:\nThe target GitHub repository is empty. Create the whole project from scratch."
    text = f"\n\nEXISTING REPOSITORY (summary):\n{summary}"
    if files:
        text += f"\n\nCurrent contents of the relevant files:\n{format_files(files)}"
    return text


# --------------------------------------------------------------------------- parsing the answer
_BLOCK = re.compile(r"^=== FILE: (.+?) ===[ \t]*\n(.*?)\n=== END FILE ===[ \t]*$", re.DOTALL | re.MULTILINE)
_MD_FILE = re.compile(r"^#{2,4}\s+`?([\w./\\-]+\.\w+)`?\s*\n+```[^\n]*\n(.*?)\n```[ \t]*$", re.DOTALL | re.MULTILINE)


def safe_path(path: str) -> str | None:
    """Reject anything that could write outside the repo or into .git."""
    path = path.strip().strip("`").replace("\\", "/")
    if path.startswith("./"):
        path = path[2:]
    parts = path.split("/")
    if not path or path.startswith("/") or ".." in parts or "" in parts or parts[0] == ".git":
        return None
    return path


def parse_changes(text: str) -> list[dict]:
    """The files in the Developer's answer: [{'path', 'content'}]. Later duplicates win."""
    matches = _BLOCK.findall(text) or _MD_FILE.findall(text)
    found: dict[str, str] = {}
    for raw_path, content in matches:
        path = safe_path(raw_path)
        if path:
            found[path] = content if content.endswith("\n") else content + "\n"
    return [{"path": p, "content": c} for p, c in found.items()]


def unified_diff(old: str, new: str, path: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", lineterm="", n=2
    )
    return "\n".join(lines)
