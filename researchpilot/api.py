import json
import os
import time
import base64
import hashlib
import hmac
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from threading import Thread

from .agent import ResearchAgent
from .agent.routing import providers_from_env
from .deployment import DeploymentConfig, DailyQuota
from .downloads import PaperDownloader
from .ingestion import PaperIngestor
from .literature import ArxivClient
from .models import Source, Status
from .rag_store import PersistentChunkStore
from .storage import ResearchRepository
from .jobs import DurableJobQueue, LeaseLost
from .worker import JobWorker, research_agent_factory
from .observability import TraceExporter
from .reporting import render_bounded_report
from .latex_report import LatexRenderError, LatexUnavailable, render_latex_pdf
from .source_links import arxiv_id_from_link
from .environment import load_workspace_env


def create_app(workspace: str | Path | None = None, *, _scoped: bool = False):
    try:
        from fastapi import FastAPI, HTTPException, Query, Request
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, Response, StreamingResponse
        from pydantic import BaseModel, Field
    except ImportError as exc:
        raise RuntimeError("Install ResearchPilot with the 'api' extra to use FastAPI") from exc

    root = Path(workspace or os.environ.get("RESEARCHPILOT_WORKSPACE", ".researchpilot")).resolve()
    load_workspace_env(root)
    identity_file = os.environ.get("RESEARCHPILOT_IDENTITIES_FILE")
    if identity_file and not _scoped:
        from .access import create_multiuser_app
        return create_multiuser_app(root, identity_file)
    repo = ResearchRepository(root / "researchpilot.db")
    config = DeploymentConfig.from_env()
    provider, synthesis_provider = providers_from_env(config, root)
    agent = ResearchAgent(repo, root / "artifacts", provider=provider,
                          trace_exporter=TraceExporter(root / "traces"), config=config,
                          synthesis_provider=synthesis_provider)
    quota = (DailyQuota(root / "quota.sqlite", config.runs_per_day,
                        legacy_path=root / "demo_quota.sqlite") if config.bounded else None)
    chunk_store = PersistentChunkStore(root / "rag.sqlite")
    embedder = agent.embedder
    queue = DurableJobQueue(root / "jobs.sqlite")
    worker_count = int(os.environ.get("RESEARCHPILOT_WORKERS", "4"))
    if not 0 <= worker_count <= 32:
        raise ValueError("RESEARCHPILOT_WORKERS must be between 0 and 32")
    workers = [JobWorker(root, research_agent_factory(root)) for _ in range(worker_count)]

    @asynccontextmanager
    async def lifespan(_app):
        threads = [Thread(target=worker.run_forever, daemon=True) for worker in workers]
        for thread in threads: thread.start()
        yield
        for worker in workers: worker.stop()
        # Active workers retain their heartbeat until their current attempt ends.
        for thread in threads: thread.join(timeout=2)
        if agent.trace_exporter:
            agent.trace_exporter.close()

    app = FastAPI(title="ResearchPilot", version="0.2.0",
                  description="Auditable orchestration for evidence-backed computational research",
                  lifespan=lifespan)
    origins = [value.strip() for value in os.environ.get(
        "RESEARCHPILOT_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if value.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "Authorization"])

    api_token = None if _scoped else os.environ.get("RESEARCHPILOT_API_TOKEN")
    @app.middleware("http")
    async def bearer_auth(request, call_next):
        if api_token and request.method != "OPTIONS" and request.url.path not in ("/health", "/docs", "/openapi.json"):
            supplied = request.headers.get("authorization", "")
            expected = f"Bearer {api_token}"
            if not hmac.compare_digest(supplied, expected):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "valid bearer token required"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)

    class ResearchRequest(BaseModel):
        question: str = Field(min_length=4, max_length=8000)
        objective: str | None = Field(default=None, max_length=8000)
        execute: bool = False

    class Feedback(BaseModel):
        message: str = Field(min_length=1, max_length=4000)

    class ExperimentCodeUpdate(BaseModel):
        code: str = Field(min_length=1, max_length=100000)

    class PaperUpload(BaseModel):
        filename: str = Field(min_length=1, max_length=180)
        content_base64: str = Field(min_length=1, max_length=36_000_000)
        title: str = Field(min_length=1, max_length=1000)
        authors: list[str] = Field(default_factory=list, max_length=100)
        doi: str | None = Field(default=None, max_length=300)
        arxiv_id: str | None = Field(default=None, max_length=100)
        url: str | None = Field(default=None, max_length=2000)

    class ArxivLink(BaseModel):
        url: str = Field(min_length=1, max_length=300)

    def require_state(research_id: str):
        state = repo.get(research_id)
        if not state:
            raise HTTPException(404, "research task not found")
        return state

    def save_edit(state, prior_timestamp):
        try:
            queue.save_idle(repo, state, prior_timestamp)
        except LeaseLost as exc:
            raise HTTPException(409, str(exc)) from exc

    def require_idle(research_id: str):
        state = require_state(research_id)
        job = queue.get(research_id)
        if state.status == Status.RUNNING or (job and job.status in ("queued", "running")):
            raise HTTPException(409, "wait for the current execution to finish")
        return state

    def clear_experiment_outcome(state, design_id: str):
        old = [item for item in state.experiments_completed if item.design_id == design_id]
        old_paths = {path for item in old for path in item.artifacts}
        state.experiments_completed = [item for item in state.experiments_completed if item.design_id != design_id]
        state.experimental_evidence = [item for item in state.experimental_evidence
                                       if item.experiment_id not in {result.id for result in old}]
        state.artifacts = [path for path in state.artifacts if path not in old_paths]
        state.stochastic_trials = max(0, state.stochastic_trials - sum(
            len(item.metrics.get("control", [])) for item in old))
        state.assessment = None
        state.conjecture_judgment = ""
        state.judgment_rationale = ""
        state.judgment_method = "unavailable"
        state.confidence_factors = None
        state.confidence = 0.0
        state.confidence_rationale = ""
        state.confidence_method = "unavailable"
        state.report = ""

    def start_background(research_id: str, request: Request) -> bool:
        state = require_state(research_id)
        if state.status in (Status.NEEDS_INPUT, Status.COMPLETED, Status.CANCELLED):
            return False
        job = queue.get(research_id)
        if job and job.status in ("queued", "running"):
            return False
        subject = quota_subject(request)
        if quota and not quota.take(subject):
            retry_after = 86400 - int(time.time()) % 86400
            raise HTTPException(429, {"message": (f"You have reached the {config.mode} deployment limit of "
                                                       f"{config.runs_per_day} research runs per UTC day. "
                                                       "The quota resets at midnight UTC; no new investigation was started."),
                                      "partial_report": state.report or render_bounded_report(state, config.max_output_tokens),
                                      "research": state.to_dict()},
                                headers={"Retry-After": str(retry_after)})
        started = queue.enqueue(research_id) is not None
        if quota and not started:
            quota.refund(subject)
        return started

    def quota_subject(request: Request) -> str:
        return hashlib.sha256((request.client.host if request.client else "unknown").encode()).hexdigest()

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/session")
    def session():
        return {"subject": "local", "role": "researcher", "authenticated": bool(api_token)}

    @app.get("/deployment/quota")
    def deployment_quota(request: Request):
        if not quota:
            return {"enabled": False, "mode": config.mode, "experiments_allowed": config.experiments_allowed}
        return {"enabled": True, "mode": config.mode, "experiments_allowed": config.experiments_allowed,
                "remaining": quota.remaining(quota_subject(request)),
                "limit": config.runs_per_day,
                "resets_at": (int(time.time() // 86400) + 1) * 86400}

    @app.get("/research")
    def list_research():
        return [] if config.bounded else repo.list()

    @app.post("/research")
    def create_research(request: ResearchRequest, http_request: Request):
        if config.bounded and len(request.question) > 1000:
            raise HTTPException(422, "Limited/Restricted questions must be at most 1000 characters")
        state = agent.propose(request.question, request.objective)
        if request.execute and state.status != Status.NEEDS_INPUT:
            start_background(state.id, http_request)
        return state.to_dict()

    @app.get("/research/{research_id}")
    def get_research(research_id: str):
        return require_state(research_id).to_dict()

    @app.get("/research/{research_id}/report.pdf")
    def download_report_pdf(research_id: str):
        state = require_state(research_id)
        if not state.report.strip():
            raise HTTPException(409, "This investigation has no report to download yet")
        try:
            pdf = render_latex_pdf(state.report, state.question)
        except LatexUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except LatexRenderError as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(pdf, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="researchpilot-report-{state.id}.pdf"',
            "Cache-Control": "no-store",
        })

    @app.get("/research/{research_id}/demo.zip")
    def download_demo(research_id: str):
        from .demo_export import demo_bundle
        state = require_state(research_id)
        job = queue.get(research_id)
        if job and job.status in ("queued", "running"):
            raise HTTPException(409, "Wait for queued jobs and reruns to finish before exporting")
        try:
            payload = demo_bundle(state, root)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return Response(payload, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="researchpilot-demo-{state.id}.zip"',
            "Cache-Control": "no-store",
        })

    @app.post("/research/{research_id}/continue")
    def continue_research(research_id: str, request: Request, background: bool = Query(default=True)):
        state = require_state(research_id)
        if background:
            started = start_background(research_id, request)
            return {"id": research_id, "accepted": started,
                    "status": "running" if started else str(state.status)}
        if not start_background(research_id, request):
            raise HTTPException(409, "research is already queued, completed, or needs input")
        JobWorker(root, research_agent_factory(root)).run_once(research_id)
        return require_state(research_id).to_dict()

    @app.post("/research/{research_id}/cancel")
    def cancel_research(research_id: str):
        require_state(research_id)
        if not queue.cancel_active(repo, research_id):
            raise HTTPException(409, "no active execution to cancel")
        return {"id": research_id, "cancelled": True, "status": str(Status.CANCELLED)}

    @app.get("/research/{research_id}/job")
    def get_job(research_id: str):
        require_state(research_id)
        job = queue.get(research_id)
        if not job: return None
        result = asdict(job)
        result.pop("lease_token", None)
        return result

    @app.post("/research/{research_id}/feedback")
    def feedback(research_id: str, request: Feedback):
        state = require_state(research_id)
        prior_timestamp = state.updated_at
        state.constraints.append(request.message)
        if state.status == Status.NEEDS_INPUT:
            state.status = Status.PLANNED
            state.unresolved_questions.clear()
        state.record("feedback", "received", "Researcher feedback added to task constraints.")
        save_edit(state, prior_timestamp)
        return state.to_dict()

    @app.put("/research/{research_id}/experiments/{design_id}/code")
    def update_experiment_code(research_id: str, design_id: str, request: ExperimentCodeUpdate):
        state = require_idle(research_id)
        design = next((item for item in state.experiments_planned if item.id == design_id), None)
        if design is None:
            raise HTTPException(404, "experiment design not found")
        if not request.code.strip():
            raise HTTPException(422, "experiment code cannot be blank")
        if design.code == request.code:
            return state.to_dict()
        prior_timestamp = state.updated_at
        design.code = request.code
        design.execution_status = "planned"
        design.execution_error = ""
        design.visualization_code = ""
        clear_experiment_outcome(state, design_id)
        state.record("experiment_code_edit", "completed", "Researcher updated experiment code; prior results were invalidated.",
                     outputs={"design_id": design_id})
        save_edit(state, prior_timestamp)
        return state.to_dict()

    @app.post("/research/{research_id}/experiments/{design_id}/rerun")
    def rerun_experiment(research_id: str, design_id: str):
        if not config.experiments_allowed:
            raise HTTPException(403, "Restricted mode disables experiment execution; planning remains available")
        state = require_idle(research_id)
        design = next((item for item in state.experiments_planned if item.id == design_id), None)
        if design is None:
            raise HTTPException(404, "experiment design not found")
        if not design.code.strip():
            raise HTTPException(409, "save executable experiment code before rerunning")
        if os.getenv("RESEARCHPILOT_EXECUTOR", "local") != "docker":
            raise HTTPException(409, "Experiment execution requires the Docker executor")
        prior_timestamp = state.updated_at
        clear_experiment_outcome(state, design_id)
        state.pending_action = "rerun_experiment"
        state.pending_experiment_id = design_id
        state.status = Status.PLANNED
        state.record("experiment_rerun", "queued", "Researcher queued experiment execution and assessment regeneration.",
                     outputs={"design_id": design_id})
        save_edit(state, prior_timestamp)
        if queue.enqueue(research_id) is None:
            raise HTTPException(409, "research is already queued or running")
        return {"id": research_id, "accepted": True, "status": "running"}

    @app.post("/research/{research_id}/resynthesize")
    def resynthesize_research(research_id: str):
        state = require_idle(research_id)
        if not state.conjecture:
            raise HTTPException(409, "complete the investigation before resynthesizing")
        prior_timestamp = state.updated_at
        state.pending_action = "resynthesize"
        state.pending_experiment_id = ""
        state.status = Status.PLANNED
        state.report = ""
        state.record("resynthesis", "queued", "Researcher queued evidence synthesis, qualitative judgment, and report generation.")
        save_edit(state, prior_timestamp)
        if queue.enqueue(research_id) is None:
            raise HTTPException(409, "research is already queued or running")
        return {"id": research_id, "accepted": True, "status": "running"}

    @app.get("/research/{research_id}/events")
    def events(research_id: str, request: Request):
        require_state(research_id)

        def stream():
            sent = 0
            idle_after_terminal = 0
            while True:
                authorized = request.scope.get("researchpilot.stream_authorized")
                if authorized is not None and not authorized():
                    yield 'event: error\ndata: {"message":"stream authorization ended"}\n\n'
                    return
                state = repo.get(research_id)
                if not state:
                    yield "event: error\ndata: {\"message\":\"research task disappeared\"}\n\n"
                    return
                while sent < len(state.trace):
                    event = asdict(state.trace[sent])
                    yield f"id: {event['sequence']}\nevent: trace\ndata: {json.dumps(event)}\n\n"
                    sent += 1
                yield f"event: state\ndata: {json.dumps({'status': str(state.status), 'trace_count': len(state.trace)})}\n\n"
                if state.status in (Status.COMPLETED, Status.FAILED, Status.NEEDS_INPUT, Status.CANCELLED):
                    idle_after_terminal += 1
                    if idle_after_terminal >= 2:
                        return
                time.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/research/{research_id}/papers")
    def ingest_paper(research_id: str, request: PaperUpload):
        state = require_state(research_id)
        prior_timestamp = state.updated_at
        job = queue.get(research_id)
        if job and job.status in ('queued', 'running'):
            raise HTTPException(409, 'wait for execution before attaching papers')
        if config.bounded and len(state.context_source_ids) >= config.max_papers:
            raise HTTPException(429, "demo paper limit reached for this investigation")
        filename = Path(request.filename).name
        if filename != request.filename or Path(filename).suffix.lower() not in (".pdf", ".txt", ".md", ".html", ".htm", ".rst"):
            raise HTTPException(422, "unsupported or unsafe paper filename")
        try:
            content = base64.b64decode(request.content_base64, validate=True)
        except ValueError as exc:
            raise HTTPException(422, "content_base64 is invalid") from exc
        max_bytes = config.max_paper_bytes if config.bounded else 25_000_000
        if len(content) > max_bytes: raise HTTPException(413, f"paper exceeds {max_bytes} byte limit")
        digest = hashlib.sha256(content).hexdigest()
        source_id = f"doi:{request.doi.lower()}" if request.doi else (f"arxiv:{request.arxiv_id}" if request.arxiv_id else f"sha256:{digest}")
        upload_dir = root / "projects" / research_id / "papers" / digest[:16]
        upload_dir.mkdir(parents=True, exist_ok=True)
        paper_path = upload_dir / filename; paper_path.write_bytes(content)
        source = Source(source_id, request.title, request.authors, doi=request.doi,
                        arxiv_id=request.arxiv_id, url=request.url, verified=False)
        try:
            parsed = PaperIngestor(max_pages=config.max_paper_pages if config.bounded else None).ingest(paper_path, source)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if not parsed.chunks:
            raise HTTPException(422, "No readable text was found in this paper")
        chunk_store.add(parsed.chunks, embedder)
        structure_path = upload_dir / "structure.json"
        structure_path.write_text(json.dumps(parsed.math_blocks, indent=2), encoding="utf-8")
        if source.id not in {item.id for item in state.sources}: state.sources.append(source)
        if source.id not in state.context_source_ids: state.context_source_ids.append(source.id)
        state.record("paper_ingestion", "completed", f"Ingested {len(parsed.chunks)} provenance-preserving chunks from {filename}.", count_as_tool=True,
                     outputs={"source_id": source.id, "chunks": len(parsed.chunks), "warnings": parsed.warnings})
        save_edit(state, prior_timestamp)
        return {"source": asdict(source), "chunks": len(parsed.chunks), "pages": parsed.page_count,
                "warnings": parsed.warnings, "math_blocks": parsed.math_blocks}

    @app.post("/research/{research_id}/arxiv")
    def ingest_arxiv(research_id: str, request: ArxivLink):
        state = require_state(research_id)
        prior_timestamp = state.updated_at
        job = queue.get(research_id)
        if job and job.status in ("queued", "running"):
            raise HTTPException(409, "wait for execution before attaching papers")
        try:
            arxiv_id = arxiv_id_from_link(request.url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        source_id = f"arxiv:{arxiv_id}"
        if source_id in state.context_source_ids:
            raise HTTPException(409, "this arXiv paper is already attached")
        if config.bounded and len(state.context_source_ids) >= config.max_papers:
            raise HTTPException(429, "demo paper limit reached for this investigation")
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
        try:
            path = PaperDownloader(root / "cache" / "papers", max_bytes=config.max_paper_bytes if config.bounded else 25_000_000).download(pdf_url)
            source = Source(source_id, f"arXiv paper {arxiv_id}", arxiv_id=arxiv_id, url=pdf_url)
            parsed = PaperIngestor(max_pages=config.max_paper_pages if config.bounded else None).ingest(path, source)
        except (ValueError, TimeoutError, OSError, RuntimeError) as exc:
            raise HTTPException(422, f"Could not import arXiv PDF: {exc}") from exc
        if not parsed.chunks:
            raise HTTPException(422, "No readable text was found in this arXiv PDF")
        try:
            metadata = ArxivClient(root / "cache" / "arxiv_metadata", timeout=4).lookup(arxiv_id)
        except (OSError, TimeoutError, ValueError):
            metadata = None
        if metadata:
            source = metadata
        chunk_store.add(parsed.chunks, embedder)
        if source_id not in {item.id for item in state.sources}: state.sources.append(source)
        state.context_source_ids.append(source_id)
        state.record("paper_ingestion", "completed", f"Imported arXiv paper {arxiv_id} with {len(parsed.chunks)} passages.", count_as_tool=True,
                     outputs={"source_id": source_id, "chunks": len(parsed.chunks), "warnings": parsed.warnings})
        save_edit(state, prior_timestamp)
        return {"source": asdict(source), "chunks": len(parsed.chunks), "pages": parsed.page_count,
                "warnings": parsed.warnings, "math_blocks": parsed.math_blocks}

    @app.get("/research/{research_id}/retrieval")
    def retrieve(research_id: str, q: str = Query(min_length=2, max_length=1000), limit: int = Query(default=5, ge=1, le=20)):
        state = require_state(research_id)
        allowed = ({source.id for source in state.sources} if not config.bounded
                   else set(state.inspected_papers))
        results = [item for source_id in sorted(allowed)
                   for item in chunk_store.hybrid_search(q, embedder, limit, source_id)]
        results = sorted(results, key=lambda item: item[1], reverse=True)[:limit]
        return [{"chunk": asdict(chunk), "score": score, "components": components} for chunk, score, components in results]

    @app.get("/research/{research_id}/artifact/{artifact_index}")
    def artifact(research_id: str, artifact_index: int):
        state = require_state(research_id)
        if artifact_index < 0 or artifact_index >= len(state.artifacts):
            raise HTTPException(404, "artifact not found")
        target = Path(state.artifacts[artifact_index])
        if not target.is_absolute():
            target = Path.cwd() / target
        target = target.resolve()
        artifact_root = (root / "artifacts").resolve()
        if artifact_root not in target.parents or not target.is_file():
            raise HTTPException(404, "artifact unavailable")
        return FileResponse(target)

    for suffix, field in (("sources", "sources"), ("experiments", "experiments_completed"),
                          ("artifacts", "artifacts"), ("trace", "trace")):
        def endpoint(research_id: str, field=field):
            value = getattr(require_state(research_id), field)
            return [item if isinstance(item, str) else asdict(item) for item in value]
        app.add_api_route(f"/research/{{research_id}}/{suffix}", endpoint, methods=["GET"])

    return app
