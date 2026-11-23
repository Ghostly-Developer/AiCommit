"""Token usage accounting and cost estimates."""

from __future__ import annotations

import os
from dataclasses import dataclass

# USD per 1M tokens: (input, output). Thinking tokens bill at the output rate.
# Paid-tier list prices; the AI Studio free tier costs $0. Prices change, so override with
# AICOMMIT_PRICE_INPUT / AICOMMIT_PRICE_OUTPUT rather than trusting this table blindly.
PRICES: dict[str, tuple[float, float]] = {
    # Introductory rate through 2026-12-31; scheduled to rise to 1.50 / 7.50 on 2027-01-01.
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    calls: int = 0

    @classmethod
    def from_metadata(cls, meta: object | None) -> "Usage":
        """Build from Gemini's `usage_metadata` (any field may be None)."""
        if meta is None:
            return cls(calls=1)
        return cls(
            input_tokens=getattr(meta, "prompt_token_count", None) or 0,
            output_tokens=getattr(meta, "candidates_token_count", None) or 0,
            thinking_tokens=getattr(meta, "thoughts_token_count", None) or 0,
            calls=1,
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.thinking_tokens + other.thinking_tokens,
            self.calls + other.calls,
        )

    @property
    def billed_output_tokens(self) -> int:
        return self.output_tokens + self.thinking_tokens


def price_for(model: str) -> tuple[float, float] | None:
    env_in, env_out = os.getenv("AICOMMIT_PRICE_INPUT"), os.getenv("AICOMMIT_PRICE_OUTPUT")
    if env_in and env_out:
        try:
            return float(env_in), float(env_out)
        except ValueError:
            pass
    # Exact match first, then the longest known prefix (e.g. "gemini-2.5-flash-001").
    if model in PRICES:
        return PRICES[model]
    for name in sorted(PRICES, key=len, reverse=True):
        if model.startswith(name):
            return PRICES[name]
    return None


def estimate_cost(usage: Usage, model: str) -> float | None:
    price = price_for(model)
    if price is None:
        return None
    p_in, p_out = price
    return (usage.input_tokens * p_in + usage.billed_output_tokens * p_out) / 1_000_000


def format_usage(usage: Usage, model: str) -> str:
    parts = [f"{usage.input_tokens:,} in", f"{usage.output_tokens:,} out"]
    if usage.thinking_tokens:
        parts.append(f"{usage.thinking_tokens:,} thinking")
    line = "tokens: " + " / ".join(parts)
    if usage.calls > 1:
        line += f" across {usage.calls} calls"
    cost = estimate_cost(usage, model)
    if cost is None:
        line += f" | cost: unknown for {model} (set AICOMMIT_PRICE_INPUT/OUTPUT, USD per 1M tokens)"
    else:
        line += f" | est. cost ${cost:.5f}"
    return line
