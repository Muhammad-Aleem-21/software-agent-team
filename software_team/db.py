"""Saved users and run history.

Uses SQLAlchemy, so the same code runs on a hosted Postgres (set DATABASE_URL, e.g. from
Supabase or Neon) and on a local SQLite file (used when DATABASE_URL is empty).

Every function that reads or changes runs takes the user's id and filters by it,
so one user can never see or delete another user's runs.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

LOCAL_DB = Path(__file__).resolve().parent.parent / ".cache" / "history.db"

metadata = MetaData()
users = Table(
    "users",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=False),  # the GitHub user id
    Column("login", String(100), nullable=False),
    Column("avatar_url", Text, default=""),
    Column("created_at", DateTime, nullable=False),
    Column("last_login", DateTime, nullable=False),
)
runs = Table(
    "runs",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("thread_id", String(32), index=True),  # runs of one task share it (the first run's id)
    Column("user_id", BigInteger, nullable=False, index=True),
    Column("created_at", DateTime, nullable=False),
    Column("request", Text, nullable=False),
    Column("mode", String(20), nullable=False),  # quick | existing | empty
    Column("repo", String(200)),  # "owner/repo" in repo mode
    Column("verdict", String(30)),
    Column("outcome", String(40)),
    Column("iterations", Integer),
    Column("seconds", Float),
    Column("cost_usd", Float),  # NULL when the model price was unknown
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("result_json", Text, nullable=False),  # everything needed to show the run again
    Column("publish_kind", String(10)),  # pr | commit
    Column("publish_url", Text),
)

# Daily counters. Kept separately from `runs` so deleting a run does not give the quota back.
usage = Table(
    "usage",
    metadata,
    Column("user_id", BigInteger, primary_key=True, autoincrement=False),
    Column("day", String(10), primary_key=True),  # UTC date, YYYY-MM-DD
    Column("runs", Integer, nullable=False, default=0),
    Column("cost_usd", Float, nullable=False, default=0.0),
    Column("tokens", BigInteger, nullable=False, default=0),
)

_engine: Engine | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_engine(url: str) -> Engine:
    """Build an engine. Plain 'postgres://' / 'postgresql://' links get the psycopg2 driver."""
    if not url:
        LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{LOCAL_DB}"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url[len(prefix):]
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_size=2, max_overflow=2, pool_pre_ping=True, pool_recycle=300)


def init_db(url: str = "") -> None:
    """Connect and create the tables if they don't exist yet. Safe to call again."""
    global _engine
    _engine = make_engine(url)
    metadata.create_all(_engine)
    _migrate(_engine)


def _migrate(engine: Engine) -> None:
    """Older databases have no runs.thread_id: add it and make every old run its own thread."""
    from sqlalchemy import inspect, text

    if "thread_id" not in {c["name"] for c in inspect(engine).get_columns("runs")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE runs ADD COLUMN thread_id VARCHAR(32)"))
    if "tokens" not in {c["name"] for c in inspect(engine).get_columns("usage")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE usage ADD COLUMN tokens BIGINT NOT NULL DEFAULT 0"))
    with engine.begin() as conn:
        conn.execute(text("UPDATE runs SET thread_id = id WHERE thread_id IS NULL"))


def is_sqlite() -> bool:
    return _engine is not None and _engine.dialect.name == "sqlite"


def _eng() -> Engine:
    if _engine is None:
        raise RuntimeError("The database is not initialised (call init_db first).")
    return _engine


# --------------------------------------------------------------------------- users
def upsert_user(user_id: int, login: str, avatar_url: str = "") -> None:
    now = _now()
    with _eng().begin() as conn:
        found = conn.execute(select(users.c.id).where(users.c.id == user_id)).first()
        if found:
            conn.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(login=login, avatar_url=avatar_url, last_login=now)
            )
            return
        try:
            conn.execute(
                insert(users).values(
                    id=user_id, login=login, avatar_url=avatar_url, created_at=now, last_login=now
                )
            )
        except IntegrityError:  # two tabs signed in at the same moment: the row exists now
            pass


def get_user(user_id: int) -> dict[str, Any] | None:
    with _eng().connect() as conn:
        row = conn.execute(select(users).where(users.c.id == user_id)).mappings().first()
    if not row:
        return None
    return {"id": int(row["id"]), "login": row["login"], "avatar": row["avatar_url"] or ""}


def delete_user_data(user_id: int) -> None:
    """Remove the user's runs and the user record."""
    with _eng().begin() as conn:
        conn.execute(delete(runs).where(runs.c.user_id == user_id))
        conn.execute(delete(users).where(users.c.id == user_id))


# --------------------------------------------------------------------------- runs
def save_run(
    user_id: int, request: str, result: dict[str, Any], thread_id: str | None = None
) -> str:
    """Store a finished run (the dict returned by the app's run_team_live). Returns its id.
    A follow-up passes the thread_id of the task it continues; a new task starts its own thread."""
    state, usage, repo = result["state"], result["usage"], result.get("repo")
    total = usage["total"]
    mode = "quick" if not repo else ("empty" if repo["empty"] else "existing")
    run_id = uuid.uuid4().hex
    payload = {k: result[k] for k in ("state", "usage", "seconds", "repo")}
    payload["request"] = request  # the short text people typed (state["request"] can be longer)
    with _eng().begin() as conn:
        conn.execute(
            insert(runs).values(
                id=run_id,
                thread_id=thread_id or run_id,
                user_id=user_id,
                created_at=_now(),
                request=request,
                mode=mode,
                repo=f"{repo['owner']}/{repo['repo']}" if repo else None,
                verdict=state.get("verdict"),
                outcome=state.get("outcome"),
                iterations=state.get("iteration"),
                seconds=result.get("seconds"),
                cost_usd=total.get("cost_usd"),
                input_tokens=total.get("input_tokens"),
                output_tokens=total.get("output_tokens"),
                result_json=json.dumps(payload, default=str),
            )
        )
    return run_id


def list_runs(user_id: int, limit: int = 500) -> list[dict[str, Any]]:
    """The user's runs, newest first (summary fields only)."""
    cols = [runs.c[name] for name in (
        "id", "created_at", "request", "mode", "repo", "verdict", "outcome",
        "iterations", "cost_usd", "publish_url", "thread_id",
    )]
    with _eng().connect() as conn:
        rows = conn.execute(
            select(*cols).where(runs.c.user_id == user_id).order_by(runs.c.created_at.desc()).limit(limit)
        ).mappings().all()
    return [dict(r) for r in rows]


def list_threads(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    """One entry per task (thread), newest activity first: the first request, the number of
    steps, the total cost and the latest verdict."""
    cols = [runs.c[n] for n in (
        "id", "thread_id", "created_at", "request", "repo", "verdict", "cost_usd", "publish_url"
    )]
    with _eng().connect() as conn:
        rows = conn.execute(
            select(*cols).where(runs.c.user_id == user_id).order_by(runs.c.created_at.asc()).limit(2000)
        ).mappings().all()
    threads: dict[str, dict[str, Any]] = {}
    for r in rows:  # oldest first, so the last row of a thread is its latest step
        t = threads.setdefault(r["thread_id"], {
            "thread_id": r["thread_id"], "request": r["request"], "steps": 0,
            "cost_usd": 0.0, "published": False,
        })
        t["steps"] += 1
        t["cost_usd"] += r["cost_usd"] or 0.0
        t["published"] = t["published"] or bool(r["publish_url"])
        t["created_at"], t["verdict"], t["repo"] = r["created_at"], r["verdict"], r["repo"]
    return sorted(threads.values(), key=lambda t: t["created_at"], reverse=True)[:limit]


def list_steps(user_id: int, thread_id: str) -> list[dict[str, Any]]:
    """The steps of one thread, oldest first (summary fields only)."""
    cols = [runs.c[n] for n in ("id", "created_at", "request", "verdict", "cost_usd")]
    with _eng().connect() as conn:
        rows = conn.execute(
            select(*cols)
            .where(runs.c.user_id == user_id, runs.c.thread_id == thread_id)
            .order_by(runs.c.created_at.asc())
        ).mappings().all()
    return [dict(r) for r in rows]


def get_thread(user_id: int, thread_id: str) -> list[dict[str, Any]]:
    """Every step of a thread in the shape the app shows live, oldest first."""
    found = [get_run(user_id, s["id"]) for s in list_steps(user_id, thread_id)]
    return [r for r in found if r]


def get_run(user_id: int, run_id: str) -> dict[str, Any] | None:
    """One run in the same shape the app shows live, plus 'run_id' and 'publish'. None if it
    doesn't exist or belongs to someone else."""
    with _eng().connect() as conn:
        row = conn.execute(
            select(runs).where(runs.c.id == run_id, runs.c.user_id == user_id)
        ).mappings().first()
    if not row:
        return None
    result = json.loads(row["result_json"])
    result.setdefault("request", row["request"])
    result["thread_id"] = row["thread_id"]
    result["run_id"] = row["id"]
    result["publish"] = (
        {"kind": row["publish_kind"], "url": row["publish_url"]} if row["publish_url"] else None
    )
    return result


def set_publish(user_id: int, run_id: str, publish: dict[str, Any]) -> None:
    with _eng().begin() as conn:
        conn.execute(
            update(runs)
            .where(runs.c.id == run_id, runs.c.user_id == user_id)
            .values(publish_kind=publish.get("kind"), publish_url=publish.get("url"))
        )


def _today() -> str:
    from .timeutil import today_key

    return today_key()


def usage_today(user_id: int) -> dict[str, float]:
    """{'runs', 'cost_usd', 'tokens'} for this user since 00:00 UTC."""
    with _eng().connect() as conn:
        row = conn.execute(
            select(usage.c.runs, usage.c.cost_usd, usage.c.tokens)
            .where(usage.c.user_id == user_id, usage.c.day == _today())
        ).first()
    if not row:
        return {"runs": 0, "cost_usd": 0.0, "tokens": 0}
    return {"runs": int(row[0]), "cost_usd": float(row[1]), "tokens": int(row[2] or 0)}


def add_usage(user_id: int, cost_usd: float | None, tokens: int = 0) -> None:
    """Count one finished run (its cost, if known, and its tokens) against today's quota."""
    day, cost, tokens = _today(), float(cost_usd or 0.0), int(tokens or 0)

    def bump(conn):
        return conn.execute(
            update(usage)
            .where(usage.c.user_id == user_id, usage.c.day == day)
            .values(runs=usage.c.runs + 1, cost_usd=usage.c.cost_usd + cost, tokens=usage.c.tokens + tokens)
        )

    with _eng().begin() as conn:
        if bump(conn).rowcount:
            return
        try:
            conn.execute(insert(usage).values(user_id=user_id, day=day, runs=1, cost_usd=cost, tokens=tokens))
        except IntegrityError:  # another tab created today's row first
            bump(conn)


def delete_run(user_id: int, run_id: str) -> None:
    with _eng().begin() as conn:
        conn.execute(delete(runs).where(runs.c.id == run_id, runs.c.user_id == user_id))
