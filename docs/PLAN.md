# Strategy analysis & build plan (Oct 2026)

All win rates and returns in this doc are **estimates and assumptions** made before any backtest. Their job
is to show which strategies can work at all. The real numbers come from step 1 of the plan (backtest on the
free PumpAPI archive).

## 1. The market we'd be trading

- **~1–2% of pump.fun launches ever "graduate"** to a real pool, and some 2026 months were well under 1%
  ([Solana Compass](https://solanacompass.com/news/pumpfun-launched-42000-tokens-in-one-day-fewer-than-2-will-ever-reach-a-dex),
  [PumpFunData](https://pumpfundata.com/blog/pumpfun-graduation-rate-analysis),
  [DEXTools](https://www.dextools.io/news/pump-fun-graduation-collapse-solana-fees-2026),
  [arXiv survival study](https://arxiv.org/html/2607.02823v3)).
- **Most traders lose money in most months.** 30–50% of pump.fun wallets were profitable in most months from 2024–25,
  with a spike to 73% in April 2026 ([CoinGecko](https://www.coingecko.com/research/publications/pump-fun-traders-are-making-a-comeback),
  [crypto.news](https://crypto.news/over-60-pump-fun-traders-saw-losses-less-than-0-01-made-over-1m/)). Profits are
  extremely concentrated: well under 0.01% of wallets made $1M+.
- "Smart money" wallets with 100% win rates are usually insiders, bundlers, or MEV bots, not skilled traders
  ([MadeOnSol](https://madeonsol.com/blog/why-100-percent-winrate-solana-wallet-red-flag)).

## 2. How sniper bots are so fast

A pro sniper's full path from "token created" to "I own it" is **~20–200 ms**. Every step is engineered:

| Step | What pros do | What our bot does today |
|---|---|---|
| **See the event** | **Jito ShredStream** (block fragments before the block is even finished) and **Yellowstone gRPC** (Geyser plugin that streams account/tx updates with server-side filters). They see a pump.fun `create` tx in the same slot. | Polls DexScreener every 30s, and DexScreener itself is several seconds to minutes behind the chain. |
| **Build the tx** | Nothing is fetched. The pump.fun bonding-curve address is derived from the mint, so the buy instruction is computed locally in <1 ms from a pre-built template. Rust/Go, pre-signed, blockhash refreshed in the background. | Jupiter quote → swap API → sign (~0.5–1.5 s). |
| **Land the tx** | Sent in parallel to several landing services: **Jito bundles** with a tip, **stake-weighted QoS** lanes (Helius/Triton staked connections), 0slot, Nozomi, bloXroute, NextBlock. High priority fees. Unstaked traffic gets dropped first during launches. | One public/paid RPC `sendTransaction`. |
| **Location** | Servers co-located near the validators (Frankfurt/Amsterdam/NY/Tokyo). | Wherever it runs. |
| **Cost** | Dedicated gRPC/ShredStream from ~$200–2,000+/month, tips/priority fees on every attempt, constant engineering. | ~$0–50/month. |

Also, many "snipers" that win block 0 are the **dev's own bundled wallets** buying in the same transaction bundle
as the create. They don't compete with you. They are who you'd be buying from.

Sources: [Dysnix stack guide](https://dysnix.com/blog/complete-stack-competitive-solana-sniper-bots),
[RPC Fast](https://rpcfast.com/blog/complete-stack-competitive-solana-sniper-bots),
[OrbitFlare](https://orbitflare.com/blog/developers/solana-sniper-bot-guide),
[D3AD-E/Solana-sniper-bot](https://github.com/D3AD-E/Solana-sniper-bot). Latency numbers are vendor claims.

## 3. Can we buy and sell in time?

**Entries: yes, if we don't trade launches.** A token that's been moving for 30 min to hours doesn't need
millisecond entry. Paying a few % extra for a late entry is a cost we can model.

**Exits: not with the current design. This is the biggest weakness.** The bot checks the stop-loss every 30s using
lagging DexScreener prices, so a −25% stop routinely fills at −40% or worse. On a rug (LP pulled or dev dump), price
goes −80…−99% within one or two blocks, so no stop can save you. Only filters can.

The fix is to watch each open pool's reserves over a **websocket/gRPC subscription**, which gives sub-second
updates, and fire pre-computed sells. Fast exits matter more than fast entries for us.

## 4. Expected value per trade (assumptions, not measured)

Model: `EV = win% × avg win − loss% × avg loss − round-trip cost`. Average loss blends stopped losses with
rugs/gaps that go through the stop.

| Scenario | Win % | Avg win | Avg loss | Cost RT | **EV / trade** | Break-even win % |
|---|---|---|---|---|---|---|
| A. Current bot (30s polling exits) | 33 | +45% | −42% | 6% | **−19.6%** | 55% |
| B. Graduated momentum + real-time exits | 36 | +45% | −28% | 4% | **−5.9%** | 44% |
| C. B + strong filters (holders, dev, bundles), fewer trades | 42 | +50% | −26% | 4% | **+2.0%** | 39% |
| D. Launch sniping on retail infra | 20 | +60% | −59% | 8% | **−43%** | 56% |
| E. Launch sniping on pro infra | 30 | +90% | −53% | 5% | **−15%** | 41% |

Takeaways:
- The **win rate** doesn't decide profitability by itself. Win rate × win size vs. loss rate × loss size after costs
  does. A 35–45% win rate is normal for memecoin momentum, and profitable only if winners are clearly bigger than losers.
- Only scenario C is positive, and only barely. The edge comes from **avoiding bad trades** (filters + fast exits),
  not from being clever on entries.
- Even a positive EV can have long losing streaks. At a 42% win rate, 8–10 losses in a row will happen.

Round-trip costs, measured by fly-trader on 157k real PumpSwap trades: pool fee **1.25% → 0.30%** per side
depending on market cap, Jupiter 0.10%, and price impact `size / (size + pool SOL reserve)`, plus priority fees and
latency slippage. For a 0.1 SOL trade on a young graduated pool that's ~**3–6% round trip**.

## 5. The strategy to build: "Graduated-token momentum with fast exits"

1. **Universe:** pump.fun / LetsBonk tokens that **graduated** to PumpSwap/Raydium/Meteora, aged ~30 min to 24 h.
   This skips the ~98% that die on the bonding curve and avoids competing with block-0 snipers.
2. **Entry signal:** momentum / volume breakout (already built), computed from **our own 1-minute candles**
   from the PumpAPI live stream, not DexScreener.
3. **Hard filters** (where the edge actually is):
   - mint and freeze authority revoked, LP burned/locked
   - top-10 holders < ~30% (excluding pool); dev wallet holdings and dev's past launches (serial ruggers)
   - no **same-block bundle** at launch (insiders holding a hidden share of supply)
   - organic flow: unique buyers, not a few wallets wash-trading
   - Grok X-sentiment as a veto (coordinated shilling, rug reports)
4. **Execution:** Jupiter with a **signer guard** (simulate first and refuse if it overspends or returns far
   less than quoted, borrowed from fly-trader). Priority fees, then a staked RPC lane.
5. **Exits:** real-time pool subscription, stop-loss, partial take-profit, trailing stop, time stop. A daily loss
   cap and a kill switch (from robinhood-chain-traders).
6. **Sizing:** tiny (0.05–0.1 SOL) and capped by pool depth so price impact stays < 1%.

**Not recommended:** launch sniping (negative even with pro infra unless you're an insider), copy trading
(you're the exit liquidity for the wallet you copy), arbitrage/MEV (professional market), anything manipulative.

## 6. What we take from the reviewed repos

| From | Take | Skip |
|---|---|---|
| **fly-trader** | PumpAPI archive + live stream ingestion, measured cost model, walk-forward evaluation, signer guard | the connectome brain, GPU/64 GB requirements, $FLY token/vault |
| **omo** | single rule set shared by the audit log and the live gate; decision journal | fomo.family scraping (fragile) |
| **robinhood-chain-traders** | kill switch (file / signal / API), per-strategy documented failure modes | the EVM chain code |
| **trader-tony-v4** | the risk-check list (mint/freeze/LP/holders) as a cross-check | copy-trade-for-a-fee product parts |
| **Free-SolanaSniperBot** | nothing (ad funnel, no code) | everything |

## 7. Roadmap

1. **Backtest (prove it or kill it):** download 2–4 weeks of `replay.pumpapi.io` hours, build 1-minute candles,
   replay the strategy with the real cost model. Kill the strategy if out-of-sample EV ≤ 0.
2. **Paper trade on live data** (PumpAPI stream + real-time exits) for 2+ weeks. Compare to backtest.
3. **Micro-live:** 0.02–0.05 SOL trades, burner wallet, signer guard, daily cap. Compare live fills to paper.
4. Scale size only if live ≈ paper over 100+ trades.
