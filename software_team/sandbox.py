"""Automatic check of the Developer's code, done before the Reviewer reads it.

Modes (setting CODE_CHECK):
  off     - nothing is checked
  syntax  - Python files are parsed (never executed). Safe anywhere.
  run     - after the syntax check, the code (and its tests, if any) is executed in a child
            process with a time limit, memory/CPU/file limits, an empty environment (no API
            keys) and a temporary folder. This is NOT a full sandbox: the code can still read
            files the server user can read, and has network access unless the system has
            `unshare`. Use "run" on your own machine, or put a container around the app.

Result: {"status": skipped|passed|failed|inconclusive, "report": text}.
Only "failed" makes the Reviewer send the code back.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from .repo_context import parse_changes, safe_path

TIMEOUT_SECONDS = 15
MAX_REPORT_CHARS = 2500
_FENCE = re.compile(r"```[ \t]*([\w+-]*)[^\n]*\n(.*?)```", re.DOTALL)
_PY_LANGS = {"python", "py", "python3"}
_IGNORED_FAILURES = ("ModuleNotFoundError", "ImportError", "EOFError", "KeyboardInterrupt")


def files_from_code(code: str, repo_mode: str) -> dict[str, str]:
    """Python files in the Developer's answer: path -> text."""
    if repo_mode:
        return {c["path"]: c["content"] for c in parse_changes(code) if c["path"].endswith(".py")}
    blocks = [body for lang, body in _FENCE.findall(code) if lang.lower() in _PY_LANGS]
    if not blocks:
        return {}
    joined = "\n\n".join(blocks)
    has_tests = bool(re.search(r"^\s*def test_|unittest\.TestCase", joined, re.MULTILINE))
    return {"test_solution.py" if has_tests else "solution.py": joined}


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_REPORT_CHARS else text[:MAX_REPORT_CHARS] + "\n... (cut)"


def _limits() -> None:  # runs in the child process before the code starts
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    os.setsid()


def _run(cmd: list[str], cwd: str) -> tuple[int | None, str]:
    """Run a command; (None, text) means it hit the time limit."""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": cwd, "PYTHONDONTWRITEBYTECODE": "1"}
    kwargs: dict = {"cwd": cwd, "env": env, "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True}
    if os.name == "posix":
        kwargs["preexec_fn"] = _limits
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as exc:
        return 1, f"Could not start the check: {exc}"
    try:
        out, _ = proc.communicate(timeout=TIMEOUT_SECONDS)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        out, _ = proc.communicate()
        return None, out or ""


def _has_module(name: str) -> bool:
    try:
        r = subprocess.run([sys.executable, "-I", "-c", f"import {name}"], capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def check_code(code: str, repo_mode: str, mode: str, repo_files: dict[str, str] | None = None) -> dict:
    if mode == "off":
        return {"status": "skipped", "report": ""}
    files = files_from_code(code, repo_mode)
    if not files:
        return {"status": "skipped", "report": "No Python files to check."}

    problems = []
    for path, text in files.items():
        try:
            ast.parse(text, filename=path)
        except SyntaxError as exc:
            problems.append(f"{path}: syntax error on line {exc.lineno}: {exc.msg}")
    if problems:
        return {"status": "failed", "report": "Syntax check FAILED:\n" + "\n".join(problems)}
    if mode != "run":
        return {"status": "passed", "report": f"Syntax check passed for {len(files)} Python file(s). (Code was not run.)"}

    tmp = tempfile.mkdtemp(prefix="codecheck_")
    try:
        for path, text in {**(repo_files or {}), **files}.items():
            clean = safe_path(path)
            if clean:
                target = Path(tmp) / clean
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        tests = [p for p in files if Path(p).name.startswith("test_") or Path(p).name.endswith("_test.py")]
        if tests:
            if _has_module("pytest"):
                cmd, label = [sys.executable, "-I", "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider", *tests], "pytest"
            else:
                cmd, label = [sys.executable, "-I", "-m", "unittest", *[t[:-3].replace("/", ".") for t in tests]], "unittest"
        else:
            main = next(iter(files))
            cmd, label = [sys.executable, "-I", main], f"python {main}"
        code_, out = _run(cmd, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    out = _clip(out)
    if code_ is None:
        return {"status": "inconclusive", "report": f"`{label}` ran for over {TIMEOUT_SECONDS}s and was stopped (normal for servers or loops).\n{out}"}
    if code_ < 0:  # killed by a signal: out of CPU time or memory (loops, servers)
        return {"status": "inconclusive", "report": f"`{label}` was stopped by the resource limits (CPU time or memory).\n{out}"}
    if code_ == 0:
        return {"status": "passed", "report": f"`{label}` succeeded.\n{out}"}
    if any(name in out for name in _IGNORED_FAILURES) or (code_ == 2 and "usage:" in out):
        return {"status": "inconclusive", "report": f"`{label}` could not run fully (missing module, input or arguments):\n{out}"}
    return {"status": "failed", "report": f"`{label}` FAILED (exit code {code_}):\n{out}"}
