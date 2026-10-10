"""Rug / honeypot screening.

Uses the public RugCheck API (https://api.rugcheck.xyz/swagger/index.html) plus our own pool checks.
A token must pass ALL checks before any strategy is allowed to buy it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Config
from .data import Pair

# RugCheck risk names that should always block a buy, whatever the overall score.
HARD_BLOCK_KEYWORDS = (
    "mint authority",      # dev can print unlimited supply
    "freeze authority",    # dev can freeze your tokens -> you can't sell (honeypot)
    "copycat",
    "high ownership",
    "single holder",
    "lp unlocked",
    "large amount of lp unlocked",
)


@dataclass
class SafetyResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    rugcheck_score: int | None = None


def evaluate_rugcheck(report: dict[str, Any], max_score: int) -> SafetyResult:
    reasons: list[str] = []
    score = report.get("score")
    for risk in report.get("risks") or []:
        name = str(risk.get("name", "")).lower()
        level = str(risk.get("level", "")).lower()
        if level == "danger" or any(k in name for k in HARD_BLOCK_KEYWORDS):
            reasons.append(f"rugcheck: {risk.get('name')} ({level or 'flagged'})")
    if isinstance(score, (int, float)) and score > max_score:
        reasons.append(f"rugcheck score {score} > {max_score}")
    return SafetyResult(ok=not reasons, reasons=reasons, rugcheck_score=score if isinstance(score, int) else None)


def evaluate_pool(pair: Pair, cfg: Config) -> list[str]:
    reasons = []
    if pair.liquidity_usd < cfg.min_liquidity_usd:
        reasons.append(f"liquidity ${pair.liquidity_usd:,.0f} < ${cfg.min_liquidity_usd:,.0f}")
    if pair.age_minutes < cfg.min_pair_age_minutes:
        reasons.append(f"pair age {pair.age_minutes:.0f}m < {cfg.min_pair_age_minutes}m")
    if pair.price_sol <= 0:
        reasons.append("no SOL price")
    return reasons


class SafetyChecker:
    def __init__(self, cfg: Config, client: httpx.Client | None = None):
        self.cfg = cfg
        self.http = client or httpx.Client(base_url="https://api.rugcheck.xyz", timeout=15)
        self._cache: dict[str, SafetyResult] = {}

    def check(self, pair: Pair) -> SafetyResult:
        pool_reasons = evaluate_pool(pair, self.cfg)
        if pool_reasons:
            return SafetyResult(ok=False, reasons=pool_reasons)

        if pair.token_address in self._cache:
            return self._cache[pair.token_address]

        try:
            r = self.http.get(f"/v1/tokens/{pair.token_address}/report/summary")
            r.raise_for_status()
            result = evaluate_rugcheck(r.json(), self.cfg.max_rugcheck_score)
        except (httpx.HTTPError, ValueError) as e:
            if self.cfg.require_rugcheck:
                return SafetyResult(ok=False, reasons=[f"rugcheck unavailable: {e.__class__.__name__}"])
            result = SafetyResult(ok=True, reasons=["rugcheck skipped"])

        self._cache[pair.token_address] = result
        return result
