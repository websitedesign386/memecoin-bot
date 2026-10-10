"""Optional Grok (xAI) layer: reads live X/Twitter chatter about a token and returns a sentiment score.

Grok is used as a FILTER (veto or rank boost), never as the sole reason to buy:
memecoin X activity is heavily botted and paid-shilled, and LLMs can hallucinate.

xAI docs: https://docs.x.ai  — live search is a server-side tool (`x_search`) on the Responses API.
Verify the model name and tool format in your xAI console; both are configurable.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import httpx

from .config import Config

PROMPT = """You are a skeptical crypto analyst screening a Solana memecoin.
Token: ${symbol}  (mint address: {address})

Search X for posts from the last 24 hours that mention this token's ticker or mint address.
Judge ORGANIC interest, not hype volume. Penalise heavily:
- many near-identical posts or brand-new / bot-looking accounts (coordinated shilling)
- paid "call" groups, giveaway spam, "100x guaranteed" style posts
- reports of rugs, dev selling, honeypots, or the ticker being a copycat of another coin
Reward: real community discussion, known accounts engaging independently, an actual meme/narrative.

Reply with ONLY a JSON object, no prose:
{{"score": <float -1.0 to 1.0>, "mentions_estimate": <int>, "red_flags": [<short strings>], "summary": "<one sentence>"}}
If you find little or no discussion, return score 0 and say so."""


@dataclass
class Sentiment:
    score: float
    summary: str
    red_flags: list[str] = field(default_factory=list)
    mentions_estimate: int = 0


def parse_sentiment(text: str) -> Sentiment | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return Sentiment(
            score=max(-1.0, min(1.0, float(d.get("score", 0)))),
            summary=str(d.get("summary", ""))[:300],
            red_flags=[str(x) for x in d.get("red_flags") or []][:10],
            mentions_estimate=int(d.get("mentions_estimate") or 0),
        )
    except (ValueError, TypeError):
        return None


def _output_text(body: dict) -> str:
    """Extract assistant text from a Responses-API (or Chat-Completions) style body."""
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    parts: list[str] = []
    for item in body.get("output") or []:
        for c in item.get("content") or []:
            if c.get("type") in ("output_text", "text") and c.get("text"):
                parts.append(c["text"])
    if not parts and body.get("choices"):
        parts.append(body["choices"][0]["message"]["content"])
    return "\n".join(parts)


class GrokSentiment:
    def __init__(self, cfg: Config, client: httpx.Client | None = None):
        self.cfg = cfg
        # live search runs many queries server-side; allow a long timeout
        self.http = client or httpx.Client(
            base_url="https://api.x.ai/v1",
            headers={"Authorization": f"Bearer {cfg.xai_api_key}"},
            timeout=150,
        )
        self._cache: dict[str, tuple[float, Sentiment | None]] = {}

    def score(self, symbol: str, address: str) -> Sentiment | None:
        hit = self._cache.get(address)
        if hit and time.time() - hit[0] < self.cfg.grok_cache_minutes * 60:
            return hit[1]
        result: Sentiment | None = None
        try:
            r = self.http.post("/responses", json={
                "model": self.cfg.grok_model,
                "input": [{"role": "user", "content": PROMPT.format(symbol=symbol, address=address)}],
                "tools": [{"type": "x_search"}],
            })
            r.raise_for_status()
            result = parse_sentiment(_output_text(r.json()))
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            result = None  # treat as "no opinion"; caller decides
        self._cache[address] = (time.time(), result)
        return result
