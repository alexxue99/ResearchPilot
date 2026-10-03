from __future__ import annotations

import ast
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..literature import ArxivClient, CrossrefClient, has_article_title
from ..deployment import DeploymentConfig
from ..models import Evidence, EvidenceKind, ExperimentDesign, ResearchState, new_id
from ..embeddings import embedding_provider_from_env
from ..citations import verify_passage_support
from ..downloads import PaperDownloader
from ..ingestion import PaperIngestor
from ..rag_store import PersistentChunkStore
from ..statistics import summarize
from ..symbolic import SymbolicMath


@dataclass(slots=True)
class ToolOutcome:
    status: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    required: tuple[str, ...]
    handler: Callable[[dict[str, Any]], ToolOutcome]


class ToolRegistry:
    def __init__(self) -> None: self._tools: dict[str, ToolSpec] = {}
    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools: raise ValueError(f"duplicate tool: {spec.name}")
        self._tools[spec.name] = spec
    @property
    def names(self) -> list[str]: return sorted(self._tools)
    def descriptions(self) -> list[dict[str, Any]]:
        return [{"name": spec.name, "description": spec.description, "required": list(spec.required)} for spec in self._tools.values()]
    def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        spec = self._tools.get(name)
        if not spec: return ToolOutcome("unsupported", f"Unsupported tool call: {name}")
        if not isinstance(arguments, dict): return ToolOutcome("invalid", "Tool arguments must be a JSON object.")
        missing = [key for key in spec.required if key not in arguments]
        if missing: return ToolOutcome("invalid", f"Missing required arguments: {', '.join(missing)}")
        try: return spec.handler(arguments)
        except Exception as exc: return ToolOutcome("failed", f"{name} failed: {type(exc).__name__}: {exc}")


def default_registry(state: ResearchState, artifact_root: str | Path, *, chunk_store=None, embedder=None,
                     config: DeploymentConfig | None = None, cache_root: str | Path | None = None) -> ToolRegistry:
    registry = ToolRegistry(); artifact_root = Path(artifact_root)
    config = config or DeploymentConfig()
    cache_root = Path(cache_root) if cache_root else artifact_root
    chunk_store = chunk_store or PersistentChunkStore(artifact_root / "rag.sqlite")
    embedder = embedder or embedding_provider_from_env()

    def retain_sources(sources):
        """Enrich a web-discovered record when scholarly search returns the same identifier."""
        by_id = {source.id: source for source in state.sources}
        for source in sources:
            existing = by_id.get(source.id)
            if existing is None:
                state.sources.append(source)
                by_id[source.id] = source
                continue
            for name in ("abstract", "authors", "year", "doi", "arxiv_id", "url"):
                if not getattr(existing, name) and getattr(source, name):
                    setattr(existing, name, getattr(source, name))
            if not has_article_title(existing) and has_article_title(source):
                existing.title = source.title
            existing.verified = existing.verified or source.verified

    def design(args: dict[str, Any]) -> ToolOutcome:
        definition = ExperimentDesign(new_id("design"), str(args["name"]), str(args["hypothesis"]),
            list(args.get("independent_variables", [])), list(args.get("dependent_variables", [])),
            list(args.get("controls", [])), list(args.get("baselines", [])), list(args.get("metrics", [])),
            dict(args.get("parameters", {})), [int(seed) for seed in args.get("seeds", [])],
            str(args.get("expected_behavior", "")), list(args.get("confounders", [])))
        state.experiments_planned.append(definition)
        return ToolOutcome("completed", f"Stored experiment design {definition.id}.", {"design_id": definition.id})

    def propose_python(args: dict[str, Any]) -> ToolOutcome:
        design = next((item for item in state.experiments_planned if item.id == args["design_id"]), None)
        if design is None:
            return ToolOutcome("invalid", "Experiment design was not found in this investigation.")
        code = args["code"]
        if not isinstance(code, str) or not code.strip() or len(code) > 100_000:
            return ToolOutcome("invalid", "Proposed Python code must be a nonempty string under 100 KB.")
        try:
            ast.parse(code)
        except SyntaxError as exc:
            return ToolOutcome("invalid", f"Proposed Python code has a syntax error on line {exc.lineno}.")
        design.code = code
        return ToolOutcome("proposed", f"Stored unexecuted Python code for {design.id}.", {"design_id": design.id})

    def stats(args: dict[str, Any]) -> ToolOutcome:
        result = summarize([float(value) for value in args["values"]])
        return ToolOutcome("completed", f"Summarized {result['n']} observations.", result)

    def symbolic(args: dict[str, Any]) -> ToolOutcome:
        tool = SymbolicMath(dict(args.get("symbols", {})) or None); operation = args.get("operation", "simplify")
        if operation == "equivalent": result = tool.equivalent(str(args["expression"]), str(args["right_expression"]))
        elif operation == "differentiate": result = tool.differentiate(str(args["expression"]), str(args["variable"]))
        else: result = tool.simplify(str(args["expression"]))
        return ToolOutcome("completed", f"Symbolic {operation} completed; verified={result.verified}.", {"result": result.result, "verified": result.verified, "assumptions": result.assumptions})

    def literature(args: dict[str, Any]) -> ToolOutcome:
        if config.bounded and len(str(args["query"])) > 300:
            return ToolOutcome("invalid", "Search query exceeds the deployment length limit.")
        client = CrossrefClient(cache_root / "crossref_cache", ttl_seconds=config.cache_ttl_seconds,
                                version=config.cache_version)
        sources = client.search(str(args["query"]), min(int(args.get("limit", 5)), 20), args.get("year_from"), args.get("author"))
        if client.last_cache_hit: state.cache_hits += 1
        state.candidate_papers += len(sources)
        retain_sources(sources)
        return ToolOutcome("completed", f"Retrieved {len(sources)} scholarly records.", {"sources": [asdict(source) for source in sources]})

    def arxiv(args: dict[str, Any]) -> ToolOutcome:
        if config.bounded and len(str(args["query"])) > 300:
            return ToolOutcome("invalid", "Search query exceeds the deployment length limit.")
        client = ArxivClient(cache_root / "arxiv_cache", ttl_seconds=config.cache_ttl_seconds,
                             version=config.cache_version)
        sources = client.search(str(args["query"]), min(int(args.get("limit", 10)), 20))
        if client.last_cache_hit: state.cache_hits += 1
        state.candidate_papers += len(sources)
        retain_sources(sources)
        return ToolOutcome("completed", f"Retrieved {len(sources)} arXiv records.",
                           {"sources": [asdict(source) for source in sources]})

    def metadata(args):
        source = next((item for item in state.sources if item.id == args["source_id"]), None)
        if source is None:
            return ToolOutcome("invalid", "Source does not belong to this investigation.")
        from ..source_links import arxiv_url
        arxiv_link = arxiv_url(source)
        if arxiv_link:
            client = ArxivClient(cache_root / "arxiv_cache")
            identifier = arxiv_link.split("/abs/", 1)[1]
            service = "arxiv"
        elif source.doi:
            client = CrossrefClient(cache_root / "crossref")
            identifier = source.doi
            service = "crossref"
        else:
            return ToolOutcome("unavailable", "Article title could not be retrieved.", {"source_id": source.id})
        diagnostics = {"source_id": source.id, "service": service,
                       "request_url": client.lookup_url(identifier), "timeout_seconds": client.timeout}
        started = time.perf_counter()
        try:
            found = client.lookup(identifier)
        except Exception as exc:
            reason = getattr(exc, "reason", exc)
            if not isinstance(reason, BaseException):
                reason = exc.__cause__ or exc
            diagnostics.update(elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                               cache_hit=client.last_cache_hit, error_type=type(exc).__name__,
                               error_message=str(exc)[:500], cause_type=type(reason).__name__,
                               cause_message=str(reason)[:500], http_status=getattr(exc, "code", None))
            summary = (f"source_metadata failed for {source.id} via {service}: "
                       f"{type(exc).__name__}: {exc}; timeout={client.timeout}s, "
                       f"elapsed={diagnostics['elapsed_ms']}ms")
            logging.getLogger(__name__).warning("%s; diagnostics=%s", summary, diagnostics, exc_info=True)
            return ToolOutcome("failed", summary, diagnostics)
        diagnostics.update(elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                           cache_hit=client.last_cache_hit)
        if not found or not has_article_title(found):
            return ToolOutcome("unavailable", "Article title could not be retrieved.", diagnostics)
        source.title = found.title
        return ToolOutcome("completed", "Retrieved article title.", {**diagnostics, "title": source.title})

    def retrieve(args):
        allowed = ({source.id for source in state.sources} if not config.bounded
                   else set(state.inspected_papers))
        source_id = args.get("source_id")
        if source_id and source_id not in allowed:
            raise ValueError("source does not belong to this investigation")
        limit = max(1, min(int(args.get("limit", 5)), 20))
        results = []
        for sid in ([source_id] if source_id else sorted(allowed)):
            results.extend(chunk_store.hybrid_search(str(args["query"]), embedder, limit, sid))
        results.sort(key=lambda item: item[1], reverse=True)
        return ToolOutcome("completed", "Retrieved passages from this investigation.",
            {"passages": [{**asdict(chunk), "score": score} for chunk, score, _ in results[:limit]]})

    def record_evidence(args):
        source_id, chunk_id = str(args["source_id"]), str(args["chunk_id"])
        if source_id not in {source.id for source in state.sources}:
            raise ValueError("source does not belong to this investigation")
        if config.bounded and source_id not in state.inspected_papers:
            return ToolOutcome("limited", "Inspect this paper before recording a passage-supported claim.")
        evidence = Evidence(new_id("evidence"), EvidenceKind.LITERATURE, str(args["claim"]),
                            source_id=source_id, chunk_id=chunk_id)
        passages = chunk_store.get([chunk_id])
        support = verify_passage_support(evidence, passages)
        if not support["supported"]:
            return ToolOutcome("invalid", "The passage does not support the proposed claim.", support)
        evidence.support = passages[0].text
        evidence.support_score = support["score"]
        state.evidence.append(evidence)
        return ToolOutcome("completed", "Stored a passage-supported claim.", {"evidence_id": evidence.id})

    def download(args):
        source = next((s for s in state.sources if s.id == args["source_id"]), None)
        if source is None:
            raise ValueError("source does not belong to this investigation")
        if config.bounded and source.id not in state.inspected_papers and len(state.inspected_papers) >= config.max_papers:
            return ToolOutcome("limited", "Paper inspection limit reached; synthesize from collected evidence.")
        if config.bounded and str(args["url"]) != source.url:
            return ToolOutcome("invalid", "Download URL must match the selected source metadata.")
        marker = cache_root / "parsed" / (hashlib.sha256(source.id.encode()).hexdigest() + ".json")
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
        except (OSError, json.JSONDecodeError):
            metadata = {}
        existing = chunk_store.for_sources([source.id]) if config.bounded else []
        valid_cache = (config.bounded and existing and metadata.get("version") == config.cache_version
                       and metadata.get("url") == source.url and metadata.get("expires", 0) > time.time())
        if valid_cache:
            if source.id not in state.inspected_papers: state.inspected_papers.append(source.id)
            state.cache_hits += 1
            return ToolOutcome("completed", f"Reused {len(existing)} cached paper passages.",
                               {"source_id": source.id, "cache_hit": True})
        path = PaperDownloader(cache_root / "papers",
                               max_bytes=config.max_paper_bytes if config.bounded else 25_000_000).download(str(args["url"]))
        parsed = PaperIngestor(max_pages=config.max_paper_pages if config.bounded else None).ingest(path, source)
        if config.bounded and existing:
            chunk_store.delete_source(source.id)
        chunk_store.add(parsed.chunks, embedder)
        if config.bounded:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"version": config.cache_version, "url": source.url,
                                          "expires": time.time() + config.cache_ttl_seconds}), encoding="utf-8")
        structure_path = path.with_suffix(".structure.json")
        structure_path.write_text(json.dumps(parsed.math_blocks, indent=2), encoding="utf-8")
        state.artifacts.extend([str(path), str(path.with_suffix(".provenance.json")), str(structure_path)])
        if source.id not in state.inspected_papers: state.inspected_papers.append(source.id)
        return ToolOutcome("completed", f"Ingested {len(parsed.chunks)} paper passages.",
                           {"source_id": source.id, "warnings": parsed.warnings, "math_blocks": parsed.math_blocks})

    if not config.bounded:
        registry.register(ToolSpec("design_experiment", "Store a structured hypothesis, variables, controls, baselines, metrics, seeds, and confounders.", ("name", "hypothesis"), design))
        registry.register(ToolSpec("propose_python", "Store runnable Python code for the researcher to review and run manually; no code is executed and no metrics are measured.", ("code", "design_id"), propose_python))
        registry.register(ToolSpec("summarize_statistics", "Compute descriptive statistics and a confidence interval from repeated observations.", ("values",), stats))
        registry.register(ToolSpec("symbolic_math", "Simplify, differentiate, or verify equivalence with explicit symbol assumptions.", ("expression",), symbolic))
    registry.register(ToolSpec("literature_search", "Retrieve real scholarly metadata from Crossref with persistent DOI identifiers.", ("query",), literature))
    registry.register(ToolSpec("arxiv_search", "Retrieve arXiv papers with public PDF URLs and abstracts.", ("query",), arxiv))
    registry.register(ToolSpec("retrieve_passages", "Search ingested papers belonging to this investigation; optional source_id and limit.", ("query",), retrieve))
    registry.register(ToolSpec("source_metadata", "Resolve the article title for an existing DOI or arXiv source.", ("source_id",), metadata))
    registry.register(ToolSpec("record_literature_claim", "Persist a claim only if its cited passage supports it.", ("claim", "source_id", "chunk_id"), record_evidence))
    registry.register(ToolSpec("download_paper", "Download a public PDF from configured repository hosts and ingest its passages.", ("url", "source_id"), download))
    return registry
