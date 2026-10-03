from __future__ import annotations

from .models import Evidence, EvidenceKind, ResearchState
from .rag import Chunk, TOKEN


def audit_grounding(state: ResearchState) -> list[str]:
    issues: list[str] = []
    source_ids = {source.id for source in state.sources}
    experiment_ids = {experiment.id for experiment in state.experiments_completed if experiment.status == "completed"}
    for evidence in state.evidence:
        if evidence.kind == EvidenceKind.LITERATURE:
            if evidence.source_id not in source_ids:
                issues.append(f"{evidence.id}: literature claim lacks a retrieved source")
            if not evidence.chunk_id:
                issues.append(f"{evidence.id}: literature claim lacks a retrieved passage identifier")
            if evidence.support_score is None or evidence.support_score < 0.35:
                issues.append(f"{evidence.id}: retrieved passage does not meet the support threshold")
        if evidence.kind in (EvidenceKind.COMPUTATION, EvidenceKind.OBSERVATION) and evidence.experiment_id not in experiment_ids:
            issues.append(f"{evidence.id}: quantitative claim lacks a completed experiment")
        if not 0 <= evidence.confidence <= 1:
            issues.append(f"{evidence.id}: confidence outside [0, 1]")
    return issues


def passage_support_score(claim: str, chunk: Chunk) -> float:
    claim_terms = {term.lower() for term in TOKEN.findall(claim) if len(term) > 3}
    passage_terms = {term.lower() for term in TOKEN.findall(chunk.text)}
    return len(claim_terms & passage_terms) / len(claim_terms) if claim_terms else 0.0


def verify_passage_support(evidence: Evidence, chunks: list[Chunk], threshold: float = 0.35) -> dict[str, object]:
    candidates = [chunk for chunk in chunks if chunk.source_id == evidence.source_id and
                  (not evidence.chunk_id or chunk.id == evidence.chunk_id)]
    scored = sorted(((chunk.id, passage_support_score(evidence.claim, chunk)) for chunk in candidates),
                    key=lambda item: item[1], reverse=True)
    best = scored[0] if scored else (None, 0.0)
    return {"supported": best[1] >= threshold, "chunk_id": best[0], "score": best[1], "threshold": threshold}
