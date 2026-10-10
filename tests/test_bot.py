"""Offline tests — no network calls."""
import json
import time

import pytest

from bot.config import Config
from bot.data import SOL_MINT, Pair, best_sol_pairs
from bot.executor import PaperExecutor
from bot.grok import Sentiment, _output_text, parse_sentiment
from bot.main import Bot
from bot.risk import Position, RiskManager
from bot.safety import SafetyResult, evaluate_rugcheck
from bot.strategies import MomentumStrategy, VolumeBreakoutStrategy


def make_pair(**kw) -> Pair:
    base = dict(
        token_address="TokenA", symbol="AAA", pair_address="P", dex="raydium",
        price_sol=0.001, price_usd=0.15, liquidity_usd=100_000, fdv=1_000_000,
        volume_m5=20_000, volume_h1=120_000, change_m5=3, change_h1=40, change_h6=80,
        buys_m5=60, sells_m5=30, buys_h1=500, sells_h1=300,
        created_at_ms=int((time.time() - 3 * 3600) * 1000),
    )
    base.update(kw)
    return Pair(**base)


@pytest.fixture
def cfg(tmp_path):
    c = Config()
    c.live_trading = False
    c.journal_path = str(tmp_path / "j.jsonl")
    c.paper_starting_sol = 1.0
    c.trade_size_sol = 0.1
    c.paper_cost_pct = 0
    c.stop_loss_pct, c.take_profit_pct, c.trailing_stop_pct = 25, 100, 30
    c.take_profit_sell_fraction = 0.5
    c.max_hold_minutes = 240
    c.max_daily_loss_sol = 0.5
    c.max_open_positions = 3
    c.xai_api_key = ""
    return c


# ---------------------------------------------------------------- data
def test_best_sol_pairs_picks_deepest_sol_pool():
    def raw(liq, quote=SOL_MINT, chain="solana"):
        return {"chainId": chain, "pairAddress": f"p{liq}", "dexId": "x", "priceNative": "0.01",
                "baseToken": {"address": "T", "symbol": "T"}, "quoteToken": {"address": quote},
                "liquidity": {"usd": liq}}
    out = best_sol_pairs([raw(10), raw(50), raw(999, quote="USDC"), raw(500, chain="base")])
    assert out["T"].liquidity_usd == 50


# ---------------------------------------------------------------- strategies
def test_momentum_signals_on_healthy_uptrend():
    assert MomentumStrategy().evaluate(make_pair()) is not None


@pytest.mark.parametrize("kw", [dict(change_h1=500), dict(change_m5=-2), dict(buys_m5=10, sells_m5=30),
                                dict(volume_h1=1000)])
def test_momentum_rejects(kw):
    assert MomentumStrategy().evaluate(make_pair(**kw)) is None


def test_volume_breakout():
    assert VolumeBreakoutStrategy().evaluate(make_pair(volume_m5=50_000, volume_h1=120_000)) is not None
    assert VolumeBreakoutStrategy().evaluate(make_pair(volume_m5=5_000)) is None


# ---------------------------------------------------------------- safety
def test_rugcheck_blocks_freeze_authority_and_danger():
    r = evaluate_rugcheck({"score": 100, "risks": [{"name": "Freeze Authority still enabled", "level": "warn"}]}, 1000)
    assert not r.ok
    r = evaluate_rugcheck({"score": 100, "risks": [{"name": "Something", "level": "danger"}]}, 1000)
    assert not r.ok
    assert evaluate_rugcheck({"score": 100, "risks": [{"name": "Low liquidity", "level": "warn"}]}, 1000).ok
    assert not evaluate_rugcheck({"score": 5000, "risks": []}, 1000).ok


# ---------------------------------------------------------------- risk
def test_exit_rules(cfg):
    rm = RiskManager(cfg)
    now = time.time()
    mk = lambda: Position("T", "T", 1.0, 0.1, 0.1, opened_at=now)
    assert rm.exit_decision(mk(), 0.74, now).reason.startswith("stop loss")
    d = rm.exit_decision(mk(), 2.0, now)
    assert d.reason.startswith("take profit") and d.fraction == 0.5
    p = mk(); p.take_profit_hit = True; p.peak_price_sol = 3.0
    assert rm.exit_decision(p, 2.0, now).reason.startswith("trailing")
    assert rm.exit_decision(mk(), 1.05, now + 241 * 60).reason.startswith("max hold")
    assert rm.exit_decision(mk(), 1.05, now) is None


def test_daily_loss_kill_switch(cfg):
    rm = RiskManager(cfg)
    assert rm.can_open(0, 1.0)[0]
    rm.record_realized(-0.6)
    ok, why = rm.can_open(0, 1.0)
    assert not ok and "daily loss" in why


# ---------------------------------------------------------------- grok parsing
def test_parse_sentiment():
    s = parse_sentiment('Here you go: {"score": 1.7, "summary": "ok", "red_flags": ["bots"], "mentions_estimate": 40}')
    assert s.score == 1.0 and s.red_flags == ["bots"]
    assert parse_sentiment("no json here") is None


def test_output_text_responses_shape():
    body = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"score\":0}"}]}]}
    assert _output_text(body) == '{"score":0}'


# ---------------------------------------------------------------- end-to-end paper loop
class FakeMarket:
    def __init__(self): self.pairs = {}
    def trending_token_addresses(self): return list(self.pairs)
    def pairs_for(self, addrs): return {a: self.pairs[a] for a in addrs if a in self.pairs}


class PassSafety:
    def check(self, pair): return SafetyResult(ok=True, rugcheck_score=50)


class FakeGrok:
    def __init__(self, score): self.s = score
    def score(self, sym, addr): return Sentiment(self.s, "test")


def test_paper_round_trip_profit(cfg):
    m = FakeMarket(); m.pairs["TokenA"] = make_pair()
    bot = Bot(cfg, m, MomentumStrategy(), PassSafety(), PaperExecutor(cfg))
    bot.tick()
    assert "TokenA" in bot.positions
    m.pairs["TokenA"] = make_pair(price_sol=0.0021)  # +110% -> take profit half
    bot.tick()
    assert bot.positions["TokenA"].take_profit_hit
    m.pairs["TokenA"] = make_pair(price_sol=0.0007, change_h1=-50)  # crash -> stop/trailing exits rest
    bot.tick()
    assert "TokenA" not in bot.positions
    events = [json.loads(l)["event"] for l in open(cfg.journal_path)]
    assert events.count("buy") == 1 and events.count("sell") == 2


def test_grok_veto(cfg):
    cfg.grok_min_score = 0.2
    m = FakeMarket(); m.pairs["TokenA"] = make_pair()
    bot = Bot(cfg, m, MomentumStrategy(), PassSafety(), PaperExecutor(cfg), grok=FakeGrok(-0.5))
    bot.tick()
    assert not bot.positions
