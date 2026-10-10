"""Runtime configuration, loaded from environment variables / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional
    pass


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


@dataclass
class Config:
    # --- mode ---
    live_trading: bool = field(default_factory=lambda: _bool("LIVE_TRADING", False))
    strategy: str = field(default_factory=lambda: _env("STRATEGY", "momentum"))
    poll_seconds: int = field(default_factory=lambda: _int("POLL_SECONDS", 30))
    journal_path: str = field(default_factory=lambda: _env("JOURNAL_PATH", "journal.jsonl"))

    # --- Solana / execution (live only) ---
    rpc_url: str = field(default_factory=lambda: _env("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"))
    private_key_b58: str = field(default_factory=lambda: _env("SOLANA_PRIVATE_KEY", ""))
    jupiter_base_url: str = field(default_factory=lambda: _env("JUPITER_BASE_URL", "https://lite-api.jup.ag"))
    slippage_bps: int = field(default_factory=lambda: _int("SLIPPAGE_BPS", 300))

    # --- position sizing & risk (all in SOL) ---
    paper_starting_sol: float = field(default_factory=lambda: _float("PAPER_STARTING_SOL", 5.0))
    trade_size_sol: float = field(default_factory=lambda: _float("TRADE_SIZE_SOL", 0.1))
    max_open_positions: int = field(default_factory=lambda: _int("MAX_OPEN_POSITIONS", 3))
    max_daily_loss_sol: float = field(default_factory=lambda: _float("MAX_DAILY_LOSS_SOL", 0.5))
    stop_loss_pct: float = field(default_factory=lambda: _float("STOP_LOSS_PCT", 25))
    take_profit_pct: float = field(default_factory=lambda: _float("TAKE_PROFIT_PCT", 100))
    take_profit_sell_fraction: float = field(default_factory=lambda: _float("TAKE_PROFIT_SELL_FRACTION", 0.5))
    trailing_stop_pct: float = field(default_factory=lambda: _float("TRAILING_STOP_PCT", 30))
    max_hold_minutes: int = field(default_factory=lambda: _int("MAX_HOLD_MINUTES", 240))
    # Simulated round-trip cost per side in paper mode (DEX fee + priority fee + slippage).
    paper_cost_pct: float = field(default_factory=lambda: _float("PAPER_COST_PCT", 2.0))

    # --- token filters ---
    min_liquidity_usd: float = field(default_factory=lambda: _float("MIN_LIQUIDITY_USD", 25_000))
    min_pair_age_minutes: int = field(default_factory=lambda: _int("MIN_PAIR_AGE_MINUTES", 30))
    max_rugcheck_score: int = field(default_factory=lambda: _int("MAX_RUGCHECK_SCORE", 1000))
    require_rugcheck: bool = field(default_factory=lambda: _bool("REQUIRE_RUGCHECK", True))

    # --- Grok (optional) ---
    xai_api_key: str = field(default_factory=lambda: _env("XAI_API_KEY", ""))
    grok_model: str = field(default_factory=lambda: _env("GROK_MODEL", "grok-4"))
    grok_min_score: float = field(default_factory=lambda: _float("GROK_MIN_SCORE", 0.0))
    grok_cache_minutes: int = field(default_factory=lambda: _int("GROK_CACHE_MINUTES", 30))

    @property
    def grok_enabled(self) -> bool:
        return bool(self.xai_api_key)
