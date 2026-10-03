"""Deployment model routing, independent of agent decisions."""
from __future__ import annotations

import os
from pathlib import Path

from ..deployment import DeploymentConfig
from .cached import CachedProvider
from .provider import OpenAIResponsesProvider


def providers_from_env(config: DeploymentConfig, workspace: str | Path, *, choice: str | None = None):
    choice = (choice or os.getenv("RESEARCHPILOT_PROVIDER", "auto")).strip().lower()
    if choice not in ("auto", "openai", "deterministic"):
        raise ValueError("RESEARCHPILOT_PROVIDER must be auto, openai, or deterministic")
    if choice == "deterministic" or (choice == "auto" and not os.getenv("OPENAI_API_KEY")):
        return None, None
    if not config.bounded:
        routine = OpenAIResponsesProvider.from_env(config.routine_model or os.getenv("RESEARCHPILOT_STRONG_MODEL"))
        routine.timeout = config.model_timeout_seconds
        routine.planning_timeout = routine.code_timeout = config.planning_timeout_seconds
        return routine, routine
    if not config.routine_model or not config.synthesis_model:
        raise ValueError("Limited/Restricted OpenAI routing requires configured weak and strong model names (Restricted needs only weak)")
    # Structured interpretation and executable experiment designs need more than a
    # short classification response. The per-run token and cost checks still apply.
    routine = OpenAIResponsesProvider.from_env(config.routine_model,
                                               max_output_tokens=min(15000, config.max_output_tokens))
    synthesis = (routine if config.mode == "Restricted" else
                 OpenAIResponsesProvider.from_env(config.synthesis_model,
                                                  max_output_tokens=min(5000, config.max_output_tokens)))
    for provider in (routine, synthesis):
        provider.timeout = config.model_timeout_seconds
        provider.planning_timeout = provider.code_timeout = config.planning_timeout_seconds
    if not routine.price_known or not synthesis.price_known:
        raise ValueError("bounded OpenAI routing requires known model prices")
    cache_path = Path(workspace) / "cache" / "llm.sqlite"
    cached = CachedProvider(routine, cache_path, config.cache_version, config.cache_ttl_seconds)
    return cached, (cached if config.mode == "Restricted" else
                    CachedProvider(synthesis, cache_path, config.cache_version, config.cache_ttl_seconds))
