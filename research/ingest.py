"""Download PumpAPI hourly archives and keep the pump.fun + PumpSwap events we need, as Parquet.

    python -m research.ingest --hours 48            # last 48 complete hours
    python -m research.ingest --start 2026-10-09T00 --end 2026-10-10T00

Source: https://replay.pumpapi.io/YYYY/MM/DD/HH.jsonl.zst — every decoded event since 2026-04-18, free, no key.
Each hour is ~400 MB compressed. We stream-decompress it and never store the raw file.
Output: data/replay/YYYY-MM-DD/HH.parquet, one row per trader leg (router txs are split via `breakdown`).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import zstandard

BASE = "https://replay.pumpapi.io"
SOL = "So11111111111111111111111111111111111111112"
POOLS = {"pump", "pump-amm"}
ACTIONS = {"buy", "sell", "create", "migrate", "createPool", "add", "remove"}
OUT = Path("data/replay")

SCHEMA = pa.schema([
    ("ts", pa.int64()), ("block", pa.int64()), ("action", pa.string()), ("pool", pa.string()),
    ("mint", pa.string()), ("pool_id", pa.string()), ("trader", pa.string()), ("tx_signer", pa.string()),
    ("sol", pa.float64()), ("tokens", pa.float64()), ("price", pa.float64()), ("mcap_sol", pa.float64()),
    ("quote_in_pool", pa.float64()), ("tokens_in_pool", pa.float64()), ("v_quote", pa.float64()), ("v_tokens", pa.float64()),
    ("fee_rate", pa.float64()), ("priority_fee", pa.float64()), ("supply", pa.float64()),
    ("symbol", pa.string()), ("name", pa.string()), ("creator", pa.string()),
    ("mint_auth", pa.bool_()), ("freeze_auth", pa.bool_()), ("burned_lp", pa.string()), ("pool_created_by", pa.string()),
    ("n_legs", pa.int32()),
])


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def rows_from_event(e: dict) -> list[dict]:
    """Normalise one archive event into 0..n rows (one per trader leg)."""
    if e.get("pool") not in POOLS or e.get("action") not in ACTIONS or e.get("quoteMint") != SOL:
        return []
    action = e["action"]
    v_quote = e.get("vQuoteInBondingCurve", e.get("virtualQuoteInPool"))
    v_tokens = e.get("vTokensInBondingCurve", e.get("virtualTokensInPool"))
    base = {
        "ts": int(e.get("timestamp") or 0), "block": int(e.get("block") or 0),
        "action": action, "pool": e["pool"], "mint": e.get("mint"), "pool_id": e.get("poolId"),
        "tx_signer": e.get("txSigner"), "price": _f(e.get("price")), "mcap_sol": _f(e.get("marketCapQuote")),
        "quote_in_pool": _f(e.get("quoteInPool")), "tokens_in_pool": _f(e.get("tokensInPool")),
        "v_quote": _f(v_quote), "v_tokens": _f(v_tokens), "fee_rate": _f(e.get("poolFeeRate")),
        "priority_fee": _f(e.get("priorityFee")), "supply": _f(e.get("supply")),
        "symbol": None, "name": None, "creator": None, "mint_auth": None, "freeze_auth": None,
        "burned_lp": e.get("burnedLiquidity"), "pool_created_by": e.get("poolCreatedBy"),
    }
    if action in ("create", "migrate", "createPool"):
        base.update(symbol=e.get("symbol"), name=(e.get("name") or "")[:80],
                    creator=e.get("creatorFeeAddress") or e.get("txSigner"),
                    mint_auth=e.get("mintAuthority") is not None, freeze_auth=e.get("freezeAuthority") is not None)
    legs = e.get("breakdown") if action in ("buy", "sell") else None
    if legs:
        legs = [l for l in legs if l.get("action") in ("buy", "sell")]
    if not legs:
        legs = [{"trader": e.get("txSigner"), "action": action,
                 "quoteAmount": e.get("quoteAmount"), "tokenAmount": e.get("tokenAmount")}]
    out = []
    for l in legs:
        r = dict(base)
        r.update(action=l.get("action", action), trader=l.get("trader"), sol=_f(l.get("quoteAmount")),
                 tokens=_f(l.get("tokenAmount")), n_legs=len(legs))
        out.append(r)
    return out


def ingest_hour(hour: datetime, client: httpx.Client | None = None, out_dir: Path = OUT) -> tuple[str, int, float]:
    if client is None:  # one client per worker process
        with httpx.Client() as c:
            return ingest_hour(hour, c, out_dir)
    path = out_dir / hour.strftime("%Y-%m-%d") / f"{hour:%H}.parquet"
    if path.exists():
        return str(path), -1, 0.0
    url = f"{BASE}/{hour:%Y/%m/%d/%H}.jsonl.zst"
    t0 = time.time()
    rows: list[dict] = []
    with client.stream("GET", url, timeout=httpx.Timeout(60, read=300)) as resp:
        if resp.status_code == 404:
            return url, 0, 0.0
        resp.raise_for_status()
        reader = zstandard.ZstdDecompressor().stream_reader(_IterIO(resp.iter_bytes(1 << 20)))
        text = io.TextIOWrapper(io.BufferedReader(reader, 1 << 22), encoding="utf-8")
        for line in text:
            # cheap prefilter before JSON parsing: only pump / pump-amm events
            if '"pool": "pump' not in line and '"pool":"pump' not in line:
                continue
            try:
                rows.extend(rows_from_event(json.loads(line)))
            except (ValueError, KeyError):
                continue
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp, compression="zstd")
    tmp.rename(path)
    return str(path), len(rows), time.time() - t0


class _IterIO(io.RawIOBase):
    """File-like wrapper over an iterator of bytes chunks (for streaming zstd)."""

    def __init__(self, it):
        self.it, self.buf = it, b""

    def readable(self):
        return True

    def readinto(self, b):
        while not self.buf:
            try:
                self.buf = next(self.it)
            except StopIteration:
                return 0
        n = min(len(b), len(self.buf))
        b[:n], self.buf = self.buf[:n], self.buf[n:]
        return n


def hours_between(start: datetime, end: datetime) -> list[datetime]:
    out, h = [], start
    while h < end:
        out.append(h)
        h += timedelta(hours=1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, help="last N complete hours")
    ap.add_argument("--start", help="UTC hour, e.g. 2026-10-09T00")
    ap.add_argument("--end", help="UTC hour (exclusive)")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if a.start:
        start = datetime.strptime(a.start, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
        end = datetime.strptime(a.end, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if a.end else now
    else:
        end, start = now - timedelta(hours=1), now - timedelta(hours=1 + (a.hours or 24))
    hours = sorted(hours_between(start, end), reverse=True)  # newest first
    # JSON parsing is CPU-bound, so use processes (each holds ~1-2 GB while parsing an hour)
    with ProcessPoolExecutor(a.parallel) as pool:
        futs = {pool.submit(ingest_hour, h): h for h in hours}
        for f in as_completed(futs):
            h = futs[f]
            try:
                path, n, secs = f.result()
                print(f"{h:%Y-%m-%dT%H} rows={n} {secs:.0f}s {path}", flush=True)
            except Exception as e:  # keep going; rerun fills gaps
                print(f"{h:%Y-%m-%dT%H} FAILED {e!r}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
