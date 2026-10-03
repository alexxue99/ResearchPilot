from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Status(StrEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvidenceKind(StrEnum):
    LITERATURE = "literature"
    COMPUTATION = "computation"
    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"


@dataclass(slots=True)
class Source:
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    url: str | None = None
    verified: bool = False


@dataclass(slots=True)
class Evidence:
    id: str
    kind: EvidenceKind
    claim: str
    source_id: str | None = None
    chunk_id: str | None = None
    experiment_id: str | None = None
    support: str = ""
    confidence: float = 0.5
    support_score: float | None = None


@dataclass(slots=True)
class ExperimentDesign:
    id: str
    name: str
    hypothesis: str
    independent_variables: list[str]
    dependent_variables: list[str]
    controls: list[str]
    baselines: list[str]
    metrics: list[str]
    parameter_ranges: dict[str, Any]
    seeds: list[int]
    expected_behavior: str
    confounders: list[str]
    code: str = ""
    expected_if_false: str = ""
    limitations: list[str] = field(default_factory=list)
    test_type: str = "illustrative"
    rationale: str = ""
    assumptions: list[str] = field(default_factory=list)
    algorithm_steps: list[str] = field(default_factory=list)
    visualization_code: str = ""
    purpose: str = ""
    decision_criteria: str = ""
    additional_assumptions: list[str] = field(default_factory=list)

    planning_schema_version: int = 1
    execution_status: str = "planned"
    execution_error: str = ""

    def model_context(self) -> dict[str, Any]:
        """Provide experimental methods and settings without planner assessments."""
        context = {"name": self.name, "baselines": self.baselines, "metrics": self.metrics,
                "parameters": self.parameter_ranges, "seeds": self.seeds,
                "algorithm_steps": self.algorithm_steps, "assumptions": self.assumptions,
                "test_type": self.test_type}
        for key in ("independent_variables", "dependent_variables", "controls"):
            if value := getattr(self, key):
                context[key] = value
        return context


@dataclass(slots=True)
class ExperimentResult:
    id: str
    design_id: str
    status: str
    configuration: dict[str, Any]
    metrics: dict[str, Any]
    seed: int | None = None
    runtime_seconds: float = 0.0
    artifacts: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    software_versions: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class TraceEvent:
    sequence: int
    action: str
    status: str
    summary: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    created_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class Conjecture:
    original_statement: str
    normalized_statement: str
    mathematical_domain: str = "mathematics"
    objects: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    measurable_predictions: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    experimentable: bool = False
    supporting_observations: list[str] = field(default_factory=list)
    contradicting_observations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EvidenceItem:
    source_id: str
    citation: str
    statement: str
    evidence_type: str
    relation_to_conjecture: str
    source_location: str | None = None
    assumptions: list[str] = field(default_factory=list)
    strength: float = 0.0
    notes: str = ""


@dataclass(slots=True)
class ExperimentalEvidence:
    experiment_id: str
    finding: str
    relation_to_conjecture: str
    effect_size: float | None = None
    uncertainty: str = ""
    robustness: str = ""
    limitations: list[str] = field(default_factory=list)
    confounders: list[str] = field(default_factory=list)
    evidence_paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConfidenceFactors:
    direct_theoretical_support: float = 0.0
    indirect_theoretical_support: float = 0.0
    empirical_support: float = 0.0
    experimental_support: float = 0.0
    contradictory_evidence: float = 0.0
    counterexample_strength: float = 0.0
    assumption_mismatch: float = 0.0
    unresolved_uncertainty: float = 0.0
    evidence_coverage: float = 0.0


@dataclass(slots=True)
class ConjectureAssessment:
    literature_support: list[EvidenceItem] = field(default_factory=list)
    literature_contradictions: list[EvidenceItem] = field(default_factory=list)
    literature_qualifications: list[EvidenceItem] = field(default_factory=list)
    literature_related: list[EvidenceItem] = field(default_factory=list)
    experimental_support: list[ExperimentalEvidence] = field(default_factory=list)
    experimental_contradictions: list[ExperimentalEvidence] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    key_assumptions: list[str] = field(default_factory=list)
    revised_conjecture: str | None = None
    synthesis_summary: str = ""


@dataclass(slots=True)
class ResearchState:
    question: str
    objective: str
    id: str = field(default_factory=lambda: str(uuid4()))
    status: Status = Status.DRAFT
    assumptions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    context_source_ids: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    mathematical_observations: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    experiments_planned: list[ExperimentDesign] = field(default_factory=list)
    experiments_completed: list[ExperimentResult] = field(default_factory=list)
    failed_attempts: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    conclusions: list[str] = field(default_factory=list)
    critique: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    trace: list[TraceEvent] = field(default_factory=list)
    confidence: float = 0.0
    conjecture: Conjecture | None = None
    literature_queries: list[str] = field(default_factory=list)
    general_reasoning: list[dict[str, Any]] = field(default_factory=list)
    reasoning_source_ids: list[str] = field(default_factory=list)
    additional_literature_source_ids: list[str] = field(default_factory=list)
    selected_source_ids: list[str] = field(default_factory=list)
    source_aliases: dict[str, str] = field(default_factory=dict)
    literature_reviews: list[dict[str, Any]] = field(default_factory=list)
    structured_evidence: list[EvidenceItem] = field(default_factory=list)
    experimental_evidence: list[ExperimentalEvidence] = field(default_factory=list)
    assessment: ConjectureAssessment | None = None
    conjecture_judgment: str = ""
    judgment_rationale: str = ""
    judgment_method: str = "unavailable"
    # Legacy numeric assessment fields remain readable for saved snapshots.
    confidence_factors: ConfidenceFactors | None = None
    confidence_rationale: str = ""
    confidence_method: str = "unavailable"
    report: str = ""
    pending_action: str = ""
    pending_experiment_id: str = ""
    model: str = "deterministic-baseline"
    model_calls: int = 0
    tool_calls: int = 0
    token_usage: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    agent_steps: int = 0
    candidate_papers: int = 0
    stochastic_trials: int = 0
    inspected_papers: list[str] = field(default_factory=list)
    cache_hits: int = 0
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    estimated_cost_usd: float = 0.0
    cost_estimate_complete: bool = True
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def record(self, action: str, status: str, summary: str, *, count_as_tool: bool = False, **details: Any) -> None:
        self.trace.append(TraceEvent(len(self.trace) + 1, action, status, summary,
                                     details.get("inputs", {}), details.get("outputs", {}),
                                     details.get("latency_ms", 0.0)))
        if count_as_tool: self.tool_calls += 1
        self.updated_at = utc_now()

    def finish_investigation_trace(self, status: str, summary: str, *, touch: bool = True) -> None:
        """Resolve open investigation rows when a run ends."""
        latest = True
        for event in reversed(self.trace):
            if event.action == "investigation" and event.status == "running":
                event.status = status if latest else "failed"
                event.summary = summary if latest else "Previous investigation attempt ended without completion."
                if touch:
                    self.updated_at = utc_now()
                latest = False

    def to_dict(self) -> dict[str, Any]:
        from .assessment_summary import assessment_evidence_summary
        return {**asdict(self), 'assessment_evidence_summary': assessment_evidence_summary(self)}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"
