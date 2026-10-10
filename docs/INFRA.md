# Sniper bots & Solana speed infrastructure (Jito, Helius, Triton, SWQoS)

Researched Oct 2026. Prices come from vendor pages and reviews, so confirm them before paying.

## 1. How a sniper bot actually works

Reference implementation: [chainstacklabs/pumpfun-bonkfun-bot](https://github.com/chainstacklabs/pumpfun-bonkfun-bot),
the most credible open-source one (we read the code). Every sniper is the same 5-stage pipeline:

| Stage | What happens | Fast version |
|---|---|---|
| **1. Detect** | Notice a new token being created | Chainstack's bot offers 4 "listeners", slowest → fastest: `logs` (WebSocket `logsSubscribe`), `blocks`, `geyser` (Yellowstone gRPC), `shreds` (pre-execution stream). |
| **2. Decide** | Filters: name match, creator wallet, token age | Kept minimal; every check costs time. |
| **3. Build** | Create the buy transaction | "Extreme fast mode": **zero RPC calls between detection and buy**. The bonding-curve address is derived from the mint, a fixed token amount is bought, the priority fee is fixed (a dynamic fee needs an extra RPC call), and slippage is set to 30%. |
| **4. Send / land** | Get the tx into the current block | Plain RPC, or Jito bundle + tip, or staked "sender" endpoints, often several at once. |
| **5. Exit** | Sell | Often **time-based: sell after a few seconds**, because most launches die within minutes. |

Notable details from the code:
- The **shreds** listener sees a transaction *before it executes*. That's the earliest possible signal, but it
  can't tell if the create will fail, and it can't see tokens created through a router contract.
- Its default buy is **0.0001 SOL**. Even this reputable project treats sniping as an experiment.
- It sends via standard `send_transaction`, without Jito. Competitive sniping needs more.

Retail sniper products ([Trojan, BonkBot, Axiom, Photon, GMGN](https://solanatools.io/blog/solana-trading-bot-fees-compared))
wrap this same pipeline in a Telegram or web UI and charge **~0.75–1% per trade**, plus priority fees and Jito tips.
The Telegram ones are custodial: they hold your key ([MemeGateway](https://memegateway.com/academy/solana-trading-bot-fees-compared-2026/)).

## 2. Jito: the block-building and MEV layer

- **What it is:** a modified validator client plus an off-chain **Block Engine** that runs an auction for
  transaction placement. ~94–95% of stake runs a Jito-compatible client ([Chainstack](https://chainstack.com/jito-explained-bundles-tips-mev-solana/), [RPC Fast](https://rpcfast.com/blog/jito-explained-bundles-tips-mev-solana)).
- **Bundles:** up to **5 transactions executed in order, all-or-nothing**. If one fails, none land, and you pay nothing.
- **Tips:** a SOL transfer (min 1,000 lamports) to one of 8 Jito tip accounts, separate from the priority fee. Put the
  tip in the last tx so you only pay if the bundle lands ([AllenHark](https://allenhark.com/blog/how-to-send-jito-bundles-solana)).
- **Not guaranteed:** if the current leader isn't running Jito or you're outbid, the bundle silently expires (~60s).
- **ShredStream:** Jito's feed of raw block fragments ("shreds"), streamed before the block is complete. It gives a
  lead of up to ~50–200 ms (vendor claim).
- **BAM (2026):** a new block builder running inside secure hardware (TEEs) with "preconfirmations". ~34% of stake at
  launch ([Solana Compass](https://solanacompass.com/news/jito-bam-preconfirmations-go-live-on-solana-covering-34-of-network-stake)).

**Useful for us:** yes, for **sells and buys sent as bundles**. A swap in a bundle can't have another trader's tx
inserted around it inside the bundle, which gives MEV/sandwich protection
([DEV](https://dev.to/gerus_team/mev-protection-on-solana-in-2026-jito-bundles-astralane-and-what-actually-works-3gbc)).
Failed attempts cost nothing. The other common use of Jito, sandwiching other users, is out of scope.

## 3. SWQoS (stake-weighted quality of service): why "staked connections" matter

- Each Solana block leader can only accept so many incoming connections. Since the 2022 spam outages, validators give
  **bandwidth in proportion to stake**: a validator with 0.5% of stake can send ~0.5% of the packets to the leader.
- RPC nodes have no stake, so a provider only gets this priority lane by **peering with a staked validator**. That's
  what a "staked connection" is.
- **Priority fees** decide *order inside the block*. **SWQoS** decides whether your packet *gets in the door at all*.
  During a hyped launch, unstaked traffic is dropped first. Neither guarantees inclusion.
  ([RPC Fast](https://rpcfast.com/blog/solana-swqos-staked-connections), [Everstake](https://everstake.one/resources/blog/swqos-vs-priority-fees-which-is-better-on-solana),
  [Triton](https://blog.triton.one/evolution-of-solanas-stake-weighted-quality-of-service-from-the-agave-side/))

## 4. Helius: RPC + landing + streaming

| Product | What it does | Plan / price |
|---|---|---|
| **RPC + staked connections** | Normal API access; transactions are forwarded through staked validators (SWQoS). | Developer **$49/mo** (10M credits, 50 req/s). Business $499, Professional $999. |
| **Sender** | Sends each transaction **to staked validator connections and Jito at the same time**. No credit cost, ~50 tx/s. | Included. |
| **LaserStream** | gRPC/WebSocket streaming of accounts and transactions (Yellowstone-compatible), with replay after disconnects. | Mainnet from Business ($499/mo); data add-ons from $400/mo. |
| **Raw shreds** | Earliest data feed | $1,000/mo per IP. |
| **Dedicated node** | Your own node | from $2,900/mo. |

Sources: [Helius pricing](https://www.helius.dev/pricing), [Helius plans](https://www.helius.dev/docs/billing/plans),
[Chainstack overview](https://chainstack.com/helius-rpc-provider-a-practical-overview/), [DappAtlas](https://dappatlas.com/projects/helius/).

## 5. Triton One: creators of Yellowstone gRPC

- **Yellowstone "Dragon's Mouth" gRPC** is Triton's open-source Geyser plugin. It runs **inside the validator** and
  streams account, transaction, slot and block updates the instant the node processes them, filtered server-side
  so you only receive what you subscribed to. It's the industry standard: Helius LaserStream, QuickNode, Chainstack
  and others offer compatible endpoints ([GitHub](https://github.com/rpcpool/yellowstone-grpc), [Triton guide](https://blog.triton.one/complete-guide-to-solana-streaming-and-yellowstone-grpc/)).
- Compared with WebSockets: lower latency (vendors cite ~5–15 ms median), filtering, and no missed messages under load.
- **Pricing:** pay-as-you-go. **$125 minimum prepaid deposit**, streaming billed **$0.08/GB** (RPC calls $10 per million).
  Alternatives: QuickNode gRPC in its $499 plan, Alchemy $75/TB ([RPC Fast](https://rpcfast.com/blog/triton-one-alternatives-solana), [DappAtlas](https://dappatlas.com/projects/triton/)).
- Triton also open-sources **Jet** (a transaction sender).

## 6. Can this help our bot?

**The sniper strategy: no.** See PLAN.md. Landing first in block 0 makes you the counterparty to dev bundles and
other snipers on tokens that ~98–99% die.

**The sniper infrastructure: yes**, aimed at our real weakness, slow exits:

| Problem in our bot | Fix with this infra | Rough cost |
|---|---|---|
| Stop-loss checked every 30s on lagging DexScreener prices | Subscribe to each open pool's account over **gRPC** and react to every reserve change in ms | Triton PAYG: a filtered stream on ~20 pools is roughly a few GB/day ≈ **$5–15/mo** after the $125 deposit (estimate). Or the free PumpAPI stream for pump.fun pools. |
| Transactions dropped when the network is busy (exactly when a token is dumping) | **Staked sending** (Helius Sender: staked validators + Jito in parallel) | Helius Developer **$49/mo** |
| Getting sandwiched on thin pools; paying for failed sells | **Jito bundle** for swaps, tip only on success | tip per trade (~0.0001–0.001 SOL) |
| Jupiter API round-trip before each sell | **Pre-build sell transactions** for open positions, refresh the blockhash in the background | free (engineering) |

Recommended starting stack: **Helius Developer ($49/mo)** for RPC + Sender, plus **Triton PAYG or the PumpAPI
stream** for real-time prices. Skip ShredStream, raw shreds and dedicated nodes ($1,000–3,000/mo): they only pay
off for block-0 sniping.
