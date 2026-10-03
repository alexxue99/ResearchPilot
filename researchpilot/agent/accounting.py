"""Per-run metering and conservative bounded-mode call admission."""
from __future__ import annotations

import json

from ..deployment import DeploymentConfig


def can_call(state, provider, messages, config: DeploymentConfig, schema=None, *, search_options=None) -> bool:
    if not config.bounded:
        return True
    if schema is not None and hasattr(provider, "will_hit"):
        hit = (provider.will_hit(messages, schema, search_options=search_options)
               if search_options else provider.will_hit(messages, schema))
        if hit:
            return True
    inner = getattr(provider, "provider", provider)
    price = getattr(inner, "price", None)
    if price is None or not getattr(inner, "price_known", False):
        raise ValueError("bounded modes require configured prices for every routed model")
    # Reserve about twice the tokens typical prose/JSON uses, while leaving room
    # for later interpretation calls. Actual provider usage is recorded afterward.
    estimated_input = max(1, (len(json.dumps(messages, ensure_ascii=False)) + 1) // 2)
    estimated_output = inner.max_output_tokens or config.max_output_tokens
    estimate = price.cost({"input_tokens": estimated_input, "output_tokens": estimated_output})
    if search_options:
        estimate += search_options["max_tool_calls"] * getattr(inner, "web_search_cost_per_call", 0.0)
    return (state.token_usage + estimated_input + estimated_output <= config.max_run_tokens
            and state.estimated_cost_usd + estimate <= config.max_cost_usd)


def record_call(state, provider, stage: str, *, status: str = "completed") -> None:
    usage = getattr(provider, "last_usage", {})
    hit = bool(getattr(provider, "cache_hit", False))
    if hit:
        state.cache_hits += 1
    else:
        state.model_calls += 1
    inputs = int(usage.get("input_tokens", 0))
    outputs = int(usage.get("output_tokens", 0))
    state.input_tokens += inputs
    state.output_tokens += outputs
    state.token_usage += int(usage.get("total_tokens", inputs + outputs))
    cost = float(getattr(provider, "last_cost_usd", 0.0))
    state.estimated_cost_usd += cost
    state.cost_estimate_complete &= bool(getattr(provider, "price_known", False))
    state.llm_calls.append({"stage": stage, "model": provider.name,
                            "input_tokens": inputs, "output_tokens": outputs,
                            "estimated_cost_usd": cost, "cache_hit": hit, "status": status})
