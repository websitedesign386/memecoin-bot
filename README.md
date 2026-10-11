# memecoin-bot

A Solana memecoin trading bot built **paper-trading first**: it finds trending tokens, screens out rugs, and
enters on momentum signals, with optional Grok checks on X sentiment. It runs hard risk limits on every trade.

> ⚠️ **Read this first.** Most memecoins go to zero. Most memecoin traders lose money, and so do most bots.
> This bot is a tool for testing ideas cheaply. It is not a money printer. Run it in paper mode for weeks
> before risking real SOL, and then use only a burner wallet holding money you can afford to lose.

## How it trades

```
DexScreener trending feed ──► strategy signal ──► safety checks ──► Grok X-sentiment ──► risk limits ──► Jupiter swap
   (every POLL_SECONDS)       momentum /         pool liquidity,    (optional veto)      max positions,   (paper sim by
                              volume_breakout    age, RugCheck                           daily loss cap   default)
                                                 mint/freeze/LP
Open positions ──► stop-loss · take-profit (partial) · trailing stop · max hold time ──► sell
```

Everything is logged to `journal.jsonl` so you can measure results.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # defaults = paper mode, 5 SOL fake balance
python -m bot.main --once     # single tick
python -m bot.main            # run continuously
pytest                        # offline tests
```

Switch strategy with `--strategy volume_breakout`. All thresholds live in `.env` (see `.env.example`).

### Going live (only after paper results justify it)

1. Create a **new** wallet just for the bot and fund it with a small amount.
2. Use a paid RPC (Helius / QuickNode / Chainstack / Triton). The public RPC drops transactions.
3. Set `LIVE_TRADING=true`, `SOLANA_PRIVATE_KEY`, `SOLANA_RPC_URL`, and keep `TRADE_SIZE_SOL` tiny.
4. Check the Jupiter endpoints in `bot/executor.py` against https://dev.jup.ag first.

## Limitations

| Limitation | Impact |
|---|---|
| **Rugs & honeypots** | RugCheck + liquidity/age filters catch many, not all. A dev can still dump, and LP can still be pulled. |
| **Speed** | 30s polling can't compete with snipers. That's why it trades momentum, not launches. |
| **Fees & slippage** | DEX fees (~0.25–1%), pump.fun fees, priority fees, and slippage on thin pools add up. Round trips often cost 2–5%. |
| **Paper ≠ live** | Paper fills at the quoted price minus a flat cost. Live fills are worse, transactions fail, and some tokens can't be sold. |
| **Live sell sizing** | Live mode tracks the *quoted* token amount from the buy. Small differences from the actual fill are possible. |
| **API limits** | DexScreener, RugCheck, and public RPCs rate-limit. Data can lag by seconds to minutes. |
| **Backtest data** | Free tick-level pump.fun/PumpSwap history exists at replay.pumpapi.io (since Apr 2026). Backtester not built yet. |
| **AI sentiment** | Grok can be fooled by bot/shill activity and can hallucinate. It's used only as a veto. |
| **Taxes & law** | Every swap is a taxable event in many countries (including the US). Know your local rules. |
| **Key security** | A leaked private key empties the wallet instantly. Never commit `.env`. Never reuse your main wallet. |

## Docs

- [`docs/RESEARCH.md`](docs/RESEARCH.md): GitHub repos reviewed, which are worth learning from, and scam red flags
- [`docs/STRATEGIES.md`](docs/STRATEGIES.md): every common strategy and whether it works with this bot
- [`docs/PLAN.md`](docs/PLAN.md): win-rate/EV analysis, how snipers are fast, the chosen strategy and roadmap
- [`docs/INFRA.md`](docs/INFRA.md): how sniper bots work; Jito, Helius, Triton, SWQoS explained and what we use
- [`docs/TRENCHING.md`](docs/TRENCHING.md): how pro trenchers trade, KOL data, and how we study and copy their logic
- [`docs/GROK.md`](docs/GROK.md): what Grok adds, how it's wired in, and its limits

## Layout

```
bot/
  config.py      env-driven settings
  data.py        DexScreener client, Pair model
  safety.py      RugCheck + pool filters
  strategies/    momentum, volume_breakout (add your own)
  risk.py        positions, exits, daily kill switch
  executor.py    PaperExecutor, JupiterExecutor (live)
  grok.py        xAI Grok X-sentiment filter
  main.py        main loop + journal
tests/           offline tests
```
