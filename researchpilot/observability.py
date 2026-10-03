from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import ResearchState


class TraceExporter:
    """Exports a redacted, vendor-neutral trace snapshot for local inspection or shipping."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.telemetry = OpenTelemetryExporter() if os.environ.get("RESEARCHPILOT_OTEL") == "1" else None

    def export(self, state: ResearchState) -> Path:
        payload = self._redact({
            "research_id": state.id, "status": str(state.status), "model": state.model,
            "model_calls": state.model_calls, "tool_calls": state.tool_calls,
            "agent_steps": state.agent_steps, "candidate_papers": state.candidate_papers,
            "inspected_papers": len(state.inspected_papers), "cache_hits": state.cache_hits,
            "evidence_items": len(state.structured_evidence),
            "supporting_items": sum(x.relation_to_conjecture == "supports" for x in state.structured_evidence),
            "contradicting_items": sum(x.relation_to_conjecture == "contradicts" for x in state.structured_evidence),
            "experiments_planned": len(state.experiments_planned),
            "experiments_executed": sum(x.status == "completed" for x in state.experiments_completed),
            "failed_experiments": sum("experiment:" in x for x in state.failed_attempts),
            "stochastic_trials": state.stochastic_trials,
            "input_tokens": state.input_tokens, "output_tokens": state.output_tokens,
            "llm_calls": state.llm_calls, "stop_reason": state.stop_reason,
            "token_usage": state.token_usage, "estimated_cost_usd": state.estimated_cost_usd,
            "cost_estimate_complete": state.cost_estimate_complete,
            "trace": [event.__dict__ if hasattr(event, "__dict__") else {
                "sequence": event.sequence, "action": event.action, "status": event.status,
                "summary": event.summary, "inputs": event.inputs, "outputs": event.outputs,
                "latency_ms": event.latency_ms, "created_at": event.created_at} for event in state.trace]})
        target = self.root / f"{state.id}.trace.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(target)
        if self.telemetry:
            self.telemetry.export(state)
        return target

    def close(self):
        if self.telemetry:
            self.telemetry.close()

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            counters = {"token_usage", "input_tokens", "output_tokens"}
            return {key: "[REDACTED]" if any(term in key.lower() for term in ("token", "secret", "password", "api_key")) and key not in counters else self._redact(item)
                    for key, item in value.items()}
        if isinstance(value, list): return [self._redact(item) for item in value]
        return value


class OpenTelemetryExporter:
    """Opt-in OTLP export of operational metadata only; no prompts or paper contents."""

    def __init__(self, provider=None):
        if provider is None:
            try:
                from opentelemetry.sdk.resources import Resource
                from opentelemetry.sdk.trace import TracerProvider
                from opentelemetry.sdk.trace.export import BatchSpanProcessor
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            except ImportError as exc:
                raise RuntimeError("Install the 'telemetry' extra to enable OpenTelemetry") from exc
            provider = TracerProvider(resource=Resource.create({"service.name": "researchpilot"}))
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        self.provider = provider
        self.tracer = provider.get_tracer("researchpilot")
        self.sequences: dict[str, int] = {}
        self.lock = threading.Lock()

    def export(self, state: ResearchState):
        from opentelemetry.trace import Status, StatusCode
        with self.lock:
            last = self.sequences.get(state.id, 0)
            for event in state.trace:
                if event.sequence <= last:
                    continue
                end = int(datetime.fromisoformat(event.created_at).timestamp() * 1_000_000_000)
                start = end - int(max(0, event.latency_ms) * 1_000_000)
                span = self.tracer.start_span(event.action, start_time=start, attributes={
                    "research.id": state.id, "research.sequence": event.sequence,
                    "research.action_status": event.status, "research.model": state.model,
                    "research.tool_calls": state.tool_calls, "research.model_calls": state.model_calls,
                    "research.token_usage": state.token_usage, "research.estimated_cost_usd": state.estimated_cost_usd,
                    "research.cost_estimate_complete": state.cost_estimate_complete})
                if event.status in ("failed", "timeout", "invalid", "unavailable"):
                    span.set_status(Status(StatusCode.ERROR))
                span.end(end_time=end)
                self.sequences[state.id] = event.sequence

    def close(self):
        self.provider.force_flush(timeout_millis=5000)
        self.provider.shutdown()
