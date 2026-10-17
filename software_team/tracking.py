"""Token and cost tracking per agent (Deliverable 3), with runtime price lookup.

`UsageTracker` is a LangChain callback. Put it in the run config and every model
call made anywhere in the graph (also inside the agents' sub-graphs, and also
retried or cut-off calls) is reported to it. The agent name comes from the
`metadata={"agent": ...}` tag that graph.py puts on each agent's model.

`PriceBook` finds the price of whatever model was used: first a public online price
list (downloaded once a day and cached in .cache/), then the built-in PRICES table.
It never raises: if the network is down, the run just uses what it has.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler

# Built-in fallback prices, USD per 1 million tokens: (input, output).
# Used when the online lookup is off or unavailable. Check https://console.groq.com/docs/pricing
PRICES: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-20b": (0.075, 0.30),
}

AGENT_ORDER = ("supervisor", "product_manager", "developer", "reviewer")

# Community-maintained price list (the LiteLLM project). Not Groq's official page.
PRICE_LIST_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
CACHE_FILE = Path(__file__).resolve().parent.parent / ".cache" / "model_prices.json"


def parse_price_list(data: dict) -> dict[str, tuple[float, float]]:
    """Keep the Groq models of the list; convert USD per token into USD per 1M tokens."""
    prices: dict[str, tuple[float, float]] = {}
    for key, info in data.items():
        if not key.startswith("groq/") or not isinstance(info, dict):
            continue
        cost_in, cost_out = info.get("input_cost_per_token"), info.get("output_cost_per_token")
        if cost_in is None or cost_out is None:
            continue
        prices[key[len("groq/"):]] = (round(cost_in * 1e6, 6), round(cost_out * 1e6, 6))
    return prices


def fetch_price_list() -> dict[str, tuple[float, float]]:
    """Download the list (about 3 MB) and keep only the Groq prices."""
    with urllib.request.urlopen(PRICE_LIST_URL, timeout=8) as response:
        return parse_price_list(json.load(response))


def _age(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} days"


class PriceBook:
    """Finds a model's price: online list first (cached), then the built-in PRICES table.

    online=False (the default) never touches the network.
    fetch / now can be replaced in tests.
    """

    def __init__(
        self,
        online: bool = False,
        cache_file: Path | str = CACHE_FILE,
        max_age_hours: float = 24.0,
        fetch: Callable[[], dict[str, tuple[float, float]]] = fetch_price_list,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.online = online
        self.cache_file = Path(cache_file)
        self.max_age = max_age_hours * 3600
        self._fetch = fetch
        self._now = now
        self._table: dict[str, tuple[float, float]] = {}
        self._source = ""  # where the online table came from, for display
        self._loaded = False

    def lookup(self, model: str) -> tuple[float, float, str] | None:
        """(input price, output price, source) in USD per 1M tokens, or None if unknown."""
        if self.online and not self._loaded:
            self._load()
        if model in self._table:
            return (*self._table[model], self._source)
        if model in PRICES:
            return (*PRICES[model], "built-in table")
        return None

    def _load(self) -> None:
        self._loaded = True
        cached = self._read_cache()
        if cached and self._now() - cached[0] < self.max_age:
            self._table = cached[1]
            self._source = f"online price list, cached {_age(self._now() - cached[0])} ago"
            return
        try:
            prices = self._fetch()
            if not prices:
                raise ValueError("empty price list")
        except Exception:  # no network, timeout, bad JSON... never break the run for prices
            if cached:
                self._table = cached[1]
                self._source = (
                    f"online price list, cache is {_age(self._now() - cached[0])} old "
                    "(refresh failed)"
                )
            return  # nothing online: lookup() falls back to the built-in table
        self._table = prices
        self._source = "online price list, fetched now"
        self._write_cache(prices)

    def _read_cache(self) -> tuple[float, dict[str, tuple[float, float]]] | None:
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            prices = {m: (float(p[0]), float(p[1])) for m, p in data["prices"].items()}
            return float(data["fetched_at"]), prices
        except Exception:  # missing, unreadable or corrupt cache: ignore it
            return None

    def _write_cache(self, prices: dict[str, tuple[float, float]]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"fetched_at": self._now(), "prices": prices}
            self.cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass  # a cache that can't be written only means we download again next time


def cost_from_price(price: tuple[float, float] | None, input_tokens: int, output_tokens: int):
    """Cost in USD, or None when there is no price."""
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Cost with the built-in table only (no online lookup)."""
    return cost_from_price(PRICES.get(model), input_tokens, output_tokens)


class UsageTracker(BaseCallbackHandler):
    def __init__(self, price_book: PriceBook | None = None) -> None:
        self.price_book = price_book or PriceBook()  # default: built-in prices, no network
        self.calls: list[dict[str, Any]] = []  # one entry per model call
        self._agent_of_run: dict[Any, str] = {}
        self._lock = threading.Lock()

    # --- LangChain callback hooks ---------------------------------------------------
    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        self._agent_of_run[run_id] = (metadata or {}).get("agent", "unknown")

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._agent_of_run.pop(run_id, None)

    def on_llm_end(self, response, *, run_id, **kwargs):
        agent = self._agent_of_run.pop(run_id, "unknown")
        message = response.generations[0][0].message if response.generations else None
        usage = dict(getattr(message, "usage_metadata", None) or {})
        raw = (response.llm_output or {}).get("token_usage") or {}  # fallback: provider numbers
        input_tokens = usage.get("input_tokens", raw.get("prompt_tokens", 0)) or 0
        output_tokens = usage.get("output_tokens", raw.get("completion_tokens", 0)) or 0
        reasoning = (usage.get("output_token_details") or {}).get("reasoning", 0) or 0
        model = (getattr(message, "response_metadata", None) or {}).get("model_name") or (
            response.llm_output or {}
        ).get("model_name", "unknown")
        with self._lock:
            self.calls.append(
                {
                    "agent": agent,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,  # includes the thinking tokens
                    "reasoning_tokens": reasoning,
                }
            )

    # --- reporting ------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        """Totals per agent and overall, plus the prices used.

        cost_usd is None when a call's model has no known price. Prices are looked up
        here, once per model, after the run - never while the agents are working.
        """
        prices: dict[str, dict[str, Any] | None] = {}
        for model in sorted({c["model"] for c in self.calls}):
            found = self.price_book.lookup(model)
            prices[model] = (
                None
                if found is None
                else {"input_per_m": found[0], "output_per_m": found[1], "source": found[2]}
            )

        def blank() -> dict[str, Any]:
            return {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                    "reasoning_tokens": 0, "cost_usd": 0.0}

        agents: dict[str, dict[str, Any]] = {}
        total = blank()
        for call in self.calls:
            p = prices[call["model"]]
            cost = cost_from_price(
                None if p is None else (p["input_per_m"], p["output_per_m"]),
                call["input_tokens"],
                call["output_tokens"],
            )
            for bucket in (agents.setdefault(call["agent"], blank()), total):
                bucket["calls"] += 1
                for key in ("input_tokens", "output_tokens", "reasoning_tokens"):
                    bucket[key] += call[key]
                if bucket["cost_usd"] is not None:
                    bucket["cost_usd"] = None if cost is None else bucket["cost_usd"] + cost
        names = sorted(agents, key=lambda n: AGENT_ORDER.index(n) if n in AGENT_ORDER else 99)
        return {"agents": {n: agents[n] for n in names}, "total": total, "prices": prices}

    def format_table(self) -> str:
        """A plain-ASCII table for the console. Empty string if nothing was tracked."""
        if not self.calls:
            return ""
        summary = self.summary()
        money = lambda c: "n/a" if c is None else f"${c:.5f}"
        rows = [f"{'Agent':<17}{'Calls':>6}{'Input':>9}{'Output':>9}{'(thinking)':>12}{'Cost':>11}"]
        for name, s in [*summary["agents"].items(), ("TOTAL", summary["total"])]:
            rows.append(
                f"{name:<17}{s['calls']:>6}{s['input_tokens']:>9}{s['output_tokens']:>9}"
                f"{s['reasoning_tokens']:>12}{money(s['cost_usd']):>11}"
            )
        rows.insert(-1, "-" * len(rows[0]))
        unpriced = sorted(m for m, p in summary["prices"].items() if p is None)
        if unpriced:
            rows.append(f"No price known for: {', '.join(unpriced)} (add it to PRICES in tracking.py)")
        return "\n".join(rows)

    def format_prices(self) -> str:
        """Which price was used for each model, and where it came from."""
        lines = ["Prices used (USD per 1M tokens, input / output):"]
        for model, p in self.summary()["prices"].items():
            if p is None:
                lines.append(f"  {model}: unknown")
            else:
                lines.append(
                    f"  {model}: ${p['input_per_m']:g} / ${p['output_per_m']:g}  [{p['source']}]"
                )
        return "\n".join(lines)