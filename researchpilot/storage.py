from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import fields
from pathlib import Path
from typing import Any

from .models import (Evidence, EvidenceKind, ExperimentDesign, ExperimentResult,
                     ResearchState, Source, Status, TraceEvent, Conjecture, EvidenceItem,
                     ExperimentalEvidence, ConfidenceFactors, ConjectureAssessment)


class ResearchRepository:
    """SQLite persistence with JSON snapshots for durable, inspectable state."""

    def __init__(self, path: str | Path = "researchpilot.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("CREATE TABLE IF NOT EXISTS research (id TEXT PRIMARY KEY, status TEXT, updated_at TEXT, state_json TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS ix_research_status ON research(status)")
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save(self, state: ResearchState) -> None:
        payload = json.dumps(state.to_dict(), ensure_ascii=False)
        with closing(self._connect()) as db:
            db.execute("INSERT INTO research VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at, state_json=excluded.state_json",
                       (state.id, str(state.status), state.updated_at, payload))
            db.commit()

    def get(self, research_id: str) -> ResearchState | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT state_json FROM research WHERE id=?", (research_id,)).fetchone()
        if not row:
            return None
        state = state_from_dict(json.loads(row[0]))
        from .assessment_summary import assessment_evidence_summary
        if state.status == Status.COMPLETED and state.conjecture and state.report and (
            not state.conjecture_judgment or state.conjecture_judgment not in state.report
            or "## Related evidence" not in state.report or "ResearchPilot confidence estimate:" in state.report
            or assessment_evidence_summary(state) not in state.report
        ):
            # Keep completed investigations readable with the current report layout.
            from .deployment import DeploymentConfig
            from .reporting import render_conjecture_report
            config = DeploymentConfig.from_env()
            state.report = render_conjecture_report(state, config.max_output_tokens if config.bounded else None)
        return state

    def list(self) -> list[dict[str, str]]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT id, status, updated_at, json_extract(state_json, '$.question') FROM research ORDER BY updated_at DESC").fetchall()
        return [{"id": r[0], "status": r[1], "updated_at": r[2], "question": r[3] or ""} for r in rows]


def _pick(cls: type, value: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in value.items() if k in names}


def state_from_dict(data: dict[str, Any]) -> ResearchState:
    data = dict(data)
    data["status"] = Status(data["status"])
    data["sources"] = [Source(**_pick(Source, x)) for x in data.get("sources", [])]
    data["evidence"] = [Evidence(**{**_pick(Evidence, x), "kind": EvidenceKind(x["kind"])}) for x in data.get("evidence", [])]
    raw_designs = data.get("experiments_planned", [])
    data["experiments_planned"] = [ExperimentDesign(**_pick(ExperimentDesign, x)) for x in raw_designs]
    data["experiments_completed"] = [ExperimentResult(**_pick(ExperimentResult, x)) for x in data.get("experiments_completed", [])]
    # Older snapshots stored execution failures only at investigation scope.
    # Attribute them only when there is exactly one design and no recorded run.
    if len(data["experiments_planned"]) == 1 and not data["experiments_completed"]:
        design = data["experiments_planned"][0]
        failures = [item for item in data.get("failed_attempts", []) if item.startswith("experiment:")]
        if failures and "execution_status" not in raw_designs[0]:
            design.execution_status = "timeout" if "executor timeout:" in failures[-1] else "failed"
            design.execution_error = failures[-1]
    data["trace"] = [TraceEvent(**_pick(TraceEvent, x)) for x in data.get("trace", [])]
    if data.get("conjecture"):
        data["conjecture"] = Conjecture(**_pick(Conjecture, data["conjecture"]))
    data["structured_evidence"] = [EvidenceItem(**_pick(EvidenceItem, x)) for x in data.get("structured_evidence", [])]
    data["experimental_evidence"] = [ExperimentalEvidence(**_pick(ExperimentalEvidence, x)) for x in data.get("experimental_evidence", [])]
    if data.get("confidence_factors"):
        data["confidence_factors"] = ConfidenceFactors(**_pick(ConfidenceFactors, data["confidence_factors"]))
    if data.get("assessment"):
        value = dict(data["assessment"])
        for key in ("literature_support", "literature_contradictions", "literature_qualifications", "literature_related"):
            value[key] = [EvidenceItem(**_pick(EvidenceItem, x)) for x in value.get(key, [])]
        for key in ("experimental_support", "experimental_contradictions"):
            value[key] = [ExperimentalEvidence(**_pick(ExperimentalEvidence, x)) for x in value.get(key, [])]
        data["assessment"] = ConjectureAssessment(**_pick(ConjectureAssessment, value))
    state = ResearchState(**_pick(ResearchState, data))
    # Improve saved assessments too, without altering stored provenance or rerunning models.
    from .display_references import readable_references
    state.report = readable_references(state.report, state)
    state.conjecture_judgment = readable_references(state.conjecture_judgment, state)
    state.judgment_rationale = readable_references(state.judgment_rationale, state)
    state.confidence_rationale = readable_references(state.confidence_rationale, state)
    if state.confidence_method == 'unavailable' and (
        not state.confidence_rationale.strip()
        or state.confidence_rationale.startswith('No model assessment was available;')
    ):
        from .assessment_summary import assessment_evidence_summary
        state.confidence_rationale = assessment_evidence_summary(state)
    if state.assessment:
        state.assessment.synthesis_summary = readable_references(state.assessment.synthesis_summary, state)
    # Older snapshots can have a terminal research state with an open trace row.
    if state.status in (Status.COMPLETED, Status.FAILED, Status.CANCELLED):
        state.finish_investigation_trace(str(state.status),
            f"Investigation {str(state.status)}.", touch=False)
    return state
