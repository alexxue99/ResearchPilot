"""Inspectable conjecture investigation stages with grounded model assessment."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
from dataclasses import asdict, replace
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlparse

from .agent.accounting import can_call, record_call
from .agent.provider import ModelLimitError, NoLLMProvider, ProviderError
from .agent.tools import default_registry
from . import prompts
from .display_references import reference_context, readable_references
from .deployment import DeploymentConfig
from .executor import executor_from_env
from .experiment_results import discover_measurements, normalize_measurements, resolve_pointer
from .experiments.visualization import validate_visualization_svg, visualization_svg_contract
from .models import (Conjecture, ConjectureAssessment, EvidenceItem,
                     ExperimentDesign, ExperimentResult, ExperimentalEvidence, ResearchState, Source, new_id)
from .source_links import arxiv_url, source_url
from .literature import has_article_title

STAGES = ("interpretation", "ai_general_reasoning", "additional_literature_search", "evidence_extraction", "experiment_planning",
          "experiment_execution", "evidence_synthesis", "confidence_estimation", "final_report")
MODEL_LIMIT_NOTE = ("The investigation will finish using evidence already collected; "
                    "model-dependent findings may be incomplete.")
WORD = re.compile(r"[a-z][a-z0-9]{2,}")
STOP = {"the", "and", "for", "with", "that", "when", "rather", "than", "under", "from", "this", "will"}


def tokens(text: str) -> set[str]:
    return set(WORD.findall(text.lower())) - STOP


def rank_sources(conjecture: Conjecture, sources) -> list:
    target = tokens(conjecture.normalized_statement)
    return sorted(sources, key=lambda source: (
        len(tokens(source.title) & target) * 3 + len(tokens(source.abstract) & target),
        bool(source.abstract), bool(source.verified)), reverse=True)


def source_keys(source) -> set[str]:
    keys = {"id:" + source.id}
    title = " ".join(WORD.findall(source.title.lower()))
    if len(title) >= 12:
        keys.add("title:" + title)
    if source.doi:
        keys.add("doi:" + source.doi.lower())
    if source.arxiv_id:
        keys.add("arxiv:" + re.sub(r"v\d+$", "", source.arxiv_id.lower()))
    return keys


def deduplicate_sources(sources, attached_ids=(), inspected_ids=()) -> list:
    """Collapse Crossref/arXiv duplicates by DOI or normalized title."""
    by_key, groups = {}, {}
    attached_ids, inspected_ids = set(attached_ids), set(inspected_ids)
    for source in sources:
        keys = source_keys(source)
        matching = {by_key[key] for key in keys if key in by_key}
        group = min(matching) if matching else len(by_key)
        records = [groups[index] for index in sorted(matching)] + [source]
        winner = max(records, key=lambda item: (item.id in attached_ids, item.id in inspected_ids,
                     bool(item.arxiv_id), bool(item.abstract), bool(item.verified)))
        for record in records:
            if not has_article_title(winner) and has_article_title(record):
                winner.title = record.title
            for field in ("doi", "arxiv_id", "abstract", "url", "authors", "year"):
                if not getattr(winner, field) and getattr(record, field):
                    setattr(winner, field, getattr(record, field))
        for index in matching:
            groups.pop(index)
        groups[group] = winner
        for key, index in list(by_key.items()):
            if index in matching:
                by_key[key] = group
        by_key.update({key: group for key in keys | source_keys(winner)})
    return list(groups.values())


def structured_output_error(value, schema, path="output") -> str | None:
    """Validate the small JSON-schema subset used by all pipeline response contracts."""
    expected = schema.get("type")
    checks = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list),
              "string": lambda x: isinstance(x, str), "boolean": lambda x: type(x) is bool,
              "integer": lambda x: type(x) is int,
              "number": lambda x: type(x) is int or (type(x) is float and math.isfinite(x))}
    if expected in checks and not checks[expected](value):
        return f"{path} must be {expected}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} has an unsupported value"
    if expected in ("integer", "number"):
        if ("minimum" in schema and value < schema["minimum"]) or ("maximum" in schema and value > schema["maximum"]):
            return f"{path} is outside its allowed range"
    if expected == "object":
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            return f"{path} omitted {', '.join(missing)}"
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(key not in properties for key in value):
            return f"{path} contains unexpected fields"
        for key in properties.keys() & value.keys():
            error = structured_output_error(value[key], properties[key], f"{path}.{key}")
            if error:
                return error
    if expected == "array":
        for index, item in enumerate(value):
            error = structured_output_error(item, schema.get("items", {}), f"{path}[{index}]")
            if error:
                return error
    return None


def experiment_parameters(text):
    def reject_constant(value):
        raise ValueError(f"invalid JSON number: {value}")
    try:
        return json.loads(text, parse_constant=reject_constant)
    except (ValueError, TypeError):
        return None


class ConjecturePipeline:
    def __init__(self, agent):
        self.agent = agent
        self.state: ResearchState | None = None
        self.config: DeploymentConfig = agent.config
        self._repaired_designs: set[str] = set()
        self._experiment_recovery = False

    def _stage(self, name: str, summary: str, **outputs):
        self.state.record(name, "completed", summary, outputs=deepcopy(outputs))
        self.agent._checkpoint(self.state)

    def _paper_context(self, *, limit_per_source: int = 3) -> list[dict]:
        """Small, source-identified excerpts for interpreting and implementing attached methods."""
        state = self.state
        context = []
        by_id = {source.id: source for source in state.sources}
        source_budget = max(1800, 9000 // max(1, len(state.context_source_ids)))
        for source_id in state.context_source_ids:
            source = by_id.get(source_id)
            if source is None:
                continue
            found = {}
            method_terms = " ".join(sorted(tokens(state.question))[:8])
            for query in (state.question, f"algorithm method update parameters {method_terms}"):
                for chunk, _ in self.agent.chunk_store.search(query, limit_per_source, source_id):
                    found[chunk.id] = chunk
            question_terms = tokens(state.question)
            def algorithm_score(chunk):
                headings = re.findall(r"\balgorithm\s*(\d+)\s*([^\n]{0,100})", chunk.text, re.I)
                if not headings:
                    return 0
                return max(1 + 5 * len(tokens(heading) & question_terms) + (2 if number == "1" else 0)
                           for number, heading in headings)
            algorithm_chunks = [chunk for chunk in self.agent.chunk_store.for_sources([source_id])
                                if algorithm_score(chunk)]
            best_algorithm = None
            if algorithm_chunks:
                best_algorithm = max(algorithm_chunks, key=algorithm_score)
                found[best_algorithm.id] = best_algorithm
            if not found:
                for chunk in self.agent.chunk_store.for_sources([source_id])[:1]:
                    found[chunk.id] = chunk
            ranked = ([best_algorithm] if best_algorithm else []) + [
                chunk for chunk in found.values() if best_algorithm is None or chunk.id != best_algorithm.id]
            remaining = source_budget
            for chunk in ranked[:limit_per_source + 1]:
                if remaining <= 0:
                    break
                excerpt = chunk.text[:min(1800, remaining)]
                remaining -= len(excerpt)
                context.append({"source_id": source_id, "title": source.title,
                                "location": f"{chunk.section}, page {chunk.page}" if chunk.page else chunk.section,
                                "text": excerpt})
        return context

    def _reviewed_paper_context(self) -> list[dict]:
        """Carry inspected top sources and grounded findings into later model calls."""
        state = self.state
        findings = {}
        for item in state.structured_evidence:
            findings.setdefault(item.source_id, []).append(item)
        reviews = {item["source_id"]: item for item in state.literature_reviews}
        reviewed = []
        for source in state.sources:
            items = findings.get(source.id, [])
            review = reviews.get(source.id)
            if source.id not in state.inspected_papers and not items and not review:
                continue
            entry = {"source_id": source.id, "title": source.title,
                     "text_scope": "full_text_passage" if source.id in state.inspected_papers else
                                   "abstract_only" if source.abstract else "metadata_only",
                     "review": review or {"status": "passage_retrieved_unclassified"},
                     "findings": [{"excerpt": item.statement[:500], "location": item.source_location,
                                   "evidence_type": item.evidence_type,
                                   "relation": item.relation_to_conjecture,
                                   "assumptions": item.assumptions[:4], "notes": item.notes[:250]}
                                  for item in items[:2]]}
            if source.id not in state.context_source_ids:
                chunks = self.agent.chunk_store.for_sources([source.id]) if source.id in state.inspected_papers else []
                if chunks:
                    matched = next((chunk for item in items for chunk in chunks
                                    if item.statement and item.statement in chunk.text), None)
                    matches = self.agent.chunk_store.search(state.conjecture.normalized_statement, 1, source.id)
                    chunk = matched or (matches[0][0] if matches else chunks[0])
                    position = max(0, chunk.text.find(items[0].statement) - 200) if matched and items else 0
                    entry["passage"] = {"location": f"{chunk.section}, page {chunk.page}" if chunk.page else chunk.section,
                                        "text": chunk.text[position:position + 1200]}
                elif source.abstract:
                    entry["passage"] = {"location": "abstract; full text not inspected",
                                        "text": source.abstract[:1200]}
            reviewed.append(entry)
        return reviewed

    def _generate(self, stage: str, instruction: str, payload: dict, schema: dict, *, strong: bool = False,
                  web_search: bool = False):
        if self.state.stop_reason == "model_limit":
            return None
        provider = (self.agent.synthesis_provider or self.agent.provider) if strong else self.agent.provider
        if provider is None or isinstance(provider, NoLLMProvider):
            return None
        state = self.state
        instruction += "\n\n" + prompts.output_instructions(stage, schema)
        interpretation = state.conjecture
        if stage == "experiment_planning":
            # Planning needs the interpreted method, not the later literature review.
            payload = {key: value for key, value in payload.items() if key in (
                "max_repetitions", "max_experiment_seconds", "previous_design", "revision_reason")}
            payload.update(
                normalized_statement=interpretation.normalized_statement if interpretation else state.question,
                objects=interpretation.objects if interpretation else [],
                assumptions=interpretation.assumptions if interpretation else [])
        else:
            context = {"question": state.question, "objective": state.objective,
                       "user_constraints": state.constraints[:10],
                       "working_assumptions": state.assumptions[:12],
                       "unresolved_questions": state.unresolved_questions[:12],
                       "literature_queries": state.literature_queries[:6],
                       "general_reasoning": state.general_reasoning,
                       "reasoning_source_ids": state.reasoning_source_ids,
                       "selected_source_ids": state.selected_source_ids,
                       "recorded_failures": state.failed_attempts[-6:],
                       "attached_source_ids": state.context_source_ids,
                       "literature_sources": [{"source_id": source.id, "title": source.title,
                           "doi": source.doi, "arxiv_id": source.arxiv_id, "url": source.url,
                           "abstract": source.abstract[:500], "attached": source.id in state.context_source_ids,
                           "full_text_inspected": source.id in state.inspected_papers}
                           for source in state.sources]}
            if interpretation:
                context["interpretation"] = {
                    "normalized_statement": interpretation.normalized_statement,
                    "objects": interpretation.objects,
                    "measurable_predictions": interpretation.measurable_predictions,
                    "ambiguities": interpretation.ambiguities}
            if state.experiments_planned:
                context["planned_experiments"] = [{"id": item.id, **item.model_context(),
                    "code_available": bool(item.code)} for item in state.experiments_planned[:3]]
                context["executed_experiment_ids"] = [item.id for item in state.experiments_completed[:3]]
            payload = {**payload, "investigation_context": context}
            if stage in ("experiment_code", "experiment_visualization", "experiment_code_repair"):
                payload["svg_contract"] = visualization_svg_contract()
                payload["max_generated_code_chars"] = self.config.max_generated_code_chars if self.config.bounded else 100000
                payload["max_result_json_bytes"] = 100000
                payload["max_experiment_seconds"] = self.config.experiment_timeout_seconds
                payload["max_repetitions"] = self.config.experiment_seed_limit
                instruction += '\nAny visualization script must produce SVG conforming to the supplied "svg_contract".'
            paper_context = self._paper_context()
            if paper_context:
                payload = {**payload, "attached_paper_passages": paper_context}
                instruction += prompts.ATTACHED_PAPER_CONTEXT
            reviewed = self._reviewed_paper_context()
            if reviewed:
                payload = {**payload, "reviewed_papers": reviewed}
                instruction += prompts.REVIEWED_PAPER_CONTEXT
        messages = [{"role": "developer", "content": instruction},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        search_limit = (max(0, self.config.max_tools - state.tool_calls - 4) if self.config.bounded else 4)
        search_options = ({"max_tool_calls": search_limit}
                          if web_search and search_limit and getattr(provider, "supports_web_search", False) else None)
        # Preserve room for the final stages in the bounded deployment.
        reserved_steps = {"source_selection": 6, "evidence_extraction": 5, "experiment_planning": 4,
                          "experiment_code": 3, "experiment_visualization": 3,
                          "experiment_code_repair": 3,
                          "experiment_execution": 2, "evidence_synthesis": 1}
        if (self.config.bounded and stage in reserved_steps
                and not (self._experiment_recovery and stage in ("experiment_planning", "experiment_code")) and
                self.state.agent_steps >= self.config.max_steps - reserved_steps[stage]):
            message = f"{stage}: model call skipped to reserve the remaining deployment budget for later stages."
            if message not in state.unresolved_questions:
                state.unresolved_questions.append(message)
            return None
        if self.config.bounded and (self.state.agent_steps >= self.config.max_steps or
                                    not can_call(self.state, provider, messages, self.config, schema,
                                                 search_options=search_options)):
            self.state.stop_reason = self.state.stop_reason or (
                "step_limit" if self.state.agent_steps >= self.config.max_steps else "budget_limit")
            return None
        try:
            generate = provider.generate
            if stage == "experiment_planning":
                generate = getattr(provider, "generate_plan", generate)
            if stage in ("experiment_code", "experiment_visualization", "experiment_code_repair"):
                generate = getattr(provider, "generate_code", generate)
            result = (provider.generate_with_search(messages, schema, **search_options)
                      if search_options else generate(messages, schema))
        except (ProviderError, KeyError, ValueError) as exc:
            if search_options:
                state.tool_calls += getattr(provider, "last_web_search_calls", 0)
            self.state.agent_steps += 1
            record_call(self.state, provider, stage, status="failed")
            detail = f": {exc}" if isinstance(exc, ProviderError) else ""
            self.state.failed_attempts.append(f"{stage}: {type(exc).__name__}{detail}"[:240])
            if isinstance(exc, ModelLimitError):
                self.state.stop_reason = "model_limit"
                message = f"{exc} {MODEL_LIMIT_NOTE}"
                self.state.unresolved_questions.append(message)
                self.state.record("model_limit", "stopped", message)
                self.agent._checkpoint(self.state)
            return None
        self.state.agent_steps += 1
        if search_options and not getattr(provider, "cache_hit", False):
            state.tool_calls += getattr(provider, "last_web_search_calls", 0)
        record_call(self.state, provider, stage)
        public_result = ({key: value for key, value in result.items()
                          if not search_options or key not in ("_web_search_sources", "_web_search_calls")}
                         if isinstance(result, dict) else result)
        error = structured_output_error(public_result, schema)
        if error:
            state.llm_calls[-1]["status"] = "invalid_output"
            self.state.failed_attempts.append(f"{stage}: invalid structured output: {error}"[:240])
            return None
        if web_search and not search_options:
            result.pop("_web_search_sources", None)
        return result

    def run(self, state: ResearchState):
        self.state = state
        self.interpret()
        self.reason_generally()
        self.search_literature()
        self.extract_evidence()
        self.plan_experiments()
        self.execute_experiments()
        self.synthesize()
        self.assess()
        from .reporting import render_conjecture_report
        state.report = render_conjecture_report(state, self.config.max_output_tokens if self.config.bounded else None)
        self._stage("final_report", "Generated a traceable conjecture assessment.")
        return state

    def interpret(self):
        state = self.state
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {k: {"type": "boolean" if k == "experimentable" else "string" if k in ("normalized_statement", "mathematical_domain") else "array", **({"items": {"type": "string"}} if k not in ("experimentable", "normalized_statement", "mathematical_domain") else {})}
                                 for k in ("normalized_statement", "mathematical_domain", "objects", "assumptions", "measurable_predictions", "ambiguities", "experimentable")},
                  "required": ["normalized_statement", "mathematical_domain", "objects", "assumptions", "measurable_predictions", "ambiguities", "experimentable"]}
        payload = {"statement": state.question}
        result = self._generate("interpretation", prompts.INTERPRETATION,
                                payload, schema)
        if result:
            c = Conjecture(state.question, str(result["normalized_statement"]),
                           str(result["mathematical_domain"]), list(result["objects"]),
                           list(result["assumptions"]), list(result["measurable_predictions"]),
                           list(result["ambiguities"]), bool(result["experimentable"]))
        else:
            text = state.question.strip()
            c = Conjecture(text, text, "mathematics", [], [], [],
                           ["Quantifiers and scope may need clarification"], False, [], [])
        state.conjecture = c
        state.assumptions = list(dict.fromkeys(state.assumptions + c.assumptions))
        state.unresolved_questions = list(dict.fromkeys(state.unresolved_questions + c.ambiguities))
        self._stage("interpretation", "Parsed conjecture and explicit assumptions.", experimentable=c.experimentable)

    def reason_generally(self):
        state, c = self.state, self.state.conjecture
        argument_schema = {"type": "object", "additionalProperties": False,
                           "properties": {"relation": {"type": "string", "enum": ["supports", "contradicts"]},
                                          "observation": {"type": "string"}, "query": {"type": "string"},
                                          "source_ids": {"type": "array", "items": {"type": "string"}}},
                           "required": ["relation", "observation", "query", "source_ids", "source_urls"]}
        argument_schema["properties"]["source_urls"] = {"type": "array", "items": {"type": "string"}}
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"argument_searches": {"type": "array", "items": argument_schema}},
                  "required": ["argument_searches"]}
        result = self._generate("ai_general_reasoning", prompts.AI_GENERAL_REASONING,
            {"conjecture": c.normalized_statement, "assumptions": c.assumptions}, schema, web_search=True)
        state.general_reasoning = []
        state.reasoning_source_ids = []
        registry = self._literature_registry()
        # Keep three additional searches and one passage download available in bounded modes.
        focused_tool_limit = max(0, self.config.max_tools - 4) if self.config.bounded else None
        known_ids = {source.id for source in state.sources}
        discovered = self._register_reasoning_sources(result.get("_web_search_sources", []) if result else [])
        proposed_arguments = result.get("argument_searches", []) if result else []
        for i, item in enumerate((proposed_arguments if isinstance(proposed_arguments, list) else [])[:2]):
            if (isinstance(item, dict) and item.get("relation") in ("supports", "contradicts")
                    and isinstance(item.get("observation"), str) and item["observation"].strip()
                    and isinstance(item.get("query"), str)
                    and len(item["observation"]) <= 500 and len(item["query"]) <= 300):
                supplied_ids = item.get("source_ids", [])
                source_ids = list(dict.fromkeys(sid for sid in supplied_ids
                    if isinstance(sid, str) and sid in known_ids)) if isinstance(supplied_ids, list) else []
                source_urls = item.get("source_urls", [])
                if isinstance(source_urls, list):
                    source_ids = list(dict.fromkeys(source_ids + [discovered[url] for url in source_urls
                                      if isinstance(url, str) and url in discovered]))
                query = item["query"].strip()
                if query:
                    name = "arxiv_search" if i % 2 == 0 else "literature_search"
                    for tool in (name, "literature_search" if name == "arxiv_search" else "arxiv_search"):
                        if focused_tool_limit is not None and state.tool_calls >= focused_tool_limit:
                            break
                        # Locate one corroborating source per observation, leaving room for other evidence.
                        found = self._search_sources(registry, tool, query, limit=1)
                        source_ids.extend(sid for sid in found if sid not in source_ids)
                        if found:
                            break
                observation = {"relation": item["relation"], "observation": item["observation"].strip(),
                               "query": query, "source_ids": source_ids}
                state.general_reasoning.append(observation)
                state.reasoning_source_ids.extend(sid for sid in source_ids if sid not in state.reasoning_source_ids)
                target = (c.supporting_observations if item["relation"] == "supports"
                          else c.contradicting_observations)
                if observation["observation"] not in target:
                    target.append(observation["observation"])
        self._stage("ai_general_reasoning", f"Recorded {len(state.general_reasoning)} unverified observations and {len(state.reasoning_source_ids)} sources to review.",
                    observations=state.general_reasoning, source_ids=state.reasoning_source_ids)

    def _register_reasoning_sources(self, references) -> dict[str, str]:
        """Register API-retrieved web records; model-authored URLs alone cannot create a source."""
        discovered = {}
        for item in references if isinstance(references, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            url = item["url"].strip()
            record = Source("", str(item.get("title") or url)[:500], url=url)
            if not source_url(record):
                continue
            parsed = urlparse(url)
            arxiv = arxiv_url(record)
            if arxiv:
                record.arxiv_id = arxiv.split("/abs/", 1)[1]
                record.id, record.url = f"arxiv:{record.arxiv_id}", arxiv.replace("/abs/", "/pdf/")
            elif parsed.hostname in {"doi.org", "dx.doi.org"} and re.fullmatch(r"10\.\d{4,9}/\S+", unquote(parsed.path.lstrip("/")), re.I):
                record.doi = unquote(parsed.path.lstrip("/")).lower()
                record.id = f"doi:{record.doi}"
            else:
                record.id = "web:" + hashlib.sha256(url.encode()).hexdigest()[:20]
            if not any(source.id == record.id for source in self.state.sources):
                self.state.sources.append(record)
            discovered[url] = record.id
        self.state.candidate_papers += len(set(discovered.values()))
        return discovered

    def _literature_registry(self):
        return default_registry(self.state, self.agent.artifact_root / self.state.id / "generated",
                                chunk_store=self.agent.chunk_store, embedder=self.agent.embedder,
                                config=self.config, cache_root=self.agent.artifact_root.parent / "cache")

    def _search_sources(self, registry, tool: str, query: str, *, limit: int = 8) -> list[str]:
        outcome = registry.call(tool, {"query": query, "limit": limit})
        self.state.tool_calls += 1
        service = "arXiv" if tool == "arxiv_search" else "Crossref"
        self.state.record("literature_search", outcome.status,
                          f"Literature search ({service}): {outcome.summary}",
                          inputs={"tool": tool, "query": query, "limit": limit},
                          outputs={"category": "literature_search", **outcome.data})
        if outcome.status != "completed":
            self.state.failed_attempts.append(outcome.summary[:240])
            self.agent._checkpoint(self.state)
            return []
        self.agent._checkpoint(self.state)
        known_ids = {source.id for source in self.state.sources}
        return list(dict.fromkeys(item["id"] for item in outcome.data.get("sources", [])
                                 if item.get("id") in known_ids))[:limit]

    def search_literature(self):
        state, c = self.state, self.state.conjecture
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
                  "required": ["queries"]}
        result = self._generate("additional_literature_search", prompts.ADDITIONAL_LITERATURE_SEARCH,
            {"conjecture": c.normalized_statement, "assumptions": c.assumptions}, schema)
        queries = result.get("queries") if result else None
        if (not isinstance(queries, list) or len(queries) != 3 or
                any(not isinstance(q, str) or not q.strip() or len(q) > 300 for q in queries) or
                len({q.strip().casefold() for q in queries}) != 3):
            terms = list(dict.fromkeys(word for word in WORD.findall(c.normalized_statement.lower())
                                       if word not in STOP))
            core = " ".join(terms[:7]) or c.normalized_statement[:120]
            queries = [c.normalized_statement[:180], core + " theorem convergence", core + " counterexample limitations"]
        state.literature_queries = list(dict.fromkeys(q.strip()[:300] for q in queries))
        registry = self._literature_registry()
        state.additional_literature_source_ids = []
        for i, query in enumerate(state.literature_queries):
            if self.config.bounded and state.tool_calls >= self.config.max_tools:
                state.stop_reason = "tool_limit"; break
            name = "arxiv_search" if i % 2 == 0 else "literature_search"
            found = self._search_sources(registry, name, query)
            state.additional_literature_source_ids.extend(
                sid for sid in found if sid not in state.additional_literature_source_ids)
        self._deduplicate_literature()
        self.select_sources()
        search_failures = sum(item.startswith(("arxiv_search failed:", "literature_search failed:"))
                              for item in state.failed_attempts)
        self._stage("additional_literature_search", f"Searched {len(state.literature_queries)} targeted queries; found {len(state.additional_literature_source_ids)} additional candidates; {search_failures} literature search failures across the investigation.",
                    queries=state.literature_queries, source_ids=state.additional_literature_source_ids,
                    search_failures=search_failures)

    def _deduplicate_literature(self):
        """Keep provenance pointing at the surviving records after search deduplication."""
        state = self.state
        original = list(state.sources)
        state.sources = rank_sources(state.conjecture, deduplicate_sources(original, state.context_source_ids,
                                                                          state.inspected_papers))
        by_key = {key: source.id for source in state.sources for key in source_keys(source)}
        remapped = {source.id: next((by_key[key] for key in source_keys(source) if key in by_key), None)
                    for source in original}
        def remap(ids):
            return list(dict.fromkeys(remapped[sid] for sid in ids if remapped.get(sid)))
        state.source_aliases = {alias: remapped.get(sid, sid) for alias, sid in state.source_aliases.items()}
        state.source_aliases.update({old: new for old, new in remapped.items() if new and old != new})
        state.context_source_ids = remap(state.context_source_ids)
        state.inspected_papers = remap(state.inspected_papers)
        state.reasoning_source_ids = remap(state.reasoning_source_ids)
        state.additional_literature_source_ids = remap(state.additional_literature_source_ids)
        state.selected_source_ids = remap(state.selected_source_ids)
        for item in state.structured_evidence:
            item.source_id = remapped.get(item.source_id) or item.source_id
        for item in state.literature_reviews:
            item["source_id"] = remapped.get(item["source_id"]) or item["source_id"]
        for item in state.general_reasoning:
            item["source_ids"] = remap(item["source_ids"])

    def select_sources(self):
        state, c = self.state, self.state.conjecture
        context = set(state.context_source_ids)
        automatic = context | set(state.reasoning_source_ids)
        attached = [source.id for source in state.sources if source.id in context]
        automatic_ids = attached + [sid for sid in state.reasoning_source_ids if sid not in context]
        additional = set(state.additional_literature_source_ids) - automatic
        candidates = [source for source in state.sources if source.id in additional][:30]
        selection_schema = {"type": "object", "additionalProperties": False,
                            "properties": {"selected_source_ids": {"type": "array", "items": {"type": "string"}}},
                            "required": ["selected_source_ids"]}
        max_papers = self.config.max_papers if self.config.bounded else 8
        capacity = max(0, max_papers - len(automatic_ids))
        selection = self._generate("source_selection",
            prompts.SOURCE_SELECTION,
            {"conjecture": c.normalized_statement, "capacity": capacity,
             "automatically_selected_source_ids": automatic_ids,
             "candidates": [{"source_id": source.id, "title": source.title,
                              "abstract": source.abstract[:700]}
                            for source in candidates]}, selection_schema) if candidates and capacity else None
        offered_ids = {source.id for source in candidates}
        chosen = []
        if selection and isinstance(selection.get("selected_source_ids"), list):
            chosen = list(dict.fromkeys(source_id for source_id in selection["selected_source_ids"]
                                        if isinstance(source_id, str) and source_id in offered_ids))
        else:
            chosen = [source.id for source in candidates]
        state.selected_source_ids = (automatic_ids + chosen[:capacity])[:max_papers]
        if len(automatic_ids) > max_papers:
            state.unresolved_questions.append("The paper review limit prevented review of all attached and AI reasoning sources.")
        by_id = {source.id: source for source in state.sources}
        state.sources = ([by_id[sid] for sid in state.selected_source_ids] +
                         [source for source in state.sources if source.id not in state.selected_source_ids])
        self._stage("source_selection", f"Selected {len(state.selected_source_ids)} sources, including attached and AI reasoning sources automatically.",
                    automatically_selected_source_ids=[sid for sid in automatic_ids if sid in state.selected_source_ids],
                    selected_source_ids=state.selected_source_ids)

    def extract_evidence(self):
        state = self.state
        attached_context = self._paper_context()
        selection_completed = any(event.action == "source_selection" and event.status == "completed" for event in state.trace)
        selected = ([source for source in state.sources if source.id in state.selected_source_ids]
                    if selection_completed else state.sources[:self.config.max_papers if self.config.bounded else 8])
        registry = default_registry(state, self.agent.artifact_root / state.id / "generated",
                                    chunk_store=self.agent.chunk_store, embedder=self.agent.embedder,
                                    config=self.config, cache_root=self.agent.artifact_root.parent / "cache")
        review_inputs = []
        for source in selected:
            if (not has_article_title(source) and (source.arxiv_id or source.doi)
                    and (not self.config.bounded or state.tool_calls < self.config.max_tools - 1)):
                outcome = registry.call("source_metadata", {"source_id": source.id})
                state.tool_calls += 1
                state.record("source_metadata", outcome.status, outcome.summary,
                             inputs={"source_id": source.id}, outputs=outcome.data,
                             latency_ms=outcome.data.get("elapsed_ms", 0.0))
            if (source.arxiv_id and source.url and not self.agent.chunk_store.for_sources([source.id])
                    and (not self.config.bounded or state.tool_calls < self.config.max_tools)):
                outcome = registry.call("download_paper", {"source_id": source.id, "url": source.url})
                state.tool_calls += 1
                if outcome.status != "completed": state.failed_attempts.append(outcome.summary[:240])
            passages = [chunk for chunk, _ in self.agent.chunk_store.search(
                state.conjecture.normalized_statement, 3, source.id)]
            if passages and source.id not in state.inspected_papers: state.inspected_papers.append(source.id)
            texts = [(p.text[:1400], f"{p.section}, page {p.page}" if p.page else p.section, "full_text")
                     for p in passages]
            if source.id in state.context_source_ids:
                method_passages = [item for item in attached_context if item["source_id"] == source.id
                                   and re.search(r"\balgorithm\s*\d|\bmethod\b|\bupdate\b", item["text"], re.I)]
                if method_passages:
                    method = max(method_passages, key=lambda item: bool(re.search(r"\balgorithm\s*\d", item["text"], re.I)))
                    texts = [(method["text"][:1400], method["location"], "full_text")] + [
                        item for item in texts if item[1] != method["location"]]
            if not texts and source.abstract:
                texts = [(source.abstract[:1400], "abstract; full text not inspected", "abstract")]
            review_inputs.append({"source_id": source.id, "title": source.title,
                "passages": [{"location": location, "text_scope": scope, "text": text}
                             for text, location, scope in texts[:2]]})
        finding_schema = {"type": "object", "additionalProperties": False,
                          "properties": {"source_id": {"type": "string"}, "excerpt": {"type": "string"},
                                         "evidence_type": {"type": "string", "enum": ["theorem", "proof", "empirical_result", "counterexample", "discussion", "related_result"]},
                                         "relation": {"type": "string", "enum": ["supports", "contradicts", "qualifies", "neutral"]},
                                         "assumptions": {"type": "array", "items": {"type": "string"}},
                                         "notes": {"type": "string"}},
                          "required": ["source_id", "excerpt", "evidence_type", "relation", "assumptions", "notes"]}
        review_schema = {"type": "object", "additionalProperties": False,
                         "properties": {"source_id": {"type": "string"},
                                        "relevance": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
                                        "summary": {"type": "string"},
                                        "limitations": {"type": "array", "items": {"type": "string"}}},
                         "required": ["source_id", "relevance", "summary", "limitations"]}
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"reviews": {"type": "array", "items": review_schema},
                                 "findings": {"type": "array", "items": finding_schema}},
                  "required": ["reviews", "findings"]}
        result = self._generate("evidence_extraction",
            prompts.EVIDENCE_EXTRACTION,
            {"conjecture": state.conjecture.normalized_statement, "sources": review_inputs}, schema) if review_inputs else None
        by_id = {source.id: source for source in selected}
        review_by_id = {item["source_id"]: item for item in review_inputs}
        reviewed_ids = set()
        reviews = result.get("reviews", []) if isinstance(result, dict) else []
        for review in reviews if isinstance(reviews, list) else []:
            if (not isinstance(review, dict) or any(key not in review for key in review_schema["required"])
                    or review["source_id"] not in review_by_id or review["source_id"] in reviewed_ids
                    or review["relevance"] not in ("high", "medium", "low", "unknown")
                    or not isinstance(review["summary"], str)
                    or not isinstance(review["limitations"], list)
                    or any(not isinstance(item, str) for item in review["limitations"])):
                continue
            unavailable = not review_by_id[review["source_id"]]["passages"]
            state.literature_reviews = [item for item in state.literature_reviews if item["source_id"] != review["source_id"]]
            state.literature_reviews.append({"source_id": review["source_id"],
                "relevance": "unknown" if unavailable else review["relevance"],
                "summary": "No source text was available for review." if unavailable else review["summary"][:500],
                "limitations": (["No passage or abstract was retrieved; the source cannot establish evidence."]
                                if unavailable else [str(x)[:200] for x in review["limitations"][:4]])})
            reviewed_ids.add(review["source_id"])
        for source_input in review_inputs:
            sid = source_input["source_id"]
            if sid not in reviewed_ids:
                message = ("No passage or abstract was retrieved; the source cannot establish evidence."
                           if not source_input["passages"] else "No valid model review was returned for this source.")
                state.literature_reviews = [item for item in state.literature_reviews if item["source_id"] != sid]
                state.literature_reviews.append({"source_id": sid, "relevance": "unknown",
                    "summary": "Source review unavailable.", "limitations": [message]})
            if not source_input["passages"]:
                message = f"Source {sid} was selected, but no passage or abstract was available to verify its findings."
                if message not in state.unresolved_questions:
                    state.unresolved_questions.append(message)
        per_source = {}
        findings = result.get("findings", []) if isinstance(result, dict) else []
        for finding in findings if isinstance(findings, list) else []:
            if not isinstance(finding, dict) or any(key not in finding for key in finding_schema["required"]):
                continue
            source_id = finding["source_id"]
            if (source_id not in review_by_id or per_source.get(source_id, 0) >= 2
                    or finding["evidence_type"] not in ("theorem", "proof", "empirical_result", "counterexample", "discussion", "related_result")
                    or finding["relation"] not in ("supports", "contradicts", "qualifies", "neutral")
                    or not isinstance(finding["assumptions"], list)
                    or any(not isinstance(item, str) for item in finding["assumptions"])
                    or not isinstance(finding["notes"], str)):
                continue
            source_input = review_by_id[source_id]
            excerpt = finding["excerpt"]
            if not source_input or not isinstance(excerpt, str) or not excerpt.strip():
                continue
            passage = next((item for item in source_input["passages"] if excerpt.strip() in item["text"]), None)
            if passage is None:
                continue
            kind = finding["evidence_type"]
            if passage["text_scope"] == "abstract" and kind in ("theorem", "proof"):
                kind = "related_result"
            strength = 0.75 if kind in ("theorem", "proof", "counterexample") and passage["text_scope"] == "full_text" else 0.35
            state.structured_evidence.append(EvidenceItem(source_id, by_id[source_id].title, excerpt.strip(), kind,
                finding["relation"], passage["location"], list(finding["assumptions"]), strength, finding["notes"]))
            per_source[source_id] = per_source.get(source_id, 0) + 1
        self._stage("evidence_extraction", f"Recorded {len(state.inspected_papers)} full-text inspections and extracted {len(state.structured_evidence)} grounded items.", evidence=len(state.structured_evidence))

    def plan_experiments(self):
        state, c = self.state, self.state.conjecture
        if (not c.experimentable and isinstance(self.agent.provider, NoLLMProvider)
                and not self._paper_context()):
            state.unresolved_questions.append("No small computational experiment was identified that meaningfully tests this claim.")
        else:
            schema = {"type": "object", "additionalProperties": False,
                      "properties": {
                          **{key: {"type": "string"} for key in
                             ("name", "parameter_ranges_json")},
                          **{key: {"type": "array", "items": {"type": "string"}} for key in
                             ("baselines", "metrics", "algorithm_steps", "additional_assumptions")},
                          "seeds": {"type": "array", "items": {"type": "integer"}},
                          "test_type": {"type": "string", "enum": ["falsifying", "illustrative", "not_meaningful"]}},
                      "required": ["name", "baselines", "metrics", "seeds", "algorithm_steps",
                                   "parameter_ranges_json", "additional_assumptions", "test_type"]}
            seed_limit = self.config.experiment_seed_limit
            payload = {"conjecture": c.normalized_statement, "assumptions": c.assumptions,
                       "max_repetitions": seed_limit,
                       "max_experiment_seconds": self.config.experiment_timeout_seconds}
            result = self._generate("experiment_planning", prompts.EXPERIMENT_PLANNING,
                payload, schema)
            if c.experimentable and (not result or result.get("test_type") == "not_meaningful"):
                self._experiment_recovery = True
                result = self._generate("experiment_planning",
                    prompts.EXPERIMENT_PLANNING_RECOVERY,
                    {**payload, "previous_design": result or {},
                     "revision_reason": "No usable finite experiment was proposed."}, schema)
            if result and result["test_type"] == "not_meaningful":
                state.unresolved_questions.append("The planner identified no meaningful finite experiment for this claim.")
            if result and result["test_type"] != "not_meaningful":
                params = experiment_parameters(result["parameter_ranges_json"])
                seeds = result["seeds"]
                list_fields = ("baselines", "metrics", "algorithm_steps", "additional_assumptions")
                valid_lists = all(isinstance(result[key], list) and len(result[key]) <= 30 and
                                  all(isinstance(value, str) and len(value) <= 2000 for value in result[key])
                                  for key in list_fields)
                if (not isinstance(params, dict) or not isinstance(seeds, list) or
                    not 0 <= len(seeds) <= seed_limit or
                    any(type(seed) is not int for seed in seeds) or len(set(seeds)) != len(seeds) or
                    not valid_lists or not result["algorithm_steps"] or not result["metrics"] or
                    not all(isinstance(result[key], str) and result[key].strip()
                            for key in ("name",))):
                    state.failed_attempts.append("experiment_planning: invalid parameters, seeds, metrics, or algorithm steps")
                    self._experiment_recovery = True
                    result = self._generate("experiment_planning",
                        prompts.EXPERIMENT_PLANNING_REVISION,
                        {**payload, "previous_design": result}, schema)
                    if result and result.get("test_type") != "not_meaningful":
                        params = experiment_parameters(result["parameter_ranges_json"])
                        seeds = result["seeds"]
                        valid_lists = all(isinstance(result[key], list) and len(result[key]) <= 30 and
                                          all(isinstance(value, str) and len(value) <= 2000 for value in result[key])
                                          for key in list_fields)
                    if (not result or result.get("test_type") == "not_meaningful"
                            or not isinstance(params, dict) or not isinstance(seeds, list)
                            or not 0 <= len(seeds) <= seed_limit
                            or any(type(seed) is not int for seed in seeds) or len(set(seeds)) != len(seeds)
                            or not valid_lists or not result["algorithm_steps"] or not result["metrics"]
                            or not all(isinstance(result[key], str) and result[key].strip()
                                       for key in ("name",))):
                        state.unresolved_questions.append("The model's experiment design omitted valid parameters, seeds, metrics, or algorithm steps.")
                        self._stage("experiment_planning", f"Planned {len(state.experiments_planned)} controlled experiments.",
                                    plans=len(state.experiments_planned))
                        return
                code = result.get("code", "")
                visualization_code = result.get("visualization_code", "")
                if not code:
                    code_schema = {"type": "object", "additionalProperties": False,
                                   "properties": {"code": {"type": "string"},
                                                  "visualization_code": {"type": "string"}},
                                   "required": ["code", "visualization_code"]}
                    generated = self._generate("experiment_code",
                        prompts.EXPERIMENT_CODE,
                        {"conjecture": c.normalized_statement,
                         "design": {**{key: result[key] for key in schema["required"]},
                                    "inherited_assumptions": c.assumptions}},
                        code_schema)
                    code = generated.get("code", "") if generated else ""
                    visualization_code = generated.get("visualization_code", "") if generated else ""
                code = code if isinstance(code, str) else ""
                if c.experimentable:
                    try:
                        ast.parse(code)
                        usable_code = bool(code and code.strip())
                    except SyntaxError:
                        usable_code = False
                    if not usable_code or len(code) > (self.config.max_generated_code_chars if self.config.bounded else 100000):
                        self._experiment_recovery = True
                        repair = self._generate("experiment_code",
                            prompts.EXPERIMENT_CODE_REVISION,
                            {"conjecture": c.normalized_statement,
                             "design": {**{key: result[key] for key in schema["required"]},
                                        "inherited_assumptions": c.assumptions},
                             "previous_code": str(code)[:4000],
                             "revision_reason": "Previous code was absent, invalid Python, or over the size limit."},
                            code_schema)
                        code = repair.get("code", "") if repair else ""
                        visualization_code = repair.get("visualization_code", "") if repair else ""
                code = str(code)
                visualization_code = visualization_code if isinstance(visualization_code, str) else ""
                if visualization_code:
                    try:
                        ast.parse(visualization_code)
                        if ("result.json" not in visualization_code or
                                len(visualization_code) > (self.config.max_generated_code_chars if self.config.bounded else 100000)):
                            visualization_code = ""
                    except SyntaxError:
                        visualization_code = ""
                if len(code) > (self.config.max_generated_code_chars if self.config.bounded else 100000):
                    code = ""
                design = ExperimentDesign(
                    id=new_id("design"), name=result["name"], hypothesis="",
                    independent_variables=[], dependent_variables=[], controls=[],
                    baselines=result["baselines"], metrics=result["metrics"],
                    parameter_ranges=params, seeds=seeds, expected_behavior="", confounders=[],
                    code=code, test_type=result["test_type"],
                    assumptions=list(dict.fromkeys(c.assumptions + result["additional_assumptions"])),
                    algorithm_steps=result["algorithm_steps"], visualization_code=visualization_code,
                    additional_assumptions=result["additional_assumptions"], planning_schema_version=3)
                if code:
                    from .experiments.code import label_experiment_script
                    design.code = label_experiment_script(design)
                    if len(design.code) > (self.config.max_generated_code_chars if self.config.bounded else 100000):
                        design.code = ""
                    else:
                        try:
                            ast.parse(design.code)
                        except SyntaxError:
                            state.failed_attempts.append("experiment_code: generated Python has invalid syntax")
                            design.code = ""
                if not design.code:
                    state.unresolved_questions.append("Experiment design was saved, but executable code could not be generated.")
                    state.experiments_planned.append(design)
                else:
                    if not design.visualization_code:
                        state.unresolved_questions.append("Experiment code was saved, but visualization code could not be generated.")
                    state.experiments_planned.append(design)
            if not state.experiments_planned:
                state.unresolved_questions.append("No validated small numerical experiment was planned for this conjecture.")
        self._stage("experiment_planning", f"Planned {len(state.experiments_planned)} controlled experiments.", plans=len(state.experiments_planned))

    def _visualization_code(self, design: ExperimentDesign, payload: dict, reason: str = "") -> str:
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"visualization_code": {"type": "string"}},
                  "required": ["visualization_code"]}
        generated = self._generate("experiment_visualization",
            prompts.EXPERIMENT_VISUALIZATION,
            {"design": design.model_context(),
             "result_json": payload, "previous_code": design.visualization_code[:4000],
             "revision_reason": reason}, schema)
        code = generated.get("visualization_code", "") if generated else ""
        if not isinstance(code, str) or "result.json" not in code or len(code) > (
                self.config.max_generated_code_chars if self.config.bounded else 100000):
            return ""
        try:
            ast.parse(code)
        except SyntaxError:
            return ""
        return code

    def execute_experiments(self, design_ids: set[str] | None = None):
        state = self.state
        if not self.config.experiments_allowed:
            message = "Restricted mode permits experiment planning but disables experiment execution."
            for design in state.experiments_planned:
                if design_ids is None or design.id in design_ids:
                    design.execution_status = "skipped"
                    design.execution_error = message
            if message not in state.unresolved_questions:
                state.unresolved_questions.append(message)
            self._stage("experiment_execution", message, executed=0)
            return
        for design in state.experiments_planned[:self.config.max_experiments if self.config.bounded else 3]:
            if design_ids is not None and design.id not in design_ids:
                continue
            if len(design.seeds) > self.config.experiment_seed_limit:
                design.execution_status = "skipped"
                design.execution_error = (
                    f"This design has {len(design.seeds)} seeds; the maximum is {self.config.experiment_seed_limit}. "
                    "Update the design and script before rerunning.")
                state.unresolved_questions.append(design.execution_error)
                continue
            if design.code:
                # Only the configured executor runs user/provider code; production should use Docker.
                if os.getenv("RESEARCHPILOT_EXECUTOR", "local") != "docker":
                    design.execution_status = "skipped"
                    design.execution_error = "Generated experiment code requires the Docker executor; execution was skipped."
                    state.unresolved_questions.append("Generated experiment code requires the Docker executor; execution was skipped.")
                    continue
                completed_before = len(state.experiments_completed)
                execution_unavailable = False
                output = None
                design.execution_error = ""
                try:
                    executor = executor_from_env(self.agent.artifact_root / state.id / "experiments",
                                                 self.config.experiment_timeout_seconds)
                    output = executor.run(design.code, new_id("run"))
                    execution_unavailable = output.status == "unavailable"
                    if output.status != "completed": raise RuntimeError(f"executor {output.status}: {output.stderr[:200]}")
                    result_path = next((Path(p) for p in output.artifacts if Path(p).name == "result.json"), None)
                    if result_path is None: raise RuntimeError("experiment did not emit result.json")
                    if result_path.stat().st_size > 100_000: raise ValueError("experiment result exceeds size limit")
                    payload = json.loads(result_path.read_text(encoding="utf-8"),
                                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON number: {value}")))
                    if not isinstance(payload, dict):
                        raise ValueError("result.json must contain a JSON object")
                    mappings = discover_measurements(payload)
                    try:
                        regimes = (normalize_measurements(payload, mappings,
                                   self.config.experiment_seed_limit)
                                   if mappings is not None else [])
                    except ValueError as exc:
                        if "censored observations cannot be treated as measured stopping times" not in str(exc):
                            raise
                        regimes = []
                        state.unresolved_questions.append(
                            "An experiment reached its stopping cap; censored counts were retained without a numerical stopping-time summary.")
                    relative = regimes[0]["relative_effect"] if len(regimes) == 1 else None
                    metrics = ({"control": regimes[0]["control"], "treatment": regimes[0]["treatment"],
                                "relative_effect": relative} if len(regimes) == 1 else
                               {"regimes": regimes} if regimes else payload)
                    recorded = ExperimentResult(new_id("experiment"), design.id, "completed", design.parameter_ranges,
                        metrics,
                        runtime_seconds=output.runtime_seconds, artifacts=output.artifacts,
                        stdout=output.stdout[:4000], stderr=output.stderr[:4000])
                    state.experiments_completed.append(recorded)
                    design.execution_status = "completed"
                    state.record("experiment_attempt", "completed",
                                 f"Experiment {design.name} completed execution.",
                                 outputs={"category": "experiment_execution", "design_id": design.id,
                                          "experiment_id": recorded.id, "runtime_seconds": output.runtime_seconds})
                    self.agent._checkpoint(state)
                    state.artifacts.extend(path for path in output.artifacts
                                           if Path(path).suffix.lower() in (".svg", ".png", ".jpg", ".jpeg"))
                    state.stochastic_trials += sum(len(item["control"]) for item in regimes)
                    if not design.visualization_code:
                        design.visualization_code = self._visualization_code(design, payload,
                            "The saved experiment has no visualization script.")
                    visualization_path = None
                    visualization_error = "visualization code was unavailable"
                    for attempt in range(2):
                        if not design.visualization_code:
                            break
                        try:
                            visualization = executor.run(design.visualization_code, new_id("visualization"),
                                                 result_json=result_path.read_bytes())
                            if visualization.status != "completed":
                                raise RuntimeError(f"visualization executor {visualization.status}: {visualization.stderr[:200]}")
                            visualization_path = next((Path(path) for path in visualization.artifacts
                                               if Path(path).name == "visualization.svg"), None)
                            if visualization_path is None:
                                raise ValueError("visualization code did not emit visualization.svg")
                            validate_visualization_svg(visualization_path)
                            break
                        except Exception as visualization_exc:
                            visualization_path = None
                            visualization_error = f"{type(visualization_exc).__name__}: {visualization_exc}"[:200]
                            if attempt == 0:
                                design.visualization_code = self._visualization_code(
                                    design, payload, visualization_error)
                    if visualization_path is not None:
                        recorded.artifacts.append(str(visualization_path))
                        state.artifacts.append(str(visualization_path))
                        state.record("experiment_visualization", "completed",
                                     "Generated and recorded a visualization from result.json.",
                                     outputs={"experiment_id": recorded.id, "artifact": str(visualization_path)})
                    else:
                        state.failed_attempts.append(f"visualization: {visualization_error}"[:240])
                        state.unresolved_questions.append(
                            f"Experiment {recorded.id} completed, but its visualization failed.")
                        state.record("experiment_visualization", "failed",
                                     "Visualization code did not produce a valid visualization.",
                                     outputs={"experiment_id": recorded.id})
                    schema = {"type": "object", "additionalProperties": False,
                              "properties": {"finding": {"type": "string"}, "uncertainty": {"type": "string"},
                                             "robustness": {"type": "string"},
                                             "relation": {"type": "string", "enum": ["supports", "contradicts", "inconclusive"]},
                                             "confounders": {"type": "array", "items": {"type": "string"}},
                                             "evidence_paths": {"type": "array", "items": {"type": "string"}}},
                              "required": ["finding", "uncertainty", "robustness", "relation", "confounders", "evidence_paths"]}
                    interpretation = self._generate("experiment_execution",
                        prompts.EXPERIMENT_RESULT_INTERPRETATION,
                         {"conjecture": state.conjecture.normalized_statement, "experiment_id": recorded.id,
                         "design": design.model_context(),
                          "result": metrics, "raw_result": payload}, schema)
                    if (interpretation and interpretation["relation"] in ("supports", "contradicts", "inconclusive")
                            and all(isinstance(interpretation[key], str) for key in ("finding", "uncertainty", "robustness"))
                            and isinstance(interpretation["confounders"], list)
                            and isinstance(interpretation["evidence_paths"], list)):
                        paths = interpretation["evidence_paths"]
                        if (not 1 <= len(paths) <= 10 or not all(isinstance(path, str) and path.startswith("/") for path in paths)):
                            raise ValueError("experiment interpretation needs one to ten result JSON pointers")
                        try:
                            for path in paths:
                                resolve_pointer(payload, path)
                        except (KeyError, IndexError, TypeError, ValueError) as exc:
                            raise ValueError("experiment interpretation cited missing result data") from exc
                        state.experimental_evidence.append(ExperimentalEvidence(
                            recorded.id, interpretation["finding"][:1000], interpretation["relation"], relative,
                            interpretation["uncertainty"][:500], interpretation["robustness"][:500],
                            [],
                            [item[:300] for item in interpretation["confounders"] if isinstance(item, str)][:10],
                            paths))
                    else:
                        state.unresolved_questions.append(
                            f"Experiment {recorded.id} produced measurements, but no model interpretation was available.")
                except Exception as exc:
                    measurements_recorded = len(state.experiments_completed) > completed_before
                    category = "experiment_interpretation" if measurements_recorded else "experiment_execution"
                    prefix = "experiment_interpretation" if measurements_recorded else "experiment"
                    error = f"{type(exc).__name__}: {exc}"
                    state.failed_attempts.append(f"{prefix}: {error}")
                    if len(state.experiments_completed) == completed_before:
                        design.execution_status = output.status if output and output.status != "completed" else "failed"
                        design.execution_error = f"{type(exc).__name__}: {exc}"[:1000]
                    state.record("experiment_interpretation" if measurements_recorded else "experiment_attempt", "failed",
                                 f"Experiment {design.name} {'interpretation' if measurements_recorded else 'execution'} failed: {error[:1000]}",
                                 outputs={"category": category, "design_id": design.id,
                                          "error": error[:1000]})
                    self.agent._checkpoint(state)
                    if len(state.experiments_completed) > completed_before:
                        state.unresolved_questions.append(
                            f"Experiment {state.experiments_completed[-1].id} completed, but its model interpretation could not be validated.")
                    if execution_unavailable:
                        state.unresolved_questions.append(
                            "The configured Docker executor is unavailable; install or start Docker before rerunning the experiment.")
                    if (state.conjecture and state.conjecture.experimentable
                            and len(state.experiments_completed) == completed_before
                            and design.id not in self._repaired_designs
                            and not execution_unavailable
                            and not isinstance(self.agent.provider, NoLLMProvider)):
                        self._repaired_designs.add(design.id)
                        repaired = self._generate("experiment_code_repair",
                            prompts.EXPERIMENT_CODE_RUNTIME_REPAIR,
                            {"conjecture": state.conjecture.normalized_statement,
                             "design": design.model_context(),
                             "previous_code": design.code[:12000],
                             "execution_error": f"{type(exc).__name__}: {exc}"[:500]},
                            {"type": "object", "additionalProperties": False,
                             "properties": {"code": {"type": "string"}}, "required": ["code"]})
                        code = repaired.get("code", "") if repaired else ""
                        if isinstance(code, str) and code.strip():
                            from .experiments.code import label_experiment_script
                            candidate = label_experiment_script(replace(design, code=code))
                            try:
                                ast.parse(candidate)
                            except SyntaxError:
                                candidate = ""
                            if candidate and len(candidate) <= (self.config.max_generated_code_chars if self.config.bounded else 100000):
                                design.code = candidate
                                design.visualization_code = ""
                                self.execute_experiments(design_ids={design.id})
        if (state.conjecture and state.conjecture.experimentable and state.experiments_planned
                and not state.experiments_completed
                and os.getenv("RESEARCHPILOT_EXECUTOR", "local") == "docker"):
            message = "No planned experiment completed; review the recorded execution failures."
            if message not in state.unresolved_questions:
                state.unresolved_questions.append(message)
        executed = sum(x.status == "completed" for x in state.experiments_completed)
        execution_failures = sum(item.startswith("experiment:") for item in state.failed_attempts)
        interpretation_failures = sum(item.startswith(("experiment_interpretation:", "experiment_execution:"))
                                      for item in state.failed_attempts)
        visualization_failures = sum(item.startswith("visualization:") for item in state.failed_attempts)
        self._stage("experiment_execution",
                    f"Executed {executed} experiments; {execution_failures} execution failures, "
                    f"{interpretation_failures} interpretation failures, {visualization_failures} visualization failures.",
                    executed=executed, execution_failures=execution_failures,
                    interpretation_failures=interpretation_failures, visualization_failures=visualization_failures)

    def synthesize(self):
        state = self.state
        items = state.structured_evidence
        assessment = ConjectureAssessment(
            literature_support=[x for x in items if x.relation_to_conjecture == "supports"],
            literature_contradictions=[x for x in items if x.relation_to_conjecture == "contradicts"],
            literature_qualifications=[x for x in items if x.relation_to_conjecture == "qualifies"],
            literature_related=[x for x in items if x.relation_to_conjecture == "neutral"],
            experimental_support=[x for x in state.experimental_evidence if x.relation_to_conjecture == "supports"],
            experimental_contradictions=[x for x in state.experimental_evidence if x.relation_to_conjecture == "contradicts"],
            unresolved_questions=list(state.unresolved_questions), key_assumptions=list(state.assumptions))
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"summary": {"type": "string"}, "revised_conjecture": {"type": "string"},
                                 "unresolved_questions": {"type": "array", "items": {"type": "string"}}},
                  "required": ["summary", "revised_conjecture", "unresolved_questions"]}
        synthesis = self._generate("evidence_synthesis",
            prompts.EVIDENCE_SYNTHESIS,
            {"conjecture": state.conjecture.normalized_statement, "assumptions": state.assumptions,
             **reference_context(state),
             "literature": [asdict(x) for x in items],
             "experiments": [{key: value for key, value in asdict(x).items() if key != "limitations"}
                             for x in state.experimental_evidence],
             "experiment_results": [{"id": x.id, "design_id": x.design_id,
                                     "configuration": x.configuration, "metrics": x.metrics}
                                    for x in state.experiments_completed],
             "unresolved_questions": state.unresolved_questions}, schema)
        if (synthesis and isinstance(synthesis["summary"], str) and
                isinstance(synthesis["revised_conjecture"], str) and
                isinstance(synthesis["unresolved_questions"], list) and
                all(isinstance(x, str) for x in synthesis["unresolved_questions"])):
            assessment.synthesis_summary = readable_references(synthesis["summary"], state)[:2000]
            if synthesis["revised_conjecture"].strip():
                assessment.revised_conjecture = synthesis["revised_conjecture"][:1000]
            assessment.unresolved_questions = list(dict.fromkeys(
                assessment.unresolved_questions + synthesis["unresolved_questions"][:10]))
        state.assessment = assessment
        state.unresolved_questions = list(assessment.unresolved_questions)
        self._stage("evidence_synthesis", "Separated support, contradictions, qualifications, and unresolved questions.")

    def assess(self):
        state = self.state
        items, experiments = state.structured_evidence, state.experimental_evidence
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"judgment": {"type": "string"}, "rationale": {"type": "string"}},
                  "required": ["judgment", "rationale"]}
        prediction = self._generate("confidence_estimation",
            prompts.QUALITATIVE_ASSESSMENT,
            {"conjecture": state.conjecture.normalized_statement, "assumptions": state.assumptions,
             **reference_context(state),
             "literature": [asdict(x) for x in items],
             "experiments": [asdict(x) for x in experiments],
             "experiment_results": [{"id": x.id, "design_id": x.design_id,
                                     "configuration": x.configuration, "metrics": x.metrics}
                                    for x in state.experiments_completed],
             "unresolved_questions": state.assessment.unresolved_questions,
             "synthesis_summary": state.assessment.synthesis_summary}, schema, strong=True)
        # The stage identifier is retained for existing traces and resynthesis jobs.
        state.confidence = 0.0
        state.confidence_factors = None
        state.confidence_method = "unavailable"
        state.confidence_rationale = ""
        if (prediction and all(isinstance(prediction.get(key), str) and prediction[key].strip()
                               for key in ("judgment", "rationale"))):
            state.conjecture_judgment = readable_references(prediction["judgment"].strip(), state)
            state.judgment_rationale = readable_references(prediction["rationale"].strip(), state)
            state.judgment_method = "llm_judgment"
        else:
            state.conjecture_judgment = "Qualitative judgment unavailable"
            state.judgment_method = "unavailable"
            from .assessment_summary import assessment_evidence_summary
            state.judgment_rationale = assessment_evidence_summary(state)
        self._stage("confidence_estimation",
                    f"Qualitative assessment: {state.conjecture_judgment} {state.judgment_rationale}",
                    method=state.judgment_method, model=(self.agent.synthesis_provider or self.agent.provider).name)
