# Will every strategy work with this bot?

No. Strategies differ in how much **speed**, **capital**, and **infrastructure** they need. This bot polls
public APIs every ~30s and trades through Jupiter, so it's competitive only where a 30s–2min reaction time
is fine. Here is each common memecoin strategy, ranked by how well it fits.

| Strategy | Fits this bot? | Why |
|---|---|---|
| **Momentum / trend following** | ✅ Yes (built in: `momentum`) | Latency-tolerant; trades tokens that already have liquidity and a track record of hours, not seconds. Main risk: buying the top. Mitigated by the `max_change_h1` cap + trailing stop. |
| **Volume breakout** | ✅ Yes (built in: `volume_breakout`) | Catches a move earlier than momentum. More false positives; keep size small. |
| **Social / narrative (Grok)** | 🟡 As a filter only | X chatter leads price on memecoins, but it's heavily botted and paid-shilled. Used here to *veto* or rank, never as a sole buy trigger. |
| **Copy trading "smart" wallets** | 🟡 Possible add-on | Needs a wallet-stream (Helius/Shyft websockets or Geyser gRPC). You always buy *after* them, and well-known wallets dump on copiers. Need to vet wallets by realised PnL, not hype. |
| **Pump.fun graduation / migration buys** | 🟡 Possible add-on | Tokens that complete the bonding curve and migrate to PumpSwap/Raydium have survived the first filter. Still very competitive at the moment of migration; better to wait 30+ min and let momentum pick it up. |
| **New-launch sniping (block 0–2)** | ❌ Not with this design | Won by bots with Jito bundles, co-located RPC/Geyser, and sub-100ms pipelines (Rust/Go). Most launches are rugs; a 30s-polling bot buys what snipers are selling. If you want to try, start from `chainstacklabs/pumpfun-bonkfun-bot` and expect to lose while learning. |
| **Mean reversion / dip buying** | ❌ Dangerous | Memecoins don't revert to a mean — many go to zero. "Buying the dip" on a rug is the most common way bots blow up. |
| **Grid trading / DCA** | ❌ Poor fit | Grids assume range-bound prices; memecoins trend hard or die. DCA into a dying token compounds losses. |
| **DEX arbitrage** | ❌ Not for retail | Dominated by MEV searchers with atomic Jito bundles and custom on-chain programs. Margins are sub-1% and gone in milliseconds. |
| **Market making** | ❌ Not for retail | Needs inventory in both sides and tolerance for toxic flow. |

## Strategies this project will not implement

These are market manipulation or prey on other users. They're illegal in many jurisdictions and are what most
"free bot" repos on GitHub are actually selling:

- **Sandwich attacks / front-running** other people's swaps
- **Volume bots / wash trading** to fake activity and climb trending lists
- **Bundled launches / pump-and-dump** — launching a token, buying supply across many wallets, then dumping on buyers
- **Coordinated shilling** with bot accounts

## Adding a strategy

Create `bot/strategies/my_strategy.py` with a class exposing `name` and `evaluate(pair) -> Signal | None`,
register it in `bot/strategies/__init__.py`, then run `python -m bot.main --strategy my_strategy`.
Exits (stop-loss, take-profit, trailing, max hold) are shared and configured in `.env`.

## How to know if a strategy actually works

1. Run it in **paper mode for at least 1–2 weeks** (hundreds of trades, not ten).
2. Analyse `journal.jsonl`: win rate, average win vs. average loss, max drawdown.
3. Paper results are an *upper bound*. Assume live is worse by fees, slippage, failed transactions, and sells
   blocked by honeypots. If paper only barely makes money, live will lose.
4. Go live with the smallest size the network allows; compare to paper over the same period.
