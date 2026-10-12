# Backtest & wallet-study results (72 h: 2026-10-08 21:00 → 2026-10-11 21:00 UTC)

Data: PumpAPI archive, every pump.fun bonding-curve trade plus PumpSwap trades on pump-graduated pools.
Setup: $5 (0.045 SOL) per trade, fills 1.5 s after the signal at the pool's real reserves, the pool's own fee
(1.25% on the curve, tiered on PumpSwap), plus 0.0001 SOL per transaction. Max 3 open positions per strategy, one entry per token.
Exits: −25% stop · sell half at +100% ("initials") · 30% trailing stop once +40% · 4 h max hold · exit if the creator dumps.

## Strategy results

| Strategy | Half | Trades | Win % | Avg win | Avg loss | **EV / trade** | Total (SOL) | Max DD (SOL) |
|---|---|---|---|---|---|---|---|---|
| early_trench | 1st | 766 | 32% | +60% | −28% | **+0.3%** | +0.12 | −0.88 |
| early_trench | 2nd | 718 | 29% | +52% | −29% | **−5.5%** | −1.79 | −1.90 |
| final_stretch | 1st | 254 | 33% | +60% | −27% | **+1.8%** | +0.21 | −0.17 |
| final_stretch | 2nd | 198 | 34% | +39% | −26% | **−3.9%** | −0.35 | −0.39 |
| smart_convergence* | 2nd | 347 | 28% | +49% | −27% | **−5.1%** | −0.80 | −0.85 |
| migrated_momentum | 1st | 163 | 26% | +54% | −34% | **−11.1%** | −0.82 | −0.90 |
| migrated_momentum | 2nd | 145 | 26% | +55% | −39% | **−14.4%** | −0.94 | −1.07 |
| first_pullback | 1st | 96 | 27% | +46% | −35% | **−13.2%** | −0.57 | −0.62 |
| first_pullback | 2nd | 102 | 30% | +34% | −34% | **−13.3%** | −0.61 | −0.71 |

\* Smart wallets (712) were selected on the 1st half only, so only the 2nd half is a fair test.

Takeaways:
- **No strategy is reliably profitable.** The two curve strategies (early_trench, final_stretch) are near break-even in
  one half and negative in the other. Buying after migration is clearly negative (−11% to −14% per trade).
- **The −25% stop fires on ~60% of trades.** The median trade is −27%. Memecoin noise is bigger than the stop.
- Following profitable wallets (smart_convergence) didn't help: by the time 2+ pros have bought, the move is mostly done.

## Pro-wallet study (72 h)

1.07 M wallets · 111 k tokens · 5.9 M positions. 41% of active wallets (15+ tokens) were profitable.
1,411 "trencher-like" profitable wallets (20–300 tokens, ≥0.1 SOL median size, ≥1 min median hold, not creators).

| | Profitable trenchers | All active wallets |
|---|---|---|
| Win rate | 47% | 41% |
| Median trade | −2% | −4% |
| Trades ≥ 2x | 9% | 9% |
| Trades ≤ −50% | **10%** | **16%** |
| Token age at entry (median) | 3.0 min | 1.9 min |
| Market cap at entry (median) | ~107 SOL | ~130 SOL |
| Hold time (median / p75) | 3.6 / 21 min | 2.4 / 13 min |
| Size (median) | 1.1 SOL | 0.15 SOL |
| Profit from best 10% of trades | 85% | 93% |

KOLs (KolScan list): 253 of 472 traded in the window. The top ones (Schoen, decu, Megga, chester…) made ~150–375 SOL
over 72 h, trading ~50–75 tokens a day on the curve at ~70–90 SOL mcap and holding < 1 min.

Caveats: unsold tokens are marked at 50% of the last price. Wallet clusters (one person, many wallets) aren't merged.
72 hours is one market regime.

## Exit-rule sweep (tune on 1st half, check on 2nd half)

| Variant | early_trench 1st | early_trench 2nd | final_stretch 1st | final_stretch 2nd |
|---|---|---|---|---|
| base (−25% stop) | +0.3% | −5.5% | +1.8% | −3.9% |
| −40% stop | −0.9% | −8.5% | +5.0% | −5.6% |
| −60% stop | −5.8% | −8.8% | +0.2% | −7.7% |
| −40% stop, initials at +50% | −1.7% | −9.2% | +3.4% | −4.0% |
| −40% stop, 45% trail after +60% | +1.0% | −5.9% | +3.1% | −8.1% |
| −25% stop, initials at +50% | −0.1% | −5.4% | +0.8% | **−2.3%** |
| −40% stop, 30-min max hold | −2.4% | −5.9% | **+5.7%** | −4.7% |

The best first-half variant (final_stretch, −40% stop, 30-min hold: +5.7%/trade) lost −4.7%/trade on the second half.
**No variant is positive out of sample.** Tuning exits doesn't create an edge; the entry signals don't have one.
