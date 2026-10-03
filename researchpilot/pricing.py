"""Explicit, versioned price configuration; unknown models are never guessed."""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None

    def __post_init__(self):
        for value in (self.input_per_million, self.output_per_million, self.cached_input_per_million):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("prices must be finite and nonnegative")

    def cost(self, usage: dict) -> float:
        inputs, outputs = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        cached = int(usage.get("input_tokens_details", {}).get("cached_tokens", 0))
        if min(inputs, outputs, cached) < 0 or cached > inputs:
            raise ValueError("invalid token usage")
        cached_rate = self.input_per_million if self.cached_input_per_million is None else self.cached_input_per_million
        return ((inputs - cached) * self.input_per_million + cached * cached_rate +
                outputs * self.output_per_million) / 1_000_000


def price_from_env(provider: str, model: str) -> ModelPrice | None:
    input_rate = os.environ.get("OPENAI_INPUT_COST_PER_MILLION")
    output_rate = os.environ.get("OPENAI_OUTPUT_COST_PER_MILLION")
    if input_rate is not None or output_rate is not None:
        if input_rate is None or output_rate is None:
            raise ValueError("both input and output prices must be configured")
        cached = os.environ.get("OPENAI_CACHED_INPUT_COST_PER_MILLION")
        return ModelPrice(float(input_rate), float(output_rate), float(cached) if cached is not None else None)
    path = os.environ.get("RESEARCHPILOT_PRICE_TABLE")
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("currency") != "USD":
        raise ValueError("price table currency must be USD")
    date.fromisoformat(payload["as_of"])
    entry = payload["providers"].get(provider, {}).get(model)
    return ModelPrice(**entry) if entry is not None else None
