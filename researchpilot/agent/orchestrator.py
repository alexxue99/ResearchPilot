from __future__ import annotations

from pathlib import Path

from ..deployment import DeploymentConfig
from ..embeddings import embedding_provider_from_env
from ..models import ResearchState, Status
from ..observability import TraceExporter
from ..rag_store import PersistentChunkStore
from ..storage import ResearchRepository
from .provider import LLMProvider, NoLLMProvider


class ResearchAgent:
    """Checkpointed conjecture workflow using the existing research infrastructure."""

    def __init__(self, repository: ResearchRepository, artifact_root: str | Path,
                 provider: LLMProvider | None = None, trace_exporter: TraceExporter | None = None,
                 chunk_store=None, embedder=None, config: DeploymentConfig | None = None,
                 synthesis_provider: LLMProvider | None = None) -> None:
        self.repository = repository
        self.artifact_root = Path(artifact_root)
        self.provider = provider or NoLLMProvider()
        self.config = config or DeploymentConfig.from_env()
        self.synthesis_provider = synthesis_provider
        self.chunk_store = chunk_store or PersistentChunkStore(self.artifact_root.parent / "rag.sqlite")
        self.embedder = embedder or embedding_provider_from_env()
        self.trace_exporter = trace_exporter

    def _checkpoint(self, state: ResearchState) -> None:
        self.repository.save(state)
        if self.trace_exporter:
            self.trace_exporter.export(state)

    def propose(self, question: str, objective: str | None = None) -> ResearchState:
        from ..conjecture_pipeline import STAGES
        from .planner import material_ambiguities
        state = ResearchState(question=question, objective=objective or question, model=self.provider.name)
        state.plan = ["Initial reasoning" if stage == "ai_general_reasoning" else
                      "Qualitative judgment" if stage == "confidence_estimation" else
                      stage.replace("_", " ").capitalize() for stage in STAGES]
        state.unresolved_questions = material_ambiguities(question)
        state.status = Status.NEEDS_INPUT if state.unresolved_questions else Status.PLANNED
        state.record("plan", "completed", "Created the staged conjecture investigation plan.",
                     outputs={"stages": list(STAGES)})
        self._checkpoint(state)
        return state

    def run(self, state: ResearchState) -> ResearchState:
        from ..conjecture_pipeline import ConjecturePipeline, MODEL_LIMIT_NOTE
        if state.status == Status.NEEDS_INPUT:
            return state
        state.finish_investigation_trace("failed", "Previous investigation attempt ended without completion.")
        if state.stop_reason == "model_limit":
            state.unresolved_questions = [question for question in state.unresolved_questions
                                          if MODEL_LIMIT_NOTE not in question]
        state.stop_reason = None
        state.status = Status.RUNNING
        state.record("investigation", "running", "Conjecture investigation started.")
        self._checkpoint(state)
        pipeline = ConjecturePipeline(self)
        if state.pending_action == "rerun_experiment":
            design_id = state.pending_experiment_id
            pipeline.state = state
            pipeline.execute_experiments(design_ids={design_id})
        if state.pending_action in ("rerun_experiment", "resynthesize"):
            pipeline.state = state
            pipeline.synthesize()
            pipeline.assess()
            from ..reporting import render_conjecture_report
            state.report = render_conjecture_report(state, self.config.max_output_tokens if self.config.bounded else None)
            pipeline._stage("final_report", "Regenerated the conjecture assessment from current evidence.")
        else:
            pipeline.run(state)
        state.pending_action = ""
        state.pending_experiment_id = ""
        state.status = Status.COMPLETED
        state.finish_investigation_trace("completed", "Conjecture investigation completed.")
        self._checkpoint(state)
        return state
