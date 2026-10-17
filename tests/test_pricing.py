"""Runtime price lookup: online list first, cache, built-in fallback. Never uses the network."""
import json

import pytest

from conftest import make_fake_llm
from software_team.config import get_settings
from software_team.graph import build_team_graph, run_team
from software_team.tracking import (
    PRICES,
    PriceBook,
    UsageTracker,
    parse_price_list,
)
from test_tracking import FakeUsageChat

MODEL = "openai/gpt-oss-20b"
HOUR = 3600


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Fetcher:
    """A fake download: returns the given prices (or raises) and counts the calls."""

    def __init__(self, prices=None, error=None):
        self.prices, self.error, self.calls = prices, error, 0

    def __call__(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.prices


def book(tmp_path, fetch, clock=None, online=True, **kw):
    return PriceBook(online=online, cache_file=tmp_path / "prices.json", fetch=fetch,
                     now=clock or Clock(), **kw)


def test_parse_keeps_groq_models_and_converts_to_per_million():
    data = {
        "groq/openai/gpt-oss-20b": {"input_cost_per_token": 7.5e-08, "output_cost_per_token": 3e-07},
        "groq/llama-3.3-70b-versatile": {"input_cost_per_token": 5.9e-07, "output_cost_per_token": 7.9e-07},
        "gpt-4o": {"input_cost_per_token": 2.5e-06, "output_cost_per_token": 1e-05},  # not Groq
        "groq/no-prices": {"max_tokens": 100},
        "sample_spec": "not a dict",
    }
    assert parse_price_list(data) == {
        "openai/gpt-oss-20b": (0.075, 0.3),
        "llama-3.3-70b-versatile": (0.59, 0.79),
    }


def test_online_price_wins_over_the_built_in_table(tmp_path):
    pb = book(tmp_path, Fetcher({MODEL: (0.5, 2.0)}))
    assert pb.lookup(MODEL) == (0.5, 2.0, "online price list, fetched now")


def test_any_model_in_the_online_list_gets_a_price(tmp_path):
    pb = book(tmp_path, Fetcher({"brand/new-model": (1.0, 4.0)}))
    assert pb.lookup("brand/new-model")[:2] == (1.0, 4.0)
    assert "brand/new-model" not in PRICES  # it was NOT in the built-in table


def test_offline_mode_never_downloads(tmp_path):
    fetcher = Fetcher({MODEL: (9, 9)})
    pb = book(tmp_path, fetcher, online=False)
    assert pb.lookup(MODEL) == (*PRICES[MODEL], "built-in table")
    assert fetcher.calls == 0


def test_the_download_is_cached_and_reused(tmp_path):
    clock, fetcher = Clock(), Fetcher({MODEL: (0.5, 2.0)})
    book(tmp_path, fetcher, clock).lookup(MODEL)
    clock.t += 3 * HOUR
    second = book(tmp_path, fetcher, clock)  # a new run, a new PriceBook
    price = second.lookup(MODEL)
    assert fetcher.calls == 1  # no second download
    assert price == (0.5, 2.0, "online price list, cached 3 h ago")


def test_an_old_cache_is_refreshed(tmp_path):
    clock, fetcher = Clock(), Fetcher({MODEL: (0.5, 2.0)})
    book(tmp_path, fetcher, clock).lookup(MODEL)
    clock.t += 25 * HOUR
    fetcher.prices = {MODEL: (0.6, 2.4)}
    assert book(tmp_path, fetcher, clock).lookup(MODEL)[:2] == (0.6, 2.4)
    assert fetcher.calls == 2


def test_failed_download_without_cache_falls_back_to_built_in(tmp_path):
    pb = book(tmp_path, Fetcher(error=OSError("no network")))
    assert pb.lookup(MODEL) == (*PRICES[MODEL], "built-in table")
    assert pb.lookup("brand/new-model") is None


def test_failed_download_with_an_old_cache_uses_the_old_cache(tmp_path):
    clock, fetcher = Clock(), Fetcher({MODEL: (0.5, 2.0)})
    book(tmp_path, fetcher, clock).lookup(MODEL)
    clock.t += 30 * HOUR
    fetcher.error = OSError("no network")
    price = book(tmp_path, fetcher, clock).lookup(MODEL)
    assert price[:2] == (0.5, 2.0)
    assert "refresh failed" in price[2]


@pytest.mark.parametrize("bad", ["not json", '{"fetched_at": 1}', '{"prices": 5}', ""])
def test_a_corrupt_cache_is_ignored(tmp_path, bad):
    (tmp_path / "prices.json").write_text(bad, encoding="utf-8")
    pb = book(tmp_path, Fetcher({MODEL: (0.5, 2.0)}))
    assert pb.lookup(MODEL)[:2] == (0.5, 2.0)


def test_an_empty_download_is_treated_as_a_failure(tmp_path):
    pb = book(tmp_path, Fetcher({}))
    assert pb.lookup(MODEL) == (*PRICES[MODEL], "built-in table")
    assert not (tmp_path / "prices.json").exists()  # an empty list is not cached


def test_unwritable_cache_location_does_not_break_anything(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    pb = PriceBook(online=True, cache_file=blocker / "sub" / "prices.json",
                   fetch=Fetcher({MODEL: (0.5, 2.0)}), now=Clock())
    assert pb.lookup(MODEL)[:2] == (0.5, 2.0)


def test_cache_file_holds_only_what_we_need(tmp_path):
    book(tmp_path, Fetcher({MODEL: (0.5, 2.0)})).lookup(MODEL)
    saved = json.loads((tmp_path / "prices.json").read_text(encoding="utf-8"))
    assert set(saved) == {"fetched_at", "prices"}
    assert saved["prices"] == {MODEL: [0.5, 2.0]}


def test_the_tracker_prices_an_unknown_model_from_the_online_list(tmp_path):
    graph = build_team_graph(
        llm_factory=lambda name: FakeUsageChat(inner=make_fake_llm(), model_name="brand/new-model"),
        supervisor_mode="rules",
    )
    tracker = UsageTracker(book(tmp_path, Fetcher({"brand/new-model": (1.0, 4.0)})))
    run_team("x", graph=graph, config={"callbacks": [tracker]})

    total = tracker.summary()["total"]  # 5 calls x (100 in, 50 out)
    assert total["cost_usd"] == pytest.approx((500 * 1.0 + 250 * 4.0) / 1e6)
    assert "n/a" not in tracker.format_table()
    prices_text = tracker.format_prices()
    assert "brand/new-model: $1 / $4" in prices_text and "online price list" in prices_text


def test_the_tracker_says_when_it_used_the_built_in_table(tmp_path):
    graph = build_team_graph(llm_factory=lambda name: FakeUsageChat(inner=make_fake_llm()),
                             supervisor_mode="rules")
    tracker = UsageTracker(book(tmp_path, Fetcher(error=OSError()), online=True))
    run_team("x", graph=graph, config={"callbacks": [tracker]})
    assert "[built-in table]" in tracker.format_prices()
    assert tracker.summary()["prices"][MODEL]["source"] == "built-in table"


def test_prices_are_looked_up_after_the_run_and_only_once_per_model(tmp_path):
    fetcher = Fetcher({MODEL: (0.5, 2.0)})
    graph = build_team_graph(llm_factory=lambda name: FakeUsageChat(inner=make_fake_llm()),
                             supervisor_mode="rules")
    tracker = UsageTracker(book(tmp_path, fetcher))
    run_team("x", graph=graph, config={"callbacks": [tracker]})
    assert fetcher.calls == 0  # nothing downloaded while the agents were working
    tracker.summary(), tracker.format_table(), tracker.format_prices()
    assert fetcher.calls == 1


def test_price_lookup_setting(monkeypatch):
    monkeypatch.delenv("PRICE_LOOKUP", raising=False)
    assert get_settings().price_lookup is True  # on by default
    for off in ("off", "OFF", "false", "0", "no"):
        monkeypatch.setenv("PRICE_LOOKUP", off)
        assert get_settings().price_lookup is False
    monkeypatch.setenv("PRICE_LOOKUP", "on")
    assert get_settings().price_lookup is True