"""Market data from the public DexScreener API (no key required).

Docs: https://docs.dexscreener.com/api/reference
Rate limits at time of writing: ~60 req/min for profile/boost endpoints, ~300 req/min for pair endpoints.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

DEXSCREENER = "https://api.dexscreener.com"
SOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass
class Pair:
    """A token/SOL pool snapshot. Prices are in SOL (priceNative) and USD."""

    token_address: str
    symbol: str
    pair_address: str
    dex: str
    price_sol: float
    price_usd: float
    liquidity_usd: float
    fdv: float
    volume_m5: float
    volume_h1: float
    change_m5: float
    change_h1: float
    change_h6: float
    buys_m5: int
    sells_m5: int
    buys_h1: int
    sells_h1: int
    created_at_ms: int

    @property
    def age_minutes(self) -> float:
        if not self.created_at_ms:
            return 0.0
        return (time.time() * 1000 - self.created_at_ms) / 60_000

    @classmethod
    def from_api(cls, p: dict[str, Any]) -> "Pair":
        def num(d: dict | None, key: str) -> float:
            try:
                return float((d or {}).get(key) or 0)
            except (TypeError, ValueError):
                return 0.0

        txns = p.get("txns") or {}
        return cls(
            token_address=p["baseToken"]["address"],
            symbol=p["baseToken"].get("symbol", "?"),
            pair_address=p.get("pairAddress", ""),
            dex=p.get("dexId", ""),
            price_sol=num(p, "priceNative"),
            price_usd=num(p, "priceUsd"),
            liquidity_usd=num(p.get("liquidity"), "usd"),
            fdv=num(p, "fdv"),
            volume_m5=num(p.get("volume"), "m5"),
            volume_h1=num(p.get("volume"), "h1"),
            change_m5=num(p.get("priceChange"), "m5"),
            change_h1=num(p.get("priceChange"), "h1"),
            change_h6=num(p.get("priceChange"), "h6"),
            buys_m5=int(num(txns.get("m5"), "buys")),
            sells_m5=int(num(txns.get("m5"), "sells")),
            buys_h1=int(num(txns.get("h1"), "buys")),
            sells_h1=int(num(txns.get("h1"), "sells")),
            created_at_ms=int(p.get("pairCreatedAt") or 0),
        )


def best_sol_pairs(raw_pairs: list[dict[str, Any]]) -> dict[str, Pair]:
    """Keep the deepest SOL-quoted pool per token (that's where we'd trade)."""
    best: dict[str, Pair] = {}
    for raw in raw_pairs:
        if raw.get("chainId") != "solana":
            continue
        if (raw.get("quoteToken") or {}).get("address") != SOL_MINT:
            continue
        try:
            pair = Pair.from_api(raw)
        except (KeyError, TypeError):
            continue
        cur = best.get(pair.token_address)
        if cur is None or pair.liquidity_usd > cur.liquidity_usd:
            best[pair.token_address] = pair
    return best


class DexScreener:
    def __init__(self, client: httpx.Client | None = None):
        self.http = client or httpx.Client(base_url=DEXSCREENER, timeout=15)

    def trending_token_addresses(self, limit: int = 60) -> list[str]:
        """Solana tokens that recently got a profile or paid boost — DexScreener's 'what's hot' feeds."""
        seen: list[str] = []
        for path in ("/token-boosts/latest/v1", "/token-boosts/top/v1", "/token-profiles/latest/v1"):
            try:
                r = self.http.get(path)
                r.raise_for_status()
                items = r.json()
            except (httpx.HTTPError, ValueError):
                continue
            for it in items if isinstance(items, list) else []:
                addr = it.get("tokenAddress")
                if it.get("chainId") == "solana" and addr and addr not in seen:
                    seen.append(addr)
        return seen[:limit]

    def pairs_for(self, token_addresses: list[str]) -> dict[str, Pair]:
        """Fetch pools for up to N tokens (batched 30 per call, the API maximum)."""
        raw: list[dict[str, Any]] = []
        for i in range(0, len(token_addresses), 30):
            chunk = ",".join(token_addresses[i : i + 30])
            try:
                r = self.http.get(f"/tokens/v1/solana/{chunk}")
                r.raise_for_status()
                data = r.json()
                raw.extend(data if isinstance(data, list) else data.get("pairs") or [])
            except (httpx.HTTPError, ValueError):
                continue
        return best_sol_pairs(raw)
