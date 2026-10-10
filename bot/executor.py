"""Order execution: a paper simulator (default) and a live Jupiter executor.

Live mode signs transactions with YOUR key. Use a fresh burner wallet funded with only what you can lose.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from .config import Config
from .data import SOL_MINT, Pair
from .risk import Position

LAMPORTS_PER_SOL = 1_000_000_000


@dataclass
class Fill:
    sol_amount: float   # SOL spent (buy) or received (sell)
    tokens: float       # UI token amount bought or sold
    raw_amount: int     # base units bought or sold (live only)
    price_sol: float    # effective price per token
    tx: str = ""


class Executor(Protocol):
    def balance_sol(self) -> float: ...
    def buy(self, pair: Pair, size_sol: float) -> Fill: ...
    def sell(self, pos: Position, fraction: float, pair: Pair) -> Fill: ...


class PaperExecutor:
    """Fills at the DexScreener price minus a flat cost per side (fees + slippage + priority fee).

    Paper results are an UPPER BOUND on live results: real fills are worse on thin pools,
    and paper mode can't simulate failed transactions or honeypots that block sells.
    """

    def __init__(self, cfg: Config):
        self.cost = cfg.paper_cost_pct / 100
        self._balance = cfg.paper_starting_sol

    def balance_sol(self) -> float:
        return self._balance

    def buy(self, pair: Pair, size_sol: float) -> Fill:
        tokens = size_sol * (1 - self.cost) / pair.price_sol
        self._balance -= size_sol
        return Fill(size_sol, tokens, 0, size_sol / tokens, tx="paper")

    def sell(self, pos: Position, fraction: float, pair: Pair) -> Fill:
        tokens = pos.tokens * fraction
        sol = tokens * pair.price_sol * (1 - self.cost)
        self._balance += sol
        return Fill(sol, tokens, 0, pair.price_sol, tx="paper")


class JupiterExecutor:
    """Swaps through the Jupiter aggregator (routes across Raydium, Meteora, PumpSwap, Orca, ...).

    API reference: https://dev.jup.ag/docs/swap-api  — verify endpoints before going live; Jupiter
    has moved paths before (v6 -> /swap/v1, lite-api vs api.jup.ag with key). Base URL is configurable.
    """

    def __init__(self, cfg: Config):
        from solders.keypair import Keypair  # imported lazily so paper mode doesn't need solders

        if not cfg.private_key_b58:
            raise SystemExit("LIVE_TRADING=true but SOLANA_PRIVATE_KEY is empty")
        self.cfg = cfg
        self.kp = Keypair.from_base58_string(cfg.private_key_b58)
        self.jup = httpx.Client(base_url=cfg.jupiter_base_url, timeout=20)
        self.rpc = httpx.Client(timeout=30)

    # --- RPC helpers -------------------------------------------------------
    def _rpc(self, method: str, params: list) -> dict:
        r = self.rpc.post(self.cfg.rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"RPC {method} error: {body['error']}")
        return body["result"]

    def balance_sol(self) -> float:
        return self._rpc("getBalance", [str(self.kp.pubkey())])["value"] / LAMPORTS_PER_SOL

    def _confirm(self, sig: str, timeout_s: float = 60) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = self._rpc("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}])["value"][0]
            if st and st.get("err"):
                raise RuntimeError(f"transaction {sig} failed: {st['err']}")
            if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
                return
            time.sleep(1.5)
        raise TimeoutError(f"transaction {sig} not confirmed in {timeout_s}s (it may still land — check explorer)")

    # --- swaps -------------------------------------------------------------
    def _swap(self, input_mint: str, output_mint: str, amount: int) -> tuple[dict, str]:
        from solders.transaction import VersionedTransaction

        q = self.jup.get(
            "/swap/v1/quote",
            params={"inputMint": input_mint, "outputMint": output_mint, "amount": amount,
                    "slippageBps": self.cfg.slippage_bps},
        )
        q.raise_for_status()
        quote = q.json()

        s = self.jup.post("/swap/v1/swap", json={
            "quoteResponse": quote,
            "userPublicKey": str(self.kp.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        })
        s.raise_for_status()
        raw_tx = VersionedTransaction.from_bytes(base64.b64decode(s.json()["swapTransaction"]))
        signed = VersionedTransaction(raw_tx.message, [self.kp])
        sig = self._rpc("sendTransaction", [
            base64.b64encode(bytes(signed)).decode(),
            {"encoding": "base64", "skipPreflight": False, "maxRetries": 3},
        ])
        self._confirm(sig)
        return quote, sig

    def buy(self, pair: Pair, size_sol: float) -> Fill:
        lamports = int(size_sol * LAMPORTS_PER_SOL)
        quote, sig = self._swap(SOL_MINT, pair.token_address, lamports)
        raw_out = int(quote["outAmount"])  # quoted, not exact; actual may differ within slippage
        tokens = raw_out / (10 ** self._decimals(pair.token_address))
        return Fill(size_sol, tokens, raw_out, size_sol / tokens if tokens else 0, tx=sig)

    def sell(self, pos: Position, fraction: float, pair: Pair) -> Fill:
        raw = pos.raw_amount if fraction >= 0.999 else int(pos.raw_amount * fraction)
        quote, sig = self._swap(pair.token_address, SOL_MINT, raw)
        sol = int(quote["outAmount"]) / LAMPORTS_PER_SOL
        tokens = pos.tokens * (raw / pos.raw_amount) if pos.raw_amount else 0
        return Fill(sol, tokens, raw, pair.price_sol, tx=sig)

    def _decimals(self, mint: str) -> int:
        info = self._rpc("getTokenSupply", [mint])
        return int(info["value"]["decimals"])


def make_executor(cfg: Config) -> Executor:
    return JupiterExecutor(cfg) if cfg.live_trading else PaperExecutor(cfg)
