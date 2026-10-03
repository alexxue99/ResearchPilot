from __future__ import annotations

import os
import subprocess
import sys
import time
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from .images import validate_image


@dataclass(slots=True)
class ExecutionResult:
    status: str
    return_code: int | None
    stdout: str
    stderr: str
    runtime_seconds: float
    artifacts: list[str]


class PythonExecutor:
    """Best-effort local isolation. This is not a security boundary; use containers in production."""

    def __init__(self, artifact_root: str | Path, timeout_seconds: float = 20.0) -> None:
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
            raise ValueError("execution timeout must be between 0 and 300 seconds")
        self.timeout_seconds = timeout_seconds

    def run(self, code: str, run_id: str, *, result_json: bytes | None = None) -> ExecutionResult:
        run_dir = (self.artifact_root / run_id).resolve()
        if self.artifact_root not in run_dir.parents:
            raise ValueError("run_id escapes artifact root")
        run_dir.mkdir(parents=True, exist_ok=False)
        script = run_dir / "experiment.py"
        script.write_text(code, encoding="utf-8")
        if result_json is not None:
            if len(result_json) > 100_000:
                raise ValueError("visualization input exceeds result size limit")
            (run_dir / "result.json").write_bytes(result_json)
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8",
               "PYTHONHASHSEED": "0", "RESEARCHPILOT_ARTIFACT_DIR": str(run_dir)}
        started = time.perf_counter()
        try:
            result = subprocess.run([sys.executable, "-I", str(script)], cwd=run_dir, env=env,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=self.timeout_seconds)
            status, code_value, stdout, stderr = ("completed" if result.returncode == 0 else "failed",
                                                   result.returncode, result.stdout, result.stderr)
        except subprocess.TimeoutExpired as exc:
            status, code_value = "timeout", None
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = "execution exceeded timeout"
        artifacts = [str(p) for p in run_dir.rglob("*") if p.is_file() and p != script
                     and run_dir in p.resolve().parents]
        return ExecutionResult(status, code_value, stdout, stderr,
                               time.perf_counter() - started, artifacts)


class DockerPythonExecutor(PythonExecutor):
    """Production-oriented executor using a locked-down disposable Docker container."""

    def __init__(self, artifact_root: str | Path, timeout_seconds: float = 30.0,
                 image: str = "python:3.13-slim", docker_command: str = "docker") -> None:
        super().__init__(artifact_root, timeout_seconds)
        validate_image(image)
        self.image, self.docker_command = image, docker_command

    def run(self, code: str, run_id: str, *, result_json: bytes | None = None) -> ExecutionResult:
        run_dir = (self.artifact_root / run_id).resolve()
        if self.artifact_root not in run_dir.parents: raise ValueError("run_id escapes artifact root")
        run_dir.mkdir(parents=True, exist_ok=False)
        script = run_dir / "experiment.py"; script.write_text(code, encoding="utf-8")
        if result_json is not None:
            if len(result_json) > 100_000:
                raise ValueError("visualization input exceeds result size limit")
            (run_dir / "result.json").write_bytes(result_json)
        user = "65534:65534"
        if os.name == "posix":
            if os.getuid() != 0:
                user = f"{os.getuid()}:{os.getgid()}"
            else:
                run_dir.chmod(0o777)
        container_name = f"researchpilot-{uuid.uuid4().hex}"
        command = [self.docker_command, "run", "--rm", "--pull", "never", "--name", container_name,
                   "--network", "none", "--memory", "512m",
                   "--cpus", "1", "--pids-limit", "128", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--user", user,
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--mount",
                   f"type=bind,source={run_dir},target=/artifacts", "--workdir", "/artifacts",
                   self.image, "python", "-I", "experiment.py"]
        started = time.perf_counter()
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=self.timeout_seconds)
            status, return_code, stdout, stderr = ("completed" if result.returncode == 0 else "failed",
                                                    result.returncode, result.stdout, result.stderr)
        except subprocess.TimeoutExpired as exc:
            status, return_code = "timeout", None
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = "container execution exceeded timeout"
            try:
                cleanup = subprocess.run([self.docker_command, "rm", "--force", container_name],
                                         capture_output=True, text=True, encoding="utf-8",
                                         errors="replace", timeout=10)
                if cleanup.returncode and "No such container" not in cleanup.stderr:
                    stderr += "; container cleanup failed"
            except (OSError, subprocess.TimeoutExpired):
                stderr += "; container cleanup failed"
        except FileNotFoundError as exc:
            status, return_code, stdout, stderr = "unavailable", None, "", f"Docker runtime unavailable: {exc.filename}"
        artifacts = [str(path) for path in run_dir.rglob("*") if path.is_file() and path != script
                     and run_dir in path.resolve().parents]
        return ExecutionResult(status, return_code, stdout, stderr, time.perf_counter() - started, artifacts)


def executor_from_env(artifact_root: str | Path, timeout_seconds: float = 20) -> PythonExecutor:
    backend = os.environ.get("RESEARCHPILOT_EXECUTOR", "local")
    if backend == "local":
        return PythonExecutor(artifact_root, timeout_seconds)
    if backend == "docker":
        return DockerPythonExecutor(artifact_root, timeout_seconds,
            image=os.environ.get("RESEARCHPILOT_DOCKER_IMAGE", "python:3.13-slim"))
    raise ValueError(f"unknown execution backend: {backend}")
