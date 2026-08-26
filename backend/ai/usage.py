"""Isolated token-price accounting for AI jobs."""

from __future__ import annotations

from typing import Optional


# USD per one million text tokens. Update from official provider pricing; never
# duplicate these figures in business logic.
MODEL_PRICES = {
    "gpt-5.6-luna": {"input": 0.20, "output": 1.20},
    "gpt-5.6-terra": {"input": 2.00, "output": 12.00},
    "gpt-5.6-sol": {"input": 4.00, "output": 20.00},
    "gpt-5-mini": {"input": 0.25, "output": 2.00},
    "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    price = MODEL_PRICES.get(model)
    if not price:
        return None
    return round((input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000, 8)
