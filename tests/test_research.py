"""Offline tests for the research pipeline (archive parsing, AMM math, backtest exits)."""
from collections import deque

import pytest

from research.backtest import Book, Exec, Exits, Token, buy_out, eff_reserves, sell_out
from research.ingest import rows_from_event

SOL = "So11111111111111111111111111111111111111112"


def test_rows_from_event_splits_router_legs_and_filters_pools():
    ev = {"action": "buy", "pool": "pump-amm", "quoteMint": SOL, "mint": "M", "poolId": "P", "txSigner": "R",
          "price": 1e-6, "quoteInPool": 100, "tokensInPool": 1e8, "timestamp": 5, "block": 9,
          "breakdown": [{"action": "buy", "trader": "A", "quoteAmount": 1, "tokenAmount": 10},
                        {"action": "buy", "trader": "B", "quoteAmount": 2, "tokenAmount": 20}]}
    rows = rows_from_event(ev)
    assert [r["trader"] for r in rows] == ["A", "B"] and rows[1]["sol"] == 2 and rows[0]["n_legs"] == 2
    assert rows_from_event({**ev, "pool": "raydium-v4"}) == []
    assert rows_from_event({**ev, "quoteMint": "USDC"}) == []


def test_eff_reserves_curve_vs_amm():
    curve = {"pool": "pump", "v_quote": 61.0, "v_tokens": 5e8, "quote_in_pool": 31.0, "tokens_in_pool": 2.4e8}
    assert eff_reserves(curve) == (61.0, 5e8)
    amm = {"pool": "pump-amm", "v_quote": 17.0, "v_tokens": -1.0, "quote_in_pool": 67.0, "tokens_in_pool": 2e8}
    assert eff_reserves(amm) == (84.0, 2e8 - 1)


def test_constant_product_round_trip_loses_fees_and_impact():
    q, t, fee = 100.0, 1e8, 0.01
    got = buy_out(q, t, 1.0, fee)
    back = sell_out(q + 1.0 * (1 - fee), t - got, got, fee)
    assert 0.97 < back < 0.99  # ~2% fees + tiny impact


def _token(price_q: float) -> Token:
    tk = Token("M", creator="dev")
    tk.q, tk.t, tk.fee = price_q, 1e8, 0.0
    tk.price = tk.q / tk.t
    tk.balances = {"dev": 1e7}
    return tk


def test_book_fills_after_latency_then_takes_initials_and_stops():
    b = Book("x", lambda tk, now: "sig", Exec(size_sol=0.1, latency_s=1.5, tx_cost_sol=0), Exits())
    tk = _token(100.0)
    b.maybe_enter(tk, 0)
    b.on_event(tk, 1000, False)           # too early: still pending
    assert "M" in b.pending
    b.on_event(tk, 1600, False)           # filled
    assert "M" in b.open
    tk.q = 210.0; tk.price = tk.q / tk.t  # +110% -> sell half
    b.on_event(tk, 2000, False)
    assert b.open["M"].initials_taken
    tk.q = 120.0; tk.price = tk.q / tk.t  # falls from peak by > 30% -> trailing exit
    b.on_event(tk, 3000, False)
    assert not b.open and b.closed[0].exit_reason.startswith("trail")
    assert b.closed[0].pnl > 0


def test_creator_dump_exit():
    b = Book("x", lambda tk, now: "sig", Exec(size_sol=0.1, latency_s=0, tx_cost_sol=0), Exits())
    tk = _token(100.0)
    b.maybe_enter(tk, 0)
    b.on_event(tk, 0, False)
    tk.balances["dev"] = 1e6              # creator sold 90%
    b.on_event(tk, 10, True)
    assert b.closed and b.closed[0].exit_reason.startswith("creator dump")
