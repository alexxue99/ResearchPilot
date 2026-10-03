from __future__ import annotations

from .models import EvidenceKind, ResearchState
import json
from pathlib import Path

from .source_links import arxiv_url, source_url
from .display_references import experiment_labels, readable_references
from .assessment_summary import assessment_evidence_summary


def _experiment_details(state: ResearchState, compact: bool) -> str:
    sections = []
    labels = experiment_labels(state)
    results = {result.design_id: result for result in state.experiments_completed}
    for design in state.experiments_planned[:1 if compact else None]:
        if design.purpose or design.planning_schema_version >= 3:
            lines = [f"## {labels[design.id]}", "",
                     *([f"**Purpose:** {design.purpose}"] if design.planning_schema_version < 3 else []),
                     f"**Test type:** {design.test_type}.",
                     f"**Baselines:** {', '.join(design.baselines) or 'Not specified.'}",
                     f"**Metrics:** {', '.join(design.metrics)}", "", "**Algorithm pseudocode:**",
                     *(f"{index}. {step}" for index, step in enumerate(design.algorithm_steps, 1)),
                     "", "**Working assumptions:**", *(f"- {item}" for item in design.assumptions),
                     "**Exact planned settings:**",
                     *(f"- {key.replace('_', ' ')}: `{json.dumps(value, ensure_ascii=False, default=str)}`"
                       for key, value in design.parameter_ranges.items()),
                     f"- random seeds: `{design.seeds}`.", "",
                     *([f"**Decision criteria:** {design.decision_criteria}"]
                       if design.planning_schema_version < 3 else [])]
        else:
            lines = [f"## {labels[design.id]}", "",
                     f"**Why this experiment:** {design.rationale or 'No selection rationale recorded.'}", "",
                     f"**Hypothesis:** {design.hypothesis}",
                     f"**Test type:** {design.test_type}.",
                     f"**Independent variables:** {', '.join(design.independent_variables) or 'Not specified.'}",
                     f"**Measured outcomes:** {', '.join(design.dependent_variables) or 'Not specified.'}",
                     f"**Baselines:** {', '.join(design.baselines) or 'Not specified.'}",
                     f"**Metrics:** {', '.join(design.metrics) or 'Not specified.'}", "",
                     "**Algorithm pseudocode:**",
                     *(f"{index}. {step}" for index, step in enumerate(design.algorithm_steps, 1)),
                     *(["No algorithm pseudocode was recorded."] if not design.algorithm_steps else []), "",
                     "**Working assumptions:**",
                     *(f"- {item}" for item in design.assumptions),
                     "**Controls:**", *(f"- {item}" for item in design.controls),
                     "**Exact planned settings:**",
                     *(f"- {key.replace('_', ' ')}: `{json.dumps(value, ensure_ascii=False, default=str)}`"
                       for key, value in design.parameter_ranges.items()),
                     f"- random seeds: `{design.seeds}`.", "",
                     f"**Expected if supported:** {design.expected_behavior or 'Not specified.'}",
                     f"**Expected if contradicted:** {design.expected_if_false or 'Not specified.'}"]
        if design.planning_schema_version < 3 and (design.confounders or design.limitations):
            lines += ["", "**Confounders and limitations:**",
                      *(f"- {item}" for item in design.confounders + design.limitations)]
        result = results.get(design.id)
        if result:
            lines += ["", f"**Execution:** {result.status}; run `{result.id}`; "
                      f"runtime {result.runtime_seconds:.2f} seconds.",
                      f"**Recorded configuration:** `{json.dumps(result.configuration, ensure_ascii=False, default=str)}`"]
            if result.metrics:
                lines += [f"**Recorded metrics:** `{json.dumps(result.metrics, ensure_ascii=False, default=str)}`"]
            findings = [item for item in state.experimental_evidence if item.experiment_id == result.id]
            for item in findings:
                lines += [f"**Finding ({item.relation_to_conjecture}):** {item.finding}",
                          f"**Uncertainty:** {item.uncertainty}",
                          f"**Robustness:** {item.robustness}"]
                if item.confounders:
                    lines += ["**Outcome confounders:**", *(f"- {value}" for value in item.confounders)]
                if item.evidence_paths:
                    lines += ["**Result JSON fields used:** " + ", ".join(f"`{path}`" for path in item.evidence_paths)]
            if result.artifacts:
                lines += ["**Artifacts:** " + ", ".join(Path(path).name for path in result.artifacts)]
        else:
            lines += ["", "**Execution:** No successful run was recorded; this plan is not experimental evidence."]
        lines += ["", "Exact reusable experiment and visualization code is available on the Experiments page."]
        if design.code and not compact:
            lines += ["", "````python", design.code.rstrip(), "````"]
        if design.visualization_code and not compact:
            lines += ["", "**Visualization code (reads `result.json`):**", "", "````python",
                      design.visualization_code.rstrip(), "````"]
        sections.append("\n".join(lines))
    return "\n\n".join(sections) or "- No experiment was planned."


def render_conjecture_report(state: ResearchState, max_tokens: int | None = None) -> str:
    """Render only persisted findings, measured results, and identified sources."""
    c = state.conjecture
    a = state.assessment
    judgment = state.conjecture_judgment or "Qualitative judgment not yet generated"
    def literature(items):
        selected = items[:5] if max_tokens else items
        return "\n".join(f"- {x.statement[:300]} [{x.source_id}], {x.source_location or 'location unavailable'}; {x.evidence_type}."
                         + (f" Assumptions: {', '.join(x.assumptions)}." if x.assumptions else "")
                         + (f" {x.notes[:200]}" if x.notes else "")
                         for x in selected) or "- None verified."
    def experiments(items):
        return "\n".join(f"- {x.finding} [experiment {x.experiment_id}]; {x.uncertainty}" for x in items) or "- None recorded."
    def observations(relation, fallback):
        items = [item for item in state.general_reasoning if item["relation"] == relation]
        return ", ".join(item["observation"] +
                         (" [" + ", ".join(item["source_ids"]) + "]"
                          if item["source_ids"] else "; no corroborating source located")
                         for item in items) if items else ", ".join(fallback) or "Not specified."
    sources = {x.source_id for x in state.structured_evidence} | set(state.reasoning_source_ids)
    references = "\n".join(f"- [{s.title}]({url})." if (url := source_url(s)) else f"- {s.title}. Source link unavailable."
                           for s in state.sources if s.id in sources) or "- No source supported an extracted finding."
    limitations = list(dict.fromkeys((a.unresolved_questions if a else []) +
        (["The LLM limit was reached; this report uses only evidence collected before the limit. Model-dependent findings may be incomplete."]
         if state.stop_reason == "model_limit" else
         [f"Investigation stopped at {state.stop_reason}."] if state.stop_reason else []) +
        (["Some model calls, searches, or executions failed; see the trace."] if state.failed_attempts else [])))
    rendered = f"""# Conjecture

{c.normalized_statement if c else state.question}

Working assumptions: {', '.join(c.assumptions) if c and c.assumptions else 'None supplied or inferred.'}

Unverified supporting observations to check: {observations('supports', c.supporting_observations if c else [])}

Unverified contradicting observations to check: {observations('contradicts', c.contradicting_observations if c else [])}

# Assessment

**ResearchPilot assessment: {judgment}** {assessment_evidence_summary(state)}

{state.judgment_rationale or state.confidence_rationale}

{a.synthesis_summary if a and a.synthesis_summary else ''}

# Literature evidence

## Supporting evidence

{literature(a.literature_support if a else [])}

## Contradictory evidence

{literature(a.literature_contradictions if a else [])}

## Important qualifications

{literature(a.literature_qualifications if a else [])}

## Related evidence

{literature(a.literature_related if a and a.literature_related else [x for x in state.structured_evidence if x.relation_to_conjecture == 'neutral'])}

# Computational investigation

Experiments planned: {len(state.experiments_planned)}. Executed successfully: {sum(x.status == 'completed' for x in state.experiments_completed)}. Stochastic trials: {state.stochastic_trials}.

{_experiment_details(state, compact=max_tokens is not None)}

## Potential counterexamples

{experiments(a.experimental_contradictions if a else [])}

# Interpretation

{a.revised_conjecture if a and a.revised_conjecture else 'The evidence must be read under the recorded assumptions; no narrower conjecture was established.'}

# What remains uncertain

{chr(10).join('- ' + x for x in limitations) or '- Broader regimes and theoretical proof remain unverified.'}

# Sources

{references}

# Usage

{_model_usage(state)}
"""
    rendered = readable_references(rendered, state)
    return rendered[:max_tokens * 4] if max_tokens else rendered


def _evidence(state: ResearchState, kind: EvidenceKind) -> str:
    items = []
    for evidence in state.evidence:
        if evidence.kind != kind: continue
        provenance = f" [{evidence.source_id}; passage {evidence.chunk_id}]" if kind == EvidenceKind.LITERATURE else ""
        items.append(f"- {evidence.claim}{provenance}")
    return "\n".join(items) if items else "- None recorded."


def _cost_label(state: ResearchState) -> str:
    if not state.model_calls:
        return "$0.00 (responses reused from cache)" if state.llm_calls else "$0.00 (no live model requests)"
    if not state.cost_estimate_complete:
        return "Unavailable (model prices were not configured for every call)"
    return f"${state.estimated_cost_usd:.6f} USD estimated"


def _model_usage(state: ResearchState) -> str:
    calls = []
    for call in state.llm_calls:
        cost = ("cache hit; $0.00" if call.get("cache_hit") else
                f"${call['estimated_cost_usd']:.6f} estimated" if state.cost_estimate_complete else
                "cost unavailable")
        calls.append(f"- {call['stage']} ({call['model']}): "
                     f"{call['input_tokens']} input, {call['output_tokens']} output tokens; "
                     f"{call.get('status', 'completed')}; {cost}.")
    reused = sum(bool(call.get("cache_hit")) for call in state.llm_calls)
    return (f"Live model requests: {state.model_calls}. Reused model responses: {reused}. "
            f"All cache hits: {state.cache_hits}. "
            f"Tokens: {state.input_tokens} input, {state.output_tokens} output "
            f"({state.token_usage} total).\n\n"
            f"Estimated provider cost: {_cost_label(state)}.\n\n"
            + ("\n".join(calls) if calls else "- No model responses or requests were recorded."))



def render_bounded_report(state: ResearchState, max_tokens: int) -> str:
    """Compact public report that keeps references inside the output bound."""
    sources = sorted(state.sources, key=lambda s: s.id not in state.inspected_papers)[:5]
    references = "\n".join(f"- [{s.id}] [{s.title[:180]}]({url})" if (url := arxiv_url(s)) else f"- [{s.id}] {s.title[:180]} — arXiv version unavailable"
                           for s in sources) or "- No papers found."
    conclusion = "\n".join(state.conclusions) or "No supported conclusion yet."
    claims = "\n".join(f"- {e.claim[:300]} [{e.source_id}]" for e in state.evidence[:8]) or "- No passage-supported claims recorded."
    limitations = "\n".join(f"- {item[:240]}" for item in state.unresolved_questions[-4:]) or "- Further verification may be needed."
    limit = max_tokens * 4
    fixed = (f"# Research Question\n\n{state.question[:500]}\n\n"
             f"# LLM Usage and Cost\n\n{state.model_calls} live model requests; "
             f"{sum(bool(call.get('cache_hit')) for call in state.llm_calls)} reused model responses; "
             f"{state.token_usage} tokens; {_cost_label(state)}.\n\n# Findings\n\n")
    tail = f"\n\n# Passage-Supported Claims\n\n{claims}\n\n# Limitations\n\n{limitations}\n\n# References\n\n{references}\n"
    available = max(0, limit - len(fixed) - len(tail))
    if len(tail) > limit // 2:
        tail = tail[:limit // 2].rsplit("\n", 1)[0] + "\n"
        available = max(0, limit - len(fixed) - len(tail))
    if len(conclusion) > available:
        conclusion = conclusion[:max(0, available - 30)].rsplit(" ", 1)[0] + "… [output limit]"
    return (fixed + conclusion + tail)[:limit]
