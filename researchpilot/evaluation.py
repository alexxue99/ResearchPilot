from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent.planner import material_ambiguities, select_tools
from .citations import audit_grounding
from .models import EvidenceKind, ResearchState


@dataclass(slots=True)
class BenchmarkTask:
    id: str
    category: str
    prompt: str
    expected_tools: list[str]
    expected_behavior: list[str]


def load_benchmark(path: str | Path | None = None) -> list[BenchmarkTask]:
    path = Path(path) if path else Path(__file__).with_name("evaluation_data") / "benchmark.json"
    return [BenchmarkTask(**item) for item in json.loads(path.read_text(encoding="utf-8"))]


def precision_recall(actual: set[str], expected: set[str]) -> tuple[float, float]:
    overlap = len(actual & expected)
    return (overlap / len(actual) if actual else float(not expected),
            overlap / len(expected) if expected else 1.0)


def numerical_groundedness(state: ResearchState) -> dict[str, Any]:
    issues = audit_grounding(state)
    quantitative = [e for e in state.evidence if e.kind in (EvidenceKind.OBSERVATION, EvidenceKind.COMPUTATION)
                    and any(char.isdigit() for char in e.claim)]
    checks = []
    for evidence in quantitative:
        result = next((r for r in state.experiments_completed if r.id == evidence.experiment_id), None)
        numbers = []
        for match in re.finditer(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", evidence.claim):
            number = float(match.group())
            if evidence.claim[match.end():].lstrip().startswith("%"):
                number /= 100
            numbers.append(number)
        recorded = _numeric_values({"metrics": result.metrics, "configuration": result.configuration}) if result else []
        unsupported = [number for number in numbers if not any(math.isclose(number, value, rel_tol=5e-3, abs_tol=5e-6) for value in recorded)]
        checks.append({"evidence_id": evidence.id, "supported": not unsupported and bool(result),
                       "unsupported_values": unsupported})
    return {"score": sum(check["supported"] for check in checks) / len(checks) if checks else 1.0,
            "issues": issues, "checks": checks}


def _numeric_values(value: Any) -> list[float]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [number for item in value.values() for number in _numeric_values(item)]
    if isinstance(value, (list, tuple)):
        return [float(len(value))] + [number for item in value for number in _numeric_values(item)]
    return []


def citation_correctness(state: ResearchState) -> dict[str, Any]:
    source_by_id = {source.id: source for source in state.sources}
    claims = [e for e in state.evidence if e.kind == EvidenceKind.LITERATURE]
    checks = []
    for evidence in claims:
        source = source_by_id.get(evidence.source_id or "")
        checks.append({"evidence_id": evidence.id, "valid": bool(source and source.verified and (source.doi or source.arxiv_id)
                                                                   and evidence.chunk_id and evidence.support.strip()
                                                                   and evidence.support_score is not None and evidence.support_score >= 0.35),
                       "source_id": evidence.source_id})
    return {"score": sum(check["valid"] for check in checks) / len(checks) if checks else 1.0, "checks": checks}


def experiment_quality(state: ResearchState) -> dict[str, Any]:
    designs = {design.id: design for design in state.experiments_planned}
    checks = []
    for result in state.experiments_completed:
        design = designs.get(result.design_id)
        criteria = {
            "completed": result.status == "completed",
            "repeated_seeds": len(result.configuration.get("seeds", [])) >= 5,
            "controls": bool(design and design.controls),
            "baselines": bool(design and design.baselines),
            "metrics": bool(design and design.metrics and result.metrics),
            "reproducible": bool(result.software_versions and result.artifacts),
        }
        checks.append({"experiment_id": result.id, **criteria, "score": sum(criteria.values()) / len(criteria)})
    return {"score": sum(check["score"] for check in checks) / len(checks) if checks else 1.0, "checks": checks}


def efficiency(state: ResearchState) -> dict[str, Any]:
    runtime = sum(result.runtime_seconds for result in state.experiments_completed)
    return {"tool_calls": state.tool_calls, "model_calls": state.model_calls, "token_usage": state.token_usage,
            "estimated_cost_usd": state.estimated_cost_usd, "experiment_runtime_seconds": runtime,
            "trace_events": len(state.trace)}


def failure_handling(state: ResearchState) -> dict[str, Any]:
    results = state.experiments_completed
    failures = [index for index, result in enumerate(results) if result.status != "completed"]
    recovered = [index for index in failures if any(result.status == "completed" for result in results[index + 1:])]
    return {"failures": len(failures), "recovered": len(recovered),
            "score": len(recovered) / len(failures) if failures else 1.0,
            "failed_attempts_recorded": len(state.failed_attempts)}


def evaluate_investigation(state: ResearchState) -> dict[str, Any]:
    numeric = numerical_groundedness(state)
    citations = citation_correctness(state)
    experiments = experiment_quality(state)
    return {"research_id": state.id, "numerical_groundedness": numeric,
            "citation_correctness": citations, "experiment_quality": experiments,
            "failure_handling": failure_handling(state), "efficiency": efficiency(state),
            "aggregate_quality": (numeric["score"] + citations["score"] + experiments["score"] + failure_handling(state)["score"]) / 4}


class BenchmarkRunner:
    def run(self, tasks: list[BenchmarkTask]) -> dict[str, Any]:
        rows = []
        for task in tasks:
            actual = set(select_tools(task.prompt))
            precision, recall = precision_recall(actual, set(task.expected_tools))
            needs_clarification = bool(material_ambiguities(task.prompt))
            behavior_score = 1.0
            if "clarify" in task.expected_behavior and not needs_clarification:
                behavior_score = 0.0
            rows.append({"id": task.id, "category": task.category, "tool_precision": precision,
                         "tool_recall": recall, "behavior_score": behavior_score})
        mean = lambda key: sum(row[key] for row in rows) / len(rows)
        return {"tasks": len(rows), "tool_precision": mean("tool_precision"),
                "tool_recall": mean("tool_recall"), "behavior_score": mean("behavior_score"), "results": rows}
