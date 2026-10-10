"""Entry strategies. Each one only decides *whether to buy*; exits are handled by RiskManager."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..data import Pair


@dataclass
class Signal:
    token_address: str
    symbol: str
    reason: str
    strength: float  # 0..1, used to rank candidates when slots are limited


class Strategy(Protocol):
    name: str

    def evaluate(self, pair: Pair) -> Signal | None: ...


from .momentum import MomentumStrategy  # noqa: E402
from .volume_breakout import VolumeBreakoutStrategy  # noqa: E402

STRATEGIES: dict[str, type] = {
    MomentumStrategy.name: MomentumStrategy,
    VolumeBreakoutStrategy.name: VolumeBreakoutStrategy,
}


def get_strategy(name: str) -> Strategy:
    try:
        return STRATEGIES[name]()
    except KeyError:
        raise SystemExit(f"Unknown strategy '{name}'. Options: {', '.join(STRATEGIES)}")
