from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from .agent import ResearchAgent
from .agent.routing import providers_from_env
from .deployment import DeploymentConfig
from .evaluation import BenchmarkRunner, evaluate_investigation, load_benchmark
from .storage import ResearchRepository
from .observability import TraceExporter
from .environment import load_workspace_env
from .latex_report import LatexRenderError, LatexUnavailable


def main() -> None:
    parser = argparse.ArgumentParser(prog="researchpilot")
    parser.add_argument("--workspace", default=".researchpilot")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("research", help="plan and execute a research investigation")
    run.add_argument("question")
    run.add_argument("--plan-only", action="store_true")
    run.add_argument("--save-demo", metavar="ZIP", help="save a portable demo ZIP outside the runtime workspace after the run")
    run.add_argument("--provider", choices=("auto", "deterministic", "openai"), default="auto")
    show = sub.add_parser("show")
    show.add_argument("research_id")
    sub.add_parser("list", help="list investigations saved in the local workspace")
    export = sub.add_parser("export-demo", help="save an existing investigation as a portable demo ZIP")
    export.add_argument("research_id")
    export.add_argument("--output", required=True, metavar="ZIP")
    publish = sub.add_parser("publish-demo", help="prepare a saved ZIP for the static website gallery (no deployment)")
    publish.add_argument("archive")
    publish.add_argument("--slug", required=True)
    publish.add_argument("--title", required=True)
    publish.add_argument("--summary", required=True)
    publish.add_argument("--replace", action="store_true", help="replace an existing gallery snapshot after validation")
    evaluate = sub.add_parser("evaluate", help="score a completed investigation for grounding and experiment quality")
    evaluate.add_argument("research_id")
    sub.add_parser("benchmark", help="run historical tool-selection diagnostics (not a pipeline evaluation)")
    images = sub.add_parser("images", help="inspect, prepare, or retire workspace-owned Docker image aliases")
    images.add_argument("action", choices=("inspect", "prepare", "remove"))
    images.add_argument("--image", required=True)
    worker = sub.add_parser("worker", help="run a standalone leased worker against a local workspace")
    worker.add_argument("--once", action="store_true", help="claim at most one available job and exit")
    args = parser.parse_args()
    workspace = Path(args.workspace)
    load_workspace_env(workspace)
    if args.command == "publish-demo":
        from .demo_publish import publish_demo
        try:
            target = publish_demo(args.archive, args.slug, args.title, args.summary, replace=args.replace)
        except (ValueError, OSError, KeyError, zipfile.BadZipFile, LatexRenderError, LatexUnavailable) as exc:
            parser.error(str(exc))
        print(json.dumps({"demo": str(target)}, indent=2))
        return
    if args.command == "worker":
        from .worker import JobWorker, research_agent_factory
        runner = JobWorker(workspace, research_agent_factory(workspace))
        try:
            if args.once: print(json.dumps({"claimed": runner.run_once()}))
            else: runner.run_forever()
        except KeyboardInterrupt:
            runner.stop()
        return
    if args.command == "images":
        from .images import DockerImageManager
        try:
            result = getattr(DockerImageManager(workspace / "images"), args.action)(args.image)
        except (ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
        return
    repository = ResearchRepository(workspace / "researchpilot.db")
    if args.command == "list":
        print(json.dumps(repository.list(), indent=2))
    elif args.command == "research":
        config = DeploymentConfig.from_env()
        provider, synthesis_provider = providers_from_env(config, workspace, choice=args.provider)
        exporter = TraceExporter(workspace / "traces")
        agent = ResearchAgent(repository, workspace / "artifacts", provider=provider, trace_exporter=exporter,
                              synthesis_provider=synthesis_provider, config=config)
        try:
            state = agent.propose(args.question)
            if not args.plan_only:
                state = agent.run(state)
        finally:
            exporter.close()
        report_path = workspace / "projects" / state.id / "report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(state.report or "\n".join(state.plan), encoding="utf-8")
        result = {"id": state.id, "status": state.status, "report": str(report_path)}
        if args.save_demo:
            from .demo_export import save_demo
            try:
                result["demo"] = str(save_demo(state, workspace, args.save_demo))
            except (ValueError, OSError, LatexRenderError, LatexUnavailable) as exc:
                parser.error(f"Investigation saved locally as {state.id}, but demo export failed: {exc}")
        print(json.dumps(result, indent=2))
    elif args.command == "export-demo":
        from .demo_export import save_demo
        from .jobs import DurableJobQueue
        state = repository.get(args.research_id)
        if not state:
            parser.error("research task not found")
        if (workspace / "jobs.sqlite").is_file():
            job = DurableJobQueue(workspace / "jobs.sqlite").get(state.id)
            if job and job.status in ("queued", "running"):
                parser.error("Wait for queued jobs and reruns to finish before exporting")
        try:
            target = save_demo(state, workspace, args.output)
        except (ValueError, OSError, LatexRenderError, LatexUnavailable) as exc:
            parser.error(str(exc))
        print(json.dumps({"id": state.id, "demo": str(target)}, indent=2))
    elif args.command in ("show", "evaluate"):
        state = repository.get(args.research_id)
        if not state:
            parser.error("research task not found")
        print(json.dumps(state.to_dict() if args.command == "show" else evaluate_investigation(state), indent=2))
    else:
        print(json.dumps(BenchmarkRunner().run(load_benchmark()), indent=2))


if __name__ == "__main__":
    main()
