# GitHub research (October 2026)

Searched GitHub for memecoin, pump.fun, Jupiter, and Solana trading bots. Most activity is on **Solana**
(pump.fun / LetsBonk launchpads → PumpSwap, Raydium, Meteora pools), so this bot targets Solana.

## Worth studying

| Repo | What it's good for |
|---|---|
| [chainstacklabs/pumpfun-bonkfun-bot](https://github.com/chainstacklabs/pumpfun-bonkfun-bot) (~1k★, Python) | Most credible open-source pump.fun/LetsBonk sniper. From an RPC provider, no third-party APIs. Best reference if you ever add launch sniping. |
| [henrytirla/Solana-Trading-Bot](https://github.com/henrytirla/Solana-Trading-Bot) (Python) | Clear examples of buying/selling SPL tokens on Raydium & pump.fun with solana-py and Jito. |
| [0xTaoDev/jupiter-python-cli](https://github.com/0xTaoDev/jupiter-python-cli) (Python) | Jupiter swap, limit and DCA calls from Python. |
| [DracoR22/handi-cat_wallet-tracker](https://github.com/DracoR22/handi-cat_wallet-tracker) (TS) | Wallet tracking → base for a copy-trading module. |
| [fciaf420/moonbags](https://github.com/fciaf420/moonbags) (TS) | Memecoin bot using an LLM as an *exit advisor* and Jupiter Ultra — similar idea to our Grok layer. |
| [tony-42069/trader-tony-v4](https://github.com/tony-42069/trader-tony-v4) (Rust) | Autonomous bot with risk analysis; useful if you move to Rust for speed. |
| [buddies2705/awesome-memecoin-trading](https://github.com/buddies2705/awesome-memecoin-trading) | Index of terminals, rug checkers, trackers, MCPs. |

## ⚠️ Red flags — do NOT run these kinds of repos with a funded wallet

A big share of "memecoin bot" repos are scams or wallet drainers. Warning signs we saw in search results:

- **Fork count far above star count** (e.g. thousands of forks, a few hundred stars) → botted popularity.
- **Keyword-stuffed descriptions** ("solana trading bot solana trading bot …") and "FREE premium sniper 2026".
- **Pre-compiled binaries** (.exe / .NET / obfuscated JS) — you can't audit what they do with your private key.
- **Asks for your private key or seed phrase in a config file *and* contains obfuscated code or a remote URL.**
- "Contact me on Telegram for the full version."
- Volume bots, bundlers, and "token launch + auto-dump" tools — these exist to manipulate markets.

Rule: **only run code you can read**, and only with a **burner wallet** holding what you're willing to lose.

## Data & execution stack chosen

| Need | Choice | Notes |
|---|---|---|
| Market data / discovery | DexScreener public API | Free, no key. Rate limited (~60–300 req/min). |
| Rug screening | RugCheck API + own pool checks | Mint/freeze authority, LP lock, holder concentration. |
| Execution | Jupiter aggregator (`/swap/v1`) | Best routing across all Solana DEXs. Verify endpoint paths before live use. |
| RPC | Any Solana RPC (Helius, QuickNode, Chainstack, Triton) | Public RPC is rate-limited and slow; use a paid one for live. |
| Sentiment | Grok via xAI API (`x_search` tool) | Optional. See `GROK.md`. |
