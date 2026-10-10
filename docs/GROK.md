# Using Grok (and other AIs) with the bot

## What Grok can do that Claude can't (for this use case)

| | Grok (xAI) | Claude |
|---|---|---|
| **Live X/Twitter data** | ✅ Native `x_search` tool: keyword, semantic, user and thread search on X in real time. | ❌ No direct X access. Web search can find some posts, but not X's live firehose. |
| Where memecoin narratives start | X (CT, KOLs, community accounts) — this is Grok's real edge. | — |
| Writing / reviewing the bot's code, strategy design, log analysis | Capable | Strong — this is where Claude helps most. |
| Predicting prices | ❌ No | ❌ No |

Bottom line: **Grok is a data source (what is X saying right now?).** Claude helps build, review, and improve
the bot itself. Neither one can tell you which coin will go up.

## How it's wired in

`bot/grok.py` sends Grok the token's ticker + mint address and asks it to search X for the last 24h, then return
JSON: `score` (−1…1), `red_flags`, `summary`. The bot:

1. finds a signal with the normal strategy,
2. checks rug safety,
3. **only then** asks Grok — it's slow (search tool calls can take 30–60s) and costs money per call,
4. rejects the trade if `score < GROK_MIN_SCORE`. Results are cached for `GROK_CACHE_MINUTES`.

Enable it by setting `XAI_API_KEY` in `.env`. Check https://docs.x.ai for the current model name and
search-tool format (xAI moved live search from Chat Completions `search_parameters` to the Responses API
`x_search` tool); set `GROK_MODEL` accordingly.

## Limitations of AI sentiment

- **Bots and paid shills:** memecoin X activity is heavily faked. The prompt tells Grok to penalise coordinated
  posting, but it will still be fooled sometimes.
- **Hallucination:** an LLM can invent mentions or misread a ticker shared by several coins. That's why we pass
  the mint address and only use the score as a veto.
- **Latency & cost:** too slow for sniping; fine for momentum timeframes. Each call costs API credits.
- **Reflexivity:** by the time sentiment is visibly strong, early buyers are often selling into it.

## Other ways AIs can help

- **Claude (via API):** post-trade review — feed it a day of `journal.jsonl` and ask what losing trades had in
  common, then turn findings into new filter rules. Also good for a second-opinion "is this contract/repo safe?".
- **Ensemble:** require Grok (X sentiment) *and* a second model reading on-chain stats to agree before buying.
- **Don't** let any LLM place trades directly or hold your private key. Keep it advisory; the deterministic code
  enforces risk limits.
