"""Event-driven backtest of trencher-style strategies over the PumpAPI archive.

    python -m research.backtest                       # all downloaded hours
    python -m research.backtest --split               # report first half vs second half separately

How it works (all causal — the strategy only sees events up to "now"):
  * Replays every pump.fun bonding-curve trade and every PumpSwap trade on pump-graduated pools, in time order.
  * Keeps live state per token: reserves, price, rolling 1m/5m/60m flow, unique buyers, holder balances,
    creator balance, launch-block bundle share, curve progress, migration time.
  * Strategies emit a buy signal; the fill happens LATENCY seconds later at the pool state at that moment,
    using constant-product math on the real reserves plus the pool's own fee rate and our tx costs.
  * Exits are checked on every later trade of that token (= a real-time stream): stop loss, take initials,
    trailing stop, max hold, and "creator dumped" exit.
"""
from __future__ import annotations

import argparse
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pyarrow.parquet as pq
import pyarrow.compute as pc

DATA = Path("data/replay")
COLS = ["ts", "block", "action", "pool", "mint", "trader", "sol", "tokens", "price", "mcap_sol",
        "quote_in_pool", "tokens_in_pool", "v_quote", "v_tokens", "fee_rate", "supply", "symbol",
        "creator", "mint_auth", "freeze_auth", "pool_created_by"]


# ----------------------------------------------------------------------------- config
@dataclass
class Exec:
    size_sol: float = 0.045          # ≈ $5 at ~$111/SOL
    latency_s: float = 1.5           # signal -> our tx executes
    tx_cost_sol: float = 0.0001      # priority fee + tip + base fee, per transaction
    max_open: int = 3


@dataclass
class Exits:
    stop_loss: float = 0.25          # -25% from entry
    take_initials_at: float = 1.0    # +100%
    take_initials_frac: float = 0.5
    trail: float = 0.30              # trailing stop from peak ...
    trail_after: float = 0.40        # ... once peak is +40% over entry
    max_hold_s: float = 4 * 3600
    creator_dump_frac: float = 0.5   # exit if creator sells >= 50% of what they held at our entry


# ----------------------------------------------------------------------------- token state
@dataclass
class Token:
    mint: str
    symbol: str = ""
    creator: str | None = None
    created_ts: int | None = None
    create_block: int | None = None
    migrated_ts: int | None = None
    migrate_price: float | None = None
    stage: str = "curve"             # curve | amm
    supply: float = 1e9
    q: float = 0.0                   # effective SOL reserve
    t: float = 0.0                   # effective token reserve
    real_q: float = 0.0
    fee: float = 0.0125
    price: float = 0.0
    mcap: float = 0.0
    high_since_mig: float = 0.0
    flow: deque = field(default_factory=deque)   # (ts, is_buy, sol, trader, price) for the last 60 min
    balances: dict = field(default_factory=dict)  # trader -> tokens (from observed trades only)
    bundle_tokens: float = 0.0
    last_eval: int = 0
    unsafe_auth: bool = False
    smart_buys: deque = field(default_factory=deque)  # (ts, wallet) buys by known pro wallets

    def window(self, now: int, secs: int):
        lo = now - secs * 1000
        for ev in reversed(self.flow):
            if ev[0] < lo:
                break
            yield ev

    def price_ago(self, now: int, secs: int) -> float | None:
        lo = now - secs * 1000
        p = None
        for ev in reversed(self.flow):
            if ev[0] < lo:
                return ev[4]
            p = ev[4]
        return p if self.flow and self.flow[0][0] <= lo + 60_000 else None  # need history that far back

    def stats(self, now: int, secs: int) -> dict:
        buy_sol = sell_sol = 0.0
        buyers = set()
        n = 0
        for ts, is_buy, sol, trader, _ in self.window(now, secs):
            n += 1
            if is_buy:
                buy_sol += sol
                buyers.add(trader)
            else:
                sell_sol += sol
        return {"buy_sol": buy_sol, "sell_sol": sell_sol, "buyers": len(buyers), "n": n}

    def creator_pct(self) -> float:
        return self.balances.get(self.creator, 0.0) / self.supply if self.creator else 0.0

    def top10_pct(self) -> float:
        top = sorted((v for v in self.balances.values() if v > 0), reverse=True)[:10]
        return sum(top) / self.supply

    def bundle_pct(self) -> float:
        return self.bundle_tokens / self.supply


def eff_reserves(r: dict) -> tuple[float, float]:
    if r["pool"] == "pump":
        return r["v_quote"] or 0.0, r["v_tokens"] or 0.0
    return (r["quote_in_pool"] or 0.0) + (r["v_quote"] or 0.0), (r["tokens_in_pool"] or 0.0) + (r["v_tokens"] or 0.0)


def buy_out(q: float, t: float, sol: float, fee: float) -> float:
    dx = sol * (1 - fee)
    return t * dx / (q + dx) if q > 0 else 0.0


def sell_out(q: float, t: float, tokens: float, fee: float) -> float:
    return (q * tokens / (t + tokens)) * (1 - fee) if t > 0 else 0.0


# ----------------------------------------------------------------------------- strategies
GRAD_Q = 85.0  # approx. real SOL in the pump.fun curve at graduation (checked against data in report)
MIN = 60_000


def common_filters(tk: Token) -> bool:
    return (not tk.unsafe_auth and tk.creator_pct() <= 0.10 and tk.top10_pct() <= 0.40
            and tk.bundle_pct() <= 0.30)


def migrated_momentum(tk: Token, now: int) -> str | None:
    if tk.stage != "amm" or tk.migrated_ts is None:
        return None
    age = now - tk.migrated_ts
    if not (5 * MIN <= age <= 6 * 60 * MIN) or not (300 <= tk.mcap <= 20_000):
        return None
    p5, p60 = tk.price_ago(now, 300), tk.price_ago(now, 3600) or tk.migrate_price
    if not p5 or not p60:
        return None
    ch5, ch60 = tk.price / p5 - 1, tk.price / p60 - 1
    if ch5 < 0.10 or not (0.20 <= ch60 <= 3.0):
        return None
    s = tk.stats(now, 300)
    if s["buyers"] < 15 or s["buy_sol"] < 1.3 * max(s["sell_sol"], 1e-9):
        return None
    if not common_filters(tk):
        return None
    return f"5m +{ch5:.0%} 60m +{ch60:.0%} buyers5m {s['buyers']}"


def first_pullback(tk: Token, now: int) -> str | None:
    if tk.stage != "amm" or tk.migrated_ts is None or not tk.migrate_price:
        return None
    if now - tk.migrated_ts < 10 * MIN or not (300 <= tk.mcap <= 20_000):
        return None
    if tk.high_since_mig < 2.0 * tk.migrate_price:
        return None
    dd = 1 - tk.price / tk.high_since_mig
    if not (0.20 <= dd <= 0.45):
        return None
    p2 = tk.price_ago(now, 120)
    if not p2 or tk.price <= p2:
        return None
    s = tk.stats(now, 120)
    if s["buyers"] < 8 or s["buy_sol"] < 1.2 * max(s["sell_sol"], 1e-9):
        return None
    if not common_filters(tk):
        return None
    return f"pullback {dd:.0%} from {tk.high_since_mig / tk.migrate_price:.1f}x"


def final_stretch(tk: Token, now: int) -> str | None:
    if tk.stage != "curve" or tk.created_ts is None:
        return None
    prog = tk.real_q / GRAD_Q
    if not (0.80 <= prog <= 0.95) or now - tk.created_ts < 3 * MIN:
        return None
    s = tk.stats(now, 300)
    if s["buyers"] < 20 or s["buy_sol"] <= s["sell_sol"]:
        return None
    if not common_filters(tk):
        return None
    return f"curve {prog:.0%} buyers5m {s['buyers']}"


def early_trench(tk: Token, now: int) -> str | None:
    """Mirror the profitable-wallet profile: 1.5-10 min old, 100-800 SOL mcap, broad organic buying."""
    if tk.created_ts is None:
        return None
    age = now - tk.created_ts
    if not (1.5 * MIN <= age <= 10 * MIN) or not (100 <= tk.mcap <= 800):
        return None
    s1, s5 = tk.stats(now, 60), tk.stats(now, 300)
    if s5["buyers"] < 25 or s1["buy_sol"] < 1.5 * max(s1["sell_sol"], 1e-9) or s1["buyers"] < 6:
        return None
    if not common_filters(tk):
        return None
    return f"age {age / MIN:.1f}m mcap {tk.mcap:.0f} buyers5m {s5['buyers']}"


def smart_convergence(tk: Token, now: int) -> str | None:
    """>= 2 distinct known-profitable wallets bought within the last 5 minutes."""
    lo = now - 5 * MIN
    while tk.smart_buys and tk.smart_buys[0][0] < lo:
        tk.smart_buys.popleft()
    wallets = {w for _, w in tk.smart_buys}
    if len(wallets) < 2 or not (60 <= tk.mcap <= 3000):
        return None
    if not common_filters(tk):
        return None
    return f"{len(wallets)} smart wallets in 5m, mcap {tk.mcap:.0f}"


SMART: set[str] = set()

STRATEGIES: dict[str, Callable[[Token, int], str | None]] = {
    "early_trench": early_trench,
    "smart_convergence": smart_convergence,
    "migrated_momentum": migrated_momentum,
    "first_pullback": first_pullback,
    "final_stretch": final_stretch,
}


# ----------------------------------------------------------------------------- simulation
@dataclass
class Pos:
    mint: str
    symbol: str
    reason: str
    signal_ts: int
    entry_ts: int | None = None
    entry_price: float = 0.0
    tokens: float = 0.0
    cost_sol: float = 0.0
    proceeds_sol: float = 0.0
    peak: float = 0.0
    initials_taken: bool = False
    creator_bal_at_entry: float = 0.0
    exit_ts: int | None = None
    exit_reason: str = ""

    @property
    def pnl(self) -> float:
        return self.proceeds_sol - self.cost_sol


class Book:
    def __init__(self, name: str, fn, ex: Exec, xs: Exits):
        self.name, self.fn, self.ex, self.xs = name, fn, ex, xs
        self.pending: dict[str, Pos] = {}
        self.open: dict[str, Pos] = {}
        self.closed: list[Pos] = []
        self.traded: set[str] = set()   # one entry per token

    def n_active(self):
        return len(self.pending) + len(self.open)

    def sell(self, p: Pos, tk: Token, frac: float, now: int, reason: str):
        amt = p.tokens * frac
        p.proceeds_sol += sell_out(tk.q, tk.t, amt, tk.fee) - self.ex.tx_cost_sol
        p.tokens -= amt
        if frac >= 0.999:
            p.exit_ts, p.exit_reason = now, reason
            self.closed.append(self.open.pop(p.mint))

    def on_event(self, tk: Token, now: int, creator_sold: bool):
        # fill a pending entry once the latency has elapsed (state = latest event at/after fill time)
        p = self.pending.get(tk.mint)
        if p and now >= p.signal_ts + self.ex.latency_s * 1000:
            del self.pending[tk.mint]
            got = buy_out(tk.q, tk.t, self.ex.size_sol, tk.fee)
            if got > 0:
                p.entry_ts, p.tokens = now, got
                p.cost_sol = self.ex.size_sol + self.ex.tx_cost_sol
                p.entry_price = p.peak = self.ex.size_sol / got
                p.creator_bal_at_entry = tk.balances.get(tk.creator, 0.0) if tk.creator else 0.0
                self.open[tk.mint] = p
            return
        p = self.open.get(tk.mint)
        if not p or tk.price <= 0:
            return
        xs = self.xs
        p.peak = max(p.peak, tk.price)
        ch = tk.price / p.entry_price - 1
        if ch <= -xs.stop_loss:
            return self.sell(p, tk, 1.0, now, f"stop {ch:+.0%}")
        if creator_sold and p.creator_bal_at_entry > 0:
            left = tk.balances.get(tk.creator, 0.0)
            if left <= p.creator_bal_at_entry * (1 - xs.creator_dump_frac):
                return self.sell(p, tk, 1.0, now, f"creator dump {ch:+.0%}")
        if not p.initials_taken and ch >= xs.take_initials_at:
            p.initials_taken = True
            return self.sell(p, tk, xs.take_initials_frac, now, "initials")
        if p.peak >= p.entry_price * (1 + xs.trail_after) and tk.price <= p.peak * (1 - xs.trail):
            return self.sell(p, tk, 1.0, now, f"trail {ch:+.0%}")
        if now - p.entry_ts >= xs.max_hold_s * 1000:
            return self.sell(p, tk, 1.0, now, f"time {ch:+.0%}")

    def maybe_enter(self, tk: Token, now: int):
        if tk.mint in self.traded or self.n_active() >= self.ex.max_open:
            return
        reason = self.fn(tk, now)
        if reason:
            self.traded.add(tk.mint)
            self.pending[tk.mint] = Pos(tk.mint, tk.symbol, reason, now)

    def close_all(self, tokens: dict[str, Token], now: int):
        for m, p in list(self.open.items()):
            self.sell(p, tokens[m], 1.0, now, "end of data")


def hour_files(start: str | None = None, end: str | None = None) -> list[Path]:
    files = sorted(DATA.glob("*/*.parquet"))
    key = lambda f: f"{f.parent.name}T{f.stem}"
    return [f for f in files if (not start or key(f) >= start) and (not end or key(f) < end)]


def run(files: list[Path], ex: Exec, xs: Exits, strategies=STRATEGIES, eval_every_ms: int = 2000,
        books: dict[str, Book] | None = None) -> dict[str, Book]:
    books = books or {n: Book(n, fn, ex, xs) for n, fn in strategies.items()}
    tokens: dict[str, Token] = {}
    now = 0
    for f in files:
        tbl = pq.read_table(f, columns=COLS)
        # or_kleene: a null pool_created_by (all curve rows) must not null-out the OR
        keep = pc.or_kleene(pc.equal(tbl["pool"], "pump"), pc.equal(tbl["pool_created_by"], "pump"))
        tbl = tbl.filter(keep).sort_by("ts")
        for r in tbl.to_pylist():
            now = r["ts"]
            m = r["mint"]
            tk = tokens.get(m)
            act = r["action"]
            if tk is None:
                tk = tokens[m] = Token(m)
            if act == "create":
                tk.symbol, tk.creator, tk.created_ts, tk.create_block = r["symbol"] or "", r["creator"], now, r["block"]
                tk.unsafe_auth = bool(r["mint_auth"] or r["freeze_auth"])
            elif act == "migrate":
                tk.stage, tk.migrated_ts = "amm", now
                tk.symbol = tk.symbol or (r["symbol"] or "")
            if r["supply"]:
                tk.supply = r["supply"]
            q, t = eff_reserves(r)
            if q > 0 and t > 0:
                tk.q, tk.t = q, t
                tk.price = q / t
                tk.real_q = r["quote_in_pool"] or 0.0
                tk.fee = r["fee_rate"] if r["fee_rate"] is not None else tk.fee
                tk.mcap = tk.price * tk.supply
            if act == "migrate":
                tk.migrate_price = tk.price
                tk.high_since_mig = tk.price
            if tk.stage == "amm":
                tk.high_since_mig = max(tk.high_since_mig, tk.price)

            creator_sold = False
            if act in ("buy", "sell") and r["tokens"]:
                is_buy = act == "buy"
                trader = r["trader"]
                tk.balances[trader] = tk.balances.get(trader, 0.0) + (r["tokens"] if is_buy else -r["tokens"])
                if is_buy and tk.create_block is not None and r["block"] == tk.create_block:
                    tk.bundle_tokens += r["tokens"]
                creator_sold = (not is_buy) and trader == tk.creator
                if is_buy and trader in SMART:
                    tk.smart_buys.append((now, trader))
                tk.flow.append((now, is_buy, r["sol"] or 0.0, trader, tk.price))
                lo = now - 3600_000
                while tk.flow and tk.flow[0][0] < lo:
                    tk.flow.popleft()

            for b in books.values():
                b.on_event(tk, now, creator_sold)
            if act in ("buy", "sell") and now - tk.last_eval >= eval_every_ms:
                tk.last_eval = now
                for b in books.values():
                    b.maybe_enter(tk, now)
        print(f"  replayed {f.parent.name} {f.stem}h  tokens={len(tokens)}", flush=True)
    for b in books.values():
        b.close_all(tokens, now)
    return books


# ----------------------------------------------------------------------------- report
def summarize(b: Book, size: float) -> dict:
    ps = b.closed
    n = len(ps)
    if not n:
        return {"strategy": b.name, "trades": 0}
    wins = [p for p in ps if p.pnl > 0]
    losses = [p for p in ps if p.pnl <= 0]
    rets = [p.pnl / p.cost_sol for p in ps]
    eq = peak = dd = 0.0
    for p in sorted(ps, key=lambda p: p.exit_ts):
        eq += p.pnl
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    reasons: dict[str, int] = {}
    for p in ps:
        k = p.exit_reason.split()[0]
        reasons[k] = reasons.get(k, 0) + 1
    return {
        "strategy": b.name, "trades": n, "win_rate": len(wins) / n,
        "avg_win": sum(p.pnl / p.cost_sol for p in wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(p.pnl / p.cost_sol for p in losses) / len(losses) if losses else 0.0,
        "ev_per_trade": sum(rets) / n, "median_ret": sorted(rets)[n // 2],
        "total_sol": sum(p.pnl for p in ps), "max_dd_sol": dd,
        "best": max(rets), "worst": min(rets), "exits": reasons,
    }


def fmt(s: dict) -> str:
    if not s.get("trades"):
        return f"{s['strategy']:<26} no trades"
    return (f"{s['strategy']:<26} trades={s['trades']:<4} win={s['win_rate']:.0%}  avg_win={s['avg_win']:+.0%} "
            f"avg_loss={s['avg_loss']:+.0%}  EV/trade={s['ev_per_trade']:+.1%}  median={s['median_ret']:+.0%}  "
            f"total={s['total_sol']:+.3f} SOL  maxDD={s['max_dd_sol']:.3f}  best={s['best']:+.0%} worst={s['worst']:+.0%}  exits={s['exits']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--split", action="store_true", help="report first and second half separately")
    ap.add_argument("--latency", type=float, default=1.5)
    ap.add_argument("--sweep", action="store_true", help="exit-rule grid for early_trench and final_stretch")
    ap.add_argument("--smart", help="CSV of pro wallets (index = wallet) for smart_convergence; build it from EARLIER hours only")
    a = ap.parse_args()
    files = hour_files(a.start, a.end)
    if not files:
        raise SystemExit("no data — run python -m research.ingest first")
    ex, xs = Exec(latency_s=a.latency), Exits()
    if a.smart:
        import csv
        with open(a.smart) as fh:
            SMART.update(row[0] for i, row in enumerate(csv.reader(fh)) if i and row)
        print(f"loaded {len(SMART)} smart wallets")
    print(f"{len(files)} hours: {files[0].parent.name} {files[0].stem}h → {files[-1].parent.name} {files[-1].stem}h, "
          f"size {ex.size_sol} SOL, latency {ex.latency_s}s")
    if a.sweep:
        grid = {"base": Exits(), "sl40": Exits(stop_loss=0.40), "sl60": Exits(stop_loss=0.60),
                "sl40_init50": Exits(stop_loss=0.40, take_initials_at=0.5),
                "sl40_trail45": Exits(stop_loss=0.40, trail=0.45, trail_after=0.6),
                "sl25_init50": Exits(take_initials_at=0.5), "sl40_hold30m": Exits(stop_loss=0.40, max_hold_s=1800)}
        books = {f"{sn}/{gn}": Book(f"{sn}/{gn}", STRATEGIES[sn], ex, g)
                 for sn in ("early_trench", "final_stretch") for gn, g in grid.items()}
        run(files, ex, xs, books=books)
        print()
        for b in books.values():
            print(fmt(summarize(b, ex.size_sol)))
        return
    parts = [files[: len(files) // 2], files[len(files) // 2:]] if a.split else [files]
    for i, part in enumerate(parts):
        if a.split:
            print(f"\n=== {'first' if i == 0 else 'second'} half ({len(part)} h) ===")
        books = run(part, ex, xs)
        print()
        for b in books.values():
            print(fmt(summarize(b, ex.size_sol)))


if __name__ == "__main__":
    main()
