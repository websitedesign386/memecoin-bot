"""Momentum: buy tokens already trending up with real buy pressure, but not yet parabolic.

Latency-tolerant (works on 30s polling), which is why it's the default for a retail bot.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data import Pair
from . import Signal


@dataclass
class MomentumStrategy:
    name: str = "momentum"
    min_change_h1: float = 10.0      # % up over the last hour...
    max_change_h1: float = 150.0     # ...but not already a vertical candle (likely top)
    min_change_m5: float = 0.0       # still moving up right now
    min_buy_sell_ratio: float = 1.3  # more buyers than sellers in the last 5m
    min_volume_h1_usd: float = 50_000

    def evaluate(self, pair: Pair) -> Signal | None:
        if not (self.min_change_h1 <= pair.change_h1 <= self.max_change_h1):
            return None
        if pair.change_m5 < self.min_change_m5:
            return None
        if pair.volume_h1 < self.min_volume_h1_usd:
            return None
        ratio = pair.buys_m5 / max(pair.sells_m5, 1)
        if ratio < self.min_buy_sell_ratio:
            return None
        strength = min(1.0, (ratio / 3) * 0.5 + (pair.change_h1 / self.max_change_h1) * 0.5)
        return Signal(
            pair.token_address,
            pair.symbol,
            f"h1 +{pair.change_h1:.0f}%, m5 +{pair.change_m5:.1f}%, buy/sell {ratio:.1f}",
            strength,
        )
