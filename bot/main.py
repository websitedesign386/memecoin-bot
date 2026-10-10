"""Main loop:  python -m bot.main [--once] [--strategy momentum]

Each tick:
  1. refresh prices for open positions -> apply exits (stop / take-profit / trailing / time)
  2. pull trending Solana tokens from DexScreener
  3. strategy signal -> safety (pool + RugCheck) -> optional Grok sentiment -> risk limits -> buy
Every action is appended to a JSONL journal so you can audit and measure results.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict
from typing import Callable

from .config import Config
from .data import DexScreener, Pair
from .executor import Executor, make_executor
from .grok import GrokSentiment
from .risk import Position, RiskManager
from .safety import SafetyChecker
from .strategies import Signal, Strategy, get_strategy

log = logging.getLogger("bot")


class Bot:
    def __init__(
        self,
        cfg: Config,
        market: DexScreener,
        strategy: Strategy,
        safety: SafetyChecker,
        executor: Executor,
        grok: GrokSentiment | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.cfg, self.market, self.strategy = cfg, market, strategy
        self.safety, self.executor, self.grok = safety, executor, grok
        self.risk = RiskManager(cfg)
        self.positions: dict[str, Position] = {}
        self.clock = clock
        self.rejected: set[str] = set()  # tokens that failed safety; don't re-check every tick

    # ------------------------------------------------------------------ journal
    def journal(self, event: str, **data) -> None:
        rec = {"ts": self.clock(), "event": event, "mode": "live" if self.cfg.live_trading else "paper", **data}
        with open(self.cfg.journal_path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        log.info("%s %s", event, {k: v for k, v in data.items() if k != "pair"})

    # ------------------------------------------------------------------ exits
    def manage_positions(self, prices: dict[str, Pair]) -> None:
        for addr, pos in list(self.positions.items()):
            pair = prices.get(addr)
            if not pair:
                continue
            decision = self.risk.exit_decision(pos, pair.price_sol, now=self.clock())
            if not decision:
                continue
            try:
                fill = self.executor.sell(pos, decision.fraction, pair)
            except Exception as e:  # keep running; retry next tick
                self.journal("sell_failed", symbol=pos.symbol, token=addr, error=repr(e))
                continue
            cost_basis = pos.cost_sol * (fill.tokens / pos.tokens) if pos.tokens else 0
            pnl = fill.sol_amount - cost_basis
            self.risk.record_realized(pnl)
            pos.cost_sol -= cost_basis
            pos.tokens -= fill.tokens
            pos.raw_amount -= fill.raw_amount
            pos.proceeds_sol += fill.sol_amount
            if decision.reason.startswith("take profit"):
                pos.take_profit_hit = True
            self.journal("sell", symbol=pos.symbol, token=addr, reason=decision.reason,
                         sol_received=round(fill.sol_amount, 6), pnl_sol=round(pnl, 6), tx=fill.tx)
            if decision.fraction >= 0.999 or pos.tokens <= 1e-12:
                del self.positions[addr]

    # ------------------------------------------------------------------ entries
    def _grok_ok(self, sig: Signal) -> tuple[bool, dict]:
        if not self.grok:
            return True, {}
        s = self.grok.score(sig.symbol, sig.token_address)
        if s is None:
            return True, {"grok": "unavailable"}
        info = {"grok_score": s.score, "grok_summary": s.summary, "grok_flags": s.red_flags}
        return s.score >= self.cfg.grok_min_score, info

    def find_entries(self, pairs: dict[str, Pair]) -> None:
        signals = [s for p in pairs.values()
                   if p.token_address not in self.positions and p.token_address not in self.rejected
                   and (s := self.strategy.evaluate(p))]
        signals.sort(key=lambda s: s.strength, reverse=True)

        for sig in signals:
            ok, why = self.risk.can_open(len(self.positions), self.executor.balance_sol())
            if not ok:
                self.journal("skip_all", reason=why)
                return
            pair = pairs[sig.token_address]
            safety = self.safety.check(pair)
            if not safety.ok:
                self.rejected.add(sig.token_address)
                self.journal("rejected", symbol=sig.symbol, token=sig.token_address, reasons=safety.reasons)
                continue
            grok_ok, grok_info = self._grok_ok(sig)
            if not grok_ok:
                self.journal("rejected", symbol=sig.symbol, token=sig.token_address, reasons=["grok"], **grok_info)
                continue
            try:
                fill = self.executor.buy(pair, self.cfg.trade_size_sol)
            except Exception as e:
                self.journal("buy_failed", symbol=sig.symbol, token=sig.token_address, error=repr(e))
                continue
            self.positions[sig.token_address] = Position(
                sig.token_address, sig.symbol, fill.price_sol, fill.sol_amount, fill.tokens,
                raw_amount=fill.raw_amount, opened_at=self.clock(),
            )
            self.journal("buy", symbol=sig.symbol, token=sig.token_address, reason=sig.reason,
                         sol_spent=fill.sol_amount, price_sol=fill.price_sol, tx=fill.tx,
                         rugcheck_score=safety.rugcheck_score, **grok_info)

    # ------------------------------------------------------------------ loop
    def tick(self) -> None:
        candidates = self.market.trending_token_addresses()
        tokens = list(dict.fromkeys(list(self.positions) + candidates))
        pairs = self.market.pairs_for(tokens)
        self.manage_positions(pairs)
        self.find_entries({a: p for a, p in pairs.items() if a in candidates})
        self.journal("tick", open_positions=len(self.positions),
                     balance_sol=round(self.executor.balance_sol(), 6),
                     realized_today_sol=round(self.risk.realized_today_sol, 6))


def main() -> None:
    ap = argparse.ArgumentParser(description="Solana memecoin bot (paper mode unless LIVE_TRADING=true)")
    ap.add_argument("--once", action="store_true", help="run a single tick and exit")
    ap.add_argument("--strategy", help="override STRATEGY env var")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config()
    if args.strategy:
        cfg.strategy = args.strategy

    if cfg.live_trading:
        log.warning("LIVE TRADING ENABLED — real funds at risk. Trade size %.3f SOL, daily loss cap %.3f SOL",
                    cfg.trade_size_sol, cfg.max_daily_loss_sol)
    bot = Bot(
        cfg, DexScreener(), get_strategy(cfg.strategy), SafetyChecker(cfg), make_executor(cfg),
        GrokSentiment(cfg) if cfg.grok_enabled else None,
    )
    log.info("config: %s", {k: v for k, v in asdict(cfg).items() if k not in ("private_key_b58", "xai_api_key")})
    while True:
        try:
            bot.tick()
        except Exception:
            log.exception("tick failed")
        if args.once:
            break
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
