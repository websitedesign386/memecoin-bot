"""Study how the most profitable wallets trade pump.fun / PumpSwap tokens.

    python -m research.wallets [--kol path/to/kolscan-leaderboard.json]

For every (wallet, token) it computes SOL in / out, entry context (stage, market cap, token age, minutes
since migration), and hold time. Remaining tokens are marked to the last observed price.
Then it ranks wallets and compares the behaviour of profitable "trencher-like" wallets with everyone else.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .backtest import DATA

COLS = ["ts", "action", "pool", "mint", "trader", "sol", "tokens", "mcap_sol", "price", "pool_created_by", "creator"]


def load_positions(files: list[Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    parts, events, last_px = [], [], []
    for f in files:
        t = pq.read_table(f, columns=COLS).to_pandas()
        t = t[(t.pool == "pump") | (t.pool_created_by == "pump")]
        events.append(t[t.action.isin(["create", "migrate"])][["ts", "action", "mint", "creator"]])
        tr = t[t.action.isin(["buy", "sell"]) & t.trader.notna() & (t.sol > 0)]
        last_px.append(tr.sort_values("ts").groupby("mint").price.last())
        b = tr.action == "buy"
        tr = tr.assign(buy_sol=np.where(b, tr.sol, 0.0), sell_sol=np.where(b, 0.0, tr.sol),
                       buy_tok=np.where(b, tr.tokens, 0.0), sell_tok=np.where(b, 0.0, tr.tokens),
                       buy_ts=np.where(b, tr.ts, np.nan), sell_ts=np.where(b, np.nan, tr.ts),
                       stage=np.where(tr.pool == "pump", "curve", "amm"), is_buy=b.astype(int), is_sell=(~b).astype(int))
        tr = tr.sort_values("ts")
        g = tr.groupby(["trader", "mint"])
        agg = g.agg(buy_sol=("buy_sol", "sum"), sell_sol=("sell_sol", "sum"), buy_tok=("buy_tok", "sum"),
                    sell_tok=("sell_tok", "sum"), n_buys=("is_buy", "sum"),
                    n_sells=("is_sell", "sum"), first_buy=("buy_ts", "min"),
                    last_sell=("sell_ts", "max"))
        first = tr[tr.action == "buy"].groupby(["trader", "mint"]).first()[["mcap_sol", "stage"]]
        parts.append(agg.join(first.rename(columns={"mcap_sol": "entry_mcap", "stage": "entry_stage"})))
        print(f"  {f.parent.name} {f.stem}h", flush=True)

    pos = pd.concat(parts)
    # combine hours: sums for flows, min/max for timestamps, first-seen entry context
    pos = pos.reset_index().sort_values("first_buy").groupby(["trader", "mint"]).agg(
        buy_sol=("buy_sol", "sum"), sell_sol=("sell_sol", "sum"), buy_tok=("buy_tok", "sum"), sell_tok=("sell_tok", "sum"),
        n_buys=("n_buys", "sum"), n_sells=("n_sells", "sum"), first_buy=("first_buy", "min"), last_sell=("last_sell", "max"),
        entry_mcap=("entry_mcap", "first"), entry_stage=("entry_stage", "first")).reset_index()
    ev = pd.concat(events)
    px = pd.concat(last_px).groupby(level=0).last()
    return pos, ev.assign(px=None).drop(columns="px"), px


def enrich(pos: pd.DataFrame, ev: pd.DataFrame, px: pd.Series) -> pd.DataFrame:
    created = ev[ev.action == "create"].groupby("mint").agg(created_ts=("ts", "min"), creator=("creator", "first"))
    migrated = ev[ev.action == "migrate"].groupby("mint").ts.min().rename("migrated_ts")
    p = pos.merge(created, on="mint", how="left").merge(migrated, on="mint", how="left")
    held = (p.buy_tok - p.sell_tok).clip(lower=0)
    p["mark_sol"] = held * p.mint.map(px).fillna(0)
    p["pnl"] = p.sell_sol + p.mark_sol - p.buy_sol
    p["ret"] = p.pnl / p.buy_sol.replace(0, np.nan)
    p["age_min_at_entry"] = (p.first_buy - p.created_ts) / 60_000
    p["min_after_migration"] = (p.first_buy - p.migrated_ts) / 60_000
    p["hold_min"] = (p.last_sell - p.first_buy) / 60_000
    p["is_creator"] = p.trader == p.creator
    p["partial_exit"] = p.n_sells >= 2
    return p[p.buy_sol > 0]


def wallet_table(p: pd.DataFrame) -> pd.DataFrame:
    g = p.assign(win=(p.pnl > 0).astype(float), on_curve=(p.entry_stage == "curve").astype(float)).groupby("trader")
    w = g.agg(tokens=("mint", "nunique"), pnl=("pnl", "sum"), vol=("buy_sol", "sum"),
              win_rate=("win", "mean"), med_size=("buy_sol", "median"),
              med_hold_min=("hold_min", "median"), med_entry_mcap=("entry_mcap", "median"),
              curve_share=("on_curve", "mean"),
              partial_share=("partial_exit", "mean"), creator_share=("is_creator", "mean"))
    w["roi"] = w.pnl / w.vol
    return w


def trencher_like(w: pd.DataFrame) -> pd.Series:
    """Profitable humans, not bots/insiders: enough tokens, sane win rate, not instant flips, not creators."""
    return ((w.tokens >= 15) & (w.tokens <= 400) & (w.pnl > 1) & (w.roi > 0.05) & w.win_rate.between(0.2, 0.8)
            & (w.med_hold_min >= 1) & (w.creator_share < 0.2))


def profile(p: pd.DataFrame, who: pd.Index) -> dict:
    s = p[p.trader.isin(who)]
    q = lambda c, x: float(s[c].quantile(x)) if s[c].notna().any() else float("nan")
    return {
        "positions": len(s), "wallets": s.trader.nunique(),
        "win_rate": float((s.pnl > 0).mean()),
        "median_ret": float(s.ret.median()), "mean_ret": float(s.ret.clip(upper=50).mean()),
        "share_2x_plus": float((s.ret >= 1).mean()), "share_minus50": float((s.ret <= -0.5).mean()),
        "entry_on_curve": float((s.entry_stage == "curve").mean()),
        "entry_mcap_sol_p25_p50_p75": (q("entry_mcap", .25), q("entry_mcap", .5), q("entry_mcap", .75)),
        "token_age_min_at_entry_p25_p50_p75": (q("age_min_at_entry", .25), q("age_min_at_entry", .5), q("age_min_at_entry", .75)),
        "min_after_migration_p25_p50_p75": (q("min_after_migration", .25), q("min_after_migration", .5), q("min_after_migration", .75)),
        "hold_min_p25_p50_p75": (q("hold_min", .25), q("hold_min", .5), q("hold_min", .75)),
        "size_sol_p50": q("buy_sol", .5), "partial_exit_share": float(s.partial_exit.mean()),
        "pnl_from_top10pct_trades": float(s.pnl.nlargest(max(1, len(s) // 10)).sum() / s.pnl[s.pnl > 0].sum()) if (s.pnl > 0).any() else float("nan"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kol", help="kolscan leaderboard JSON (wallet_address, name, twitter)")
    ap.add_argument("--out", default="data/wallet_study")
    a = ap.parse_args()
    files = sorted(DATA.glob("*/*.parquet"))
    pos, ev, px = load_positions(files)
    p = enrich(pos, ev, px)
    w = wallet_table(p)
    pros = w[trencher_like(w)].sort_values("pnl", ascending=False)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pros.to_csv(out / "trencher_wallets.csv")
    p[p.trader.isin(pros.index)].to_parquet(out / "trencher_positions.parquet")

    print(f"\n{len(files)} hours | {p.trader.nunique():,} wallets | {p.mint.nunique():,} tokens | {len(p):,} positions")
    print(f"profitable wallets (>=15 tokens): {(w[(w.tokens >= 15)].pnl > 0).mean():.0%}")
    print(f"trencher-like profitable wallets: {len(pros)}")
    print("\nTop 15 trencher-like wallets:")
    print(pros.head(15)[["tokens", "pnl", "roi", "win_rate", "med_size", "med_hold_min", "med_entry_mcap", "curve_share", "partial_share"]]
          .round(3).to_string())
    everyone = w[(w.tokens >= 15) & (w.creator_share < 0.2)].index
    print("\nProfile — trencher-like pros:")
    for k, v in profile(p, pros.index).items():
        print(f"  {k}: {v}")
    print("\nProfile — all active wallets (>=15 tokens):")
    for k, v in profile(p, everyone).items():
        print(f"  {k}: {v}")

    if a.kol:
        kol = pd.DataFrame(json.load(open(a.kol)))
        kol = kol.drop_duplicates("wallet_address").set_index("wallet_address")
        seen = w.index.intersection(kol.index)
        print(f"\nKOLs from leaderboard seen trading in window: {len(seen)} / {len(kol)}")
        if len(seen):
            k = w.loc[seen].join(kol[["name", "twitter"]]).sort_values("pnl", ascending=False)
            k.to_csv(out / "kol_wallets.csv")
            print(k.head(15)[["name", "tokens", "pnl", "roi", "win_rate", "med_hold_min", "med_entry_mcap", "curve_share"]].round(3).to_string())
            print("\nProfile — KOLs:")
            for kk, v in profile(p, seen).items():
                print(f"  {kk}: {v}")


if __name__ == "__main__":
    main()
