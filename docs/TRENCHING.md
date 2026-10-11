# Making the bot trade like a trencher

## 1. What a pro trencher's process looks like

Trenchers work in Axiom's **Pulse** view (other terminals have similar views), which sorts tokens into three columns
([Axiompedia](https://axiompedia.com/guides/trading/axiom-pulse-explained)):

| Column | Token stage | Our bot's speed fit |
|---|---|---|
| **New Pairs** | Just launched on the bonding curve | ❌ ~1 s is too slow against snipers and dev bundles |
| **Final Stretch** | ~80–95% of the way to graduating | ✅ |
| **Migrated** | Graduated to PumpSwap / Raydium | ✅ |

Common checks before a buy (community guides, not verified data:
[MemeCoin Navigator](https://memecoinnavigator.com/best-axiom-trade-filters-for-new-pairs/), [AxiomPro](https://axiompro.app/memecoin-sniper/),
[Trench Review](https://trenchreview.medium.com/how-to-find-100x-memecoins-before-everyone-else-my-axiom-strategy-0a000b5a4a90)):
- dev holding ≤ ~10%; top-10 holders ≤ ~40%; snipers ≤ ~10%
- bundles: 4 or more buys in the same block = a bundle. Over ~30% bundled means inspect the wallets
- dev's history (past launches rugged or not), share of fresh wallets, holder growth
- **smart-wallet convergence:** several tracked profitable wallets buying the same token
- **X:** who posted the contract address, whether there's a real narrative or meme that fits the current trend, community quality

Typical behaviour: **many small bets**, **"take initials" at ~2x** (sell enough to recover the stake), ride the rest,
and **cut fast** when the dev sells, KOLs dump, or volume dies.

## 2. What the data says about the pros

Snapshot of the KolScan leaderboard bundled in [nirholas/kol-quest](https://github.com/nirholas/kol-quest)
(March 2026; profit units unverified, likely SOL). Wallets with 10+ trades in the window:

| Window | Wallets | **Profitable** | Median win rate (all) | Median win rate (profitable) |
|---|---|---|---|---|
| 1 day | 99 | 7 (7%) | 22% | 27% |
| 7 days | 192 | 15 (8%) | 24% | 30% |
| 30 days | 258 | 38 (15%) | 29% | 36% |

Takeaways:
- **Even well-known KOLs mostly lose money** over a given month.
- **Winners win only ~30–40% of trades.** Their edge is a few big runners plus quick cuts on losers.
- A KOL's public edge often isn't copyable: paid promotions, early allocations, and followers buying after them.
- Wallets with near-100% win rates are usually insiders or bundlers ([MadeOnSol](https://madeonsol.com/blog/why-100-percent-winrate-solana-wallet-red-flag)).
- A persistent set of ~1,000 coordinated wallet rings shows up among the first buyers of a disproportionate share of
  launches ([arXiv 2607.02795](https://arxiv.org/pdf/2607.02795)). That's who you compete with in New Pairs.

## 3. How we study them (data, not guesses)

Every trade is public on-chain, so we can reconstruct exactly what the best wallets did:

1. **Wallet list:** KolScan / GMGN smart-money lists (wallets + X handles from kol-quest's snapshot), or the CabalSpy
   API ([SDKs](https://github.com/CabalSpy/cabalspy-python)). Keep wallets profitable across 7 and 30 days with
   30–60% win rates. Drop near-100% wallets (insiders).
2. **Their full trade history:** the free PumpAPI archive (`replay.pumpapi.io`) has every pump.fun/PumpSwap trade
   with the trader's wallet since April 2026.
3. **Snapshot the token at the moment they bought:** age, market cap, bonding progress, holders, dev %, top-10 %,
   bundle %, sniper %, volume, unique buyers, which other smart wallets were already in.
4. **Compare against what they skipped:** every other token in the same column at the same minute. "What they
   bought vs. what was available" is what teaches the logic.
5. **Their exits:** hold time, % sold at 2x, partial-sell ladders, reaction to dev sells and KOL sells.
6. **X layer:** for each buy, use Grok `x_search` to find what was posted about the contract address/ticker in the
   prior hour (who, how many, which narrative), and whether the trader posted it themselves after buying (a shill pattern).
7. **Learn it:**
   - **Imitation model:** predicts "would a pro buy this token now?"
   - **Outcome model:** predicts "does it hit 2x before −50%?" (fly-trader's teacher→student idea is a reference)
   - Both are evaluated walk-forward on the archive.

## 4. Bot design ("trench mode")

```
PumpAPI live stream ─► token state per column (Final Stretch / Migrated)
Smart-wallet watcher ─► convergence signal (N tracked wallets bought within M minutes)
X watcher (Grok / X API / tweet tracker) ─► CA-mention + narrative score
                      │
                      ▼
 hard filters (dev %, top-10 %, bundles, snipers, dev history, mint/freeze) ─► score = imitation × outcome model
                      │
                      ▼
 tiny size ─► fast exits: take initials at 2x · trailing on the rest · instant exit on dev sell / LP change / smart-wallet dump
```

## 5. Limits

- New Pairs trenching needs sub-second speed and mostly faces insiders. We start with Final Stretch and Migrated.
- **Real-time X data is the hard part.** Grok `x_search` takes 30–60 s per query, which is fine for vetting but too slow
  to be first. Real-time X needs the paid X API or a third-party tweet tracker.
- Scraping KolScan, GMGN or Axiom (as kol-quest does with Playwright) may break their terms. Prefer on-chain data + official APIs.
- Copying KOLs makes us their exit liquidity. We learn their *selection logic*; we don't mirror their trades.
- The leaderboard numbers above are one scraped snapshot and unverified. The archive study replaces them with real measurements.

## 6. Trencher tools: what each gives the bot

| Tool | What trenchers use it for | Bot access | How we use it |
|---|---|---|---|
| **J7 Tracker** | Fast X/social feed next to a deploy/snipe/sell terminal | HTTP API (`POST /submit` for trades, plus a "feed" section) per [docs](https://docs.j7tracker.io/docs) / [Xanguard](https://xanguard.tech/blog/j7tracker-alternative/); unverified | **Social feed as a buzz signal**, if the API exposes it. Not for execution (~1% fee, and it's built for launching and bundling tokens). Beware the unrelated "J7" memecoin and fake sites. |
| **Cielo** | Live feed of tracked wallets' trades, wallet PnL | API: free 5k credits; Builder ~$89/mo; Architect ~$188/mo ([API info](https://api-info.cielo.finance/), [docs](https://docs.cielo.finance/)) | **Wallet PnL to pick which pros to study.** Smart-wallet convergence can be built free from the on-chain stream. |
| **Axiom** | Pulse columns, filters, charts, trading | **No public trading API** | Manual monitoring only. Automating the website would be fragile, need your Axiom login, and likely break its terms. |
| **KolScan / GMGN** | KOL leaderboards, smart-money lists | No official API (scraped by kol-quest) | Seed list of wallets to study |
| **Grok (xAI)** | — | Official API | Vet narrative and shilling (30–60 s/query) |
