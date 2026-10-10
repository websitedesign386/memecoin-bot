"""Volume breakout: last-5-minute volume far above the hourly average while price holds up.

Catches the start of a move earlier than momentum, at the cost of more false signals.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data import Pair
from . import Signal


@dataclass
class VolumeBreakoutStrategy:
    name: str = "volume_breakout"
    min_volume_spike: float = 3.0   # m5 volume vs average 5m slice of the last hour
    min_change_m5: float = 2.0
    max_change_h1: float = 80.0
    min_buys_m5: int = 20

    def evaluate(self, pair: Pair) -> Signal | None:
        avg_5m = pair.volume_h1 / 12 if pair.volume_h1 else 0
        if avg_5m <= 0:
            return None
        spike = pair.volume_m5 / avg_5m
        if spike < self.min_volume_spike:
            return None
        if pair.change_m5 < self.min_change_m5 or pair.change_h1 > self.max_change_h1:
            return None
        if pair.buys_m5 < self.min_buys_m5 or pair.buys_m5 <= pair.sells_m5:
            return None
        return Signal(
            pair.token_address,
            pair.symbol,
            f"5m volume {spike:.1f}x avg, m5 +{pair.change_m5:.1f}%",
            min(1.0, spike / 10),
        )
