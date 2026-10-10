"""Position tracking, exits, and account-level kill switches."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Config


@dataclass
class Position:
    token_address: str
    symbol: str
    entry_price_sol: float
    cost_sol: float          # SOL spent including fees
    tokens: float            # UI amount still held
    raw_amount: int = 0      # on-chain base units still held (live mode)
    opened_at: float = field(default_factory=time.time)
    peak_price_sol: float = 0.0
    take_profit_hit: bool = False
    proceeds_sol: float = 0.0  # SOL received from partial sells so far

    def __post_init__(self):
        self.peak_price_sol = self.peak_price_sol or self.entry_price_sol


@dataclass
class ExitDecision:
    fraction: float  # of the *remaining* position to sell
    reason: str


class RiskManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._day = self._today()
        self.realized_today_sol = 0.0

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _roll_day(self) -> None:
        if self._today() != self._day:
            self._day = self._today()
            self.realized_today_sol = 0.0

    def record_realized(self, pnl_sol: float) -> None:
        self._roll_day()
        self.realized_today_sol += pnl_sol

    @property
    def halted(self) -> bool:
        self._roll_day()
        return self.realized_today_sol <= -abs(self.cfg.max_daily_loss_sol)

    def can_open(self, open_positions: int, free_sol: float) -> tuple[bool, str]:
        if self.halted:
            return False, f"daily loss limit hit ({self.realized_today_sol:.3f} SOL)"
        if open_positions >= self.cfg.max_open_positions:
            return False, "max open positions"
        # keep ~0.02 SOL for network fees / rent
        if free_sol < self.cfg.trade_size_sol + 0.02:
            return False, f"insufficient balance ({free_sol:.3f} SOL)"
        return True, ""

    def exit_decision(self, pos: Position, price_sol: float, now: float | None = None) -> ExitDecision | None:
        now = now or time.time()
        if price_sol <= 0:
            return None
        pos.peak_price_sol = max(pos.peak_price_sol, price_sol)
        change_pct = (price_sol / pos.entry_price_sol - 1) * 100

        if change_pct <= -self.cfg.stop_loss_pct:
            return ExitDecision(1.0, f"stop loss {change_pct:.1f}%")

        if not pos.take_profit_hit and change_pct >= self.cfg.take_profit_pct:
            return ExitDecision(self.cfg.take_profit_sell_fraction, f"take profit +{change_pct:.0f}%")

        drawdown_from_peak = (1 - price_sol / pos.peak_price_sol) * 100
        if pos.peak_price_sol > pos.entry_price_sol and drawdown_from_peak >= self.cfg.trailing_stop_pct:
            return ExitDecision(1.0, f"trailing stop -{drawdown_from_peak:.0f}% from peak")

        if (now - pos.opened_at) / 60 >= self.cfg.max_hold_minutes:
            return ExitDecision(1.0, f"max hold time ({change_pct:+.1f}%)")
        return None
