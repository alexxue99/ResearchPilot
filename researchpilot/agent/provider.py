from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Any, Protocol
from ..pricing import ModelPrice, price_from_env


class LLMProvider(Protocol):
    """Provider-neutral structured generation boundary."""

    name: str

    def generate(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]: ...


class NoLLMProvider:
    name = "deterministic-baseline"

    def generate(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("No LLM configured. Deterministic research tools remain available.")


class ProviderError(RuntimeError):
    """Sanitized provider failure safe to persist in a research trace."""


class ModelLimitError(ProviderError):
    """The model cannot accept more work for this investigation."""


class OpenAIResponsesProvider:
    """Dependency-free Responses API adapter with strict structured outputs.

    The model is explicit to make evaluation runs reproducible. API keys are read from the
    environment and are never copied into research state, traces, or exceptions.
    """

    supports_web_search = True
    # https://developers.openai.com/api/docs/pricing (2026-09-30): $10 / 1,000 searches.
    web_search_cost_per_call = 0.01

    def __init__(self, api_key: str, model: str, *, endpoint: str = "https://api.openai.com/v1/responses",
                 timeout: float = 60.0, code_timeout: float = 300.0, planning_timeout: float = 300.0, max_retries: int = 2,
                 max_output_tokens: int | None = None,
                 input_cost_per_million: float = 0.0, output_cost_per_million: float = 0.0) -> None:
        if not api_key: raise ValueError("api_key is required")
        if not model: raise ValueError("model is required")
        if not math.isfinite(code_timeout) or code_timeout <= 0:
            raise ValueError("code_timeout must be a positive, finite number of seconds")
        if not math.isfinite(planning_timeout) or planning_timeout <= 0:
            raise ValueError("planning_timeout must be a positive, finite number of seconds")
        self.api_key, self.model, self.name = api_key, model, f"openai:{model}"
        self.endpoint, self.timeout, self.max_retries = endpoint, timeout, max_retries
        self.code_timeout = code_timeout
        self.planning_timeout = planning_timeout
        self.max_output_tokens = max_output_tokens
        self.last_usage: dict[str, int] = {}
        self.last_web_search_calls = 0
        self.input_cost_per_million = input_cost_per_million
        self.output_cost_per_million = output_cost_per_million
        self.last_cost_usd = 0.0
        self.price = ModelPrice(input_cost_per_million, output_cost_per_million)
        self.price_known = bool(input_cost_per_million or output_cost_per_million)

    @classmethod
    def from_env(cls, model: str | None = None, max_output_tokens: int | None = None) -> "OpenAIResponsesProvider":
        api_key, model = os.environ.get("OPENAI_API_KEY", ""), model or os.environ.get("RESEARCHPILOT_STRONG_MODEL", "")
        if not api_key or not model:
            raise ValueError("OPENAI_API_KEY and RESEARCHPILOT_STRONG_MODEL (or an explicit model) must both be configured")
        code_timeout = float(os.environ.get("RESEARCHPILOT_CODE_TIMEOUT_SECONDS", "300"))
        planning_timeout = float(os.environ.get("RESEARCHPILOT_PLANNING_TIMEOUT_SECONDS", "300"))
        provider = cls(api_key, model, max_output_tokens=max_output_tokens, code_timeout=code_timeout,
                       planning_timeout=planning_timeout)
        price = price_from_env("openai", model)
        if price is not None:
            provider.price = price
            provider.input_cost_per_million = price.input_per_million
            provider.output_cost_per_million = price.output_per_million
        provider.price_known = price is not None
        return provider

    def generate(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        return self._generate(messages, schema)

    def generate_code(self, messages, schema):
        """Allow longer code responses without changing other concurrent calls."""
        return self._generate(messages, schema, timeout=self.code_timeout)

    def generate_plan(self, messages, schema):
        return self._generate(messages, schema, timeout=self.planning_timeout)

    def generate_with_search(self, messages, schema, *, max_tool_calls: int = 4):
        if max_tool_calls < 1:
            raise ValueError("web search requires at least one available tool call")
        return self._generate(messages, schema, max_tool_calls=max_tool_calls)

    def _generate(self, messages, schema, *, max_tool_calls: int | None = None, timeout: float | None = None):
        self.last_usage, self.last_cost_usd = {}, 0.0
        self.last_web_search_calls = 0
        instructions = "\n\n".join(item["content"] for item in messages if item.get("role") in ("system", "developer"))
        inputs = [{"role": item.get("role", "user"), "content": item["content"]}
                  for item in messages if item.get("role") not in ("system", "developer")]
        payload = {"model": self.model, "input": inputs, "store": False,
                   "text": {"format": {"type": "json_schema", "name": "researchpilot_output",
                                       "strict": True, "schema": schema}}}
        if max_tool_calls is not None:
            payload.update(tools=[{"type": "web_search"}], tool_choice="auto",
                           include=["web_search_call.action.sources"], max_tool_calls=max_tool_calls)
        if self.max_output_tokens is not None:
            payload["max_output_tokens"] = self.max_output_tokens
        if instructions: payload["instructions"] = instructions
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
            "User-Agent": "ResearchPilot/0.2"})
        started = time.monotonic()
        request_timeout = self.timeout if timeout is None else timeout
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=request_timeout) as response:
                    data = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    if attempt < self.max_retries:
                        time.sleep(0.25 * (2 ** attempt)); continue
                    raise ModelLimitError("The LLM rate or usage limit was reached.") from exc
                if exc.code == 400:
                    try:
                        error = json.load(exc).get("error", {})
                        code = error.get("code") or error.get("type")
                    except (ValueError, AttributeError):
                        code = None
                    if code in ("context_length_exceeded", "input_tokens_exceeded"):
                        raise ModelLimitError("The LLM context limit was reached.") from exc
                retryable = exc.code >= 500
                if retryable and attempt < self.max_retries:
                    time.sleep(0.25 * (2 ** attempt)); continue
                raise ProviderError(f"Responses API request failed with HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self.max_retries:
                    time.sleep(0.25 * (2 ** attempt)); continue
                cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                kind = "timeout" if isinstance(cause, TimeoutError) else type(cause).__name__
                raise ProviderError(
                    f"Responses API request failed after {attempt + 1} attempts: {kind}; "
                    f"timeout={request_timeout:g}s; elapsed={time.monotonic() - started:.1f}s") from exc
        self.last_usage = {key: int(value) for key, value in data.get("usage", {}).items()
                           if isinstance(value, (int, float))}
        self.last_cost_usd = self.price.cost(data.get("usage", {}))
        search_items = [item for item in data.get("output", []) if item.get("type") == "web_search_call"]
        self.last_web_search_calls = len(search_items)
        self.last_cost_usd += self.web_search_cost_per_call * sum(
            item.get("action", {}).get("type") == "search" for item in search_items)
        if data.get("status") not in (None, "completed"):
            reason = data.get("incomplete_details", {}).get("reason")
            detail = f" ({reason})" if reason in ("max_output_tokens", "content_filter") else ""
            raise ProviderError(f"Responses API returned status {data.get('status')!r}{detail}")
        text = next((content.get("text") for item in data.get("output", []) if item.get("type") == "message"
                     for content in item.get("content", []) if content.get("type") == "output_text"), None)
        if not text:
            raise ProviderError("Responses API returned no structured output text")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError("Responses API returned invalid JSON despite structured-output request") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("structured output must be a JSON object")
        if max_tool_calls is not None:
            sources = {}
            for item in search_items:
                for source in item.get("action", {}).get("sources", []):
                    if isinstance(source.get("url"), str):
                        sources[source["url"]] = {"url": source["url"], "title": source.get("title", "")}
            for item in data.get("output", []):
                if item.get("type") == "message":
                    for content in item.get("content", []):
                        for citation in content.get("annotations", []):
                            if citation.get("type") == "url_citation" and isinstance(citation.get("url"), str):
                                sources[citation["url"]] = {"url": citation["url"], "title": citation.get("title", "")}
            # Transport metadata is supplied by the API, never trusted from the model's JSON.
            parsed["_web_search_sources"] = list(sources.values())
            parsed["_web_search_calls"] = self.last_web_search_calls
        return parsed
