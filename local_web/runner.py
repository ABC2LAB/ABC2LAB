"""기존 Runner CLI의 프로세스·공개 요약만 연결한다. 진단 로직은 소유하지 않는다."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from local_web.artifacts import read_results
from local_web.inputs import MAX_INPUT_BYTES, TOKEN_PATTERN, InputError, InputStore, PreparedInput

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_SUMMARY_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 8192
STOP_GRACE_SECONDS = 3
MAX_RUN_RECORDS = 16
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
PROFILE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
ARTIFACT_FILES = {
    "crawl_result": "collector/crawl_result.json",
    "semantic_analysis": "semantic_analyzer/semantic_analysis.json",
    "graph_query": "access_analyzer/graph_query.json",
    "graph_query_result": "knowledge_graph/graph_query_result.json",
    "vulnerability_candidates": "access_analyzer/vulnerability_candidates.json",
    "test_scenarios": "scenario_generator/test_scenarios.json",
    "safety_decisions": "safety_policy/safety_decisions.json",
    "verification_results": "verifier/verification_results.json",
    "diagnosis_report": "reporter/diagnosis_report.json",
    "evaluation_results": "reporter/evaluation_results.json",
}
STEP_ARTIFACTS = {
    "collector.collect": "crawl_result",
    "semantic_analyzer.analyze": "semantic_analysis",
    "knowledge_graph.ingest": None,
    "access_analyzer.prepare_queries": "graph_query",
    "knowledge_graph.query": "graph_query_result",
    "access_analyzer.analyze": "vulnerability_candidates",
    "scenario_generator.generate": "test_scenarios",
    "safety_policy.evaluate": "safety_decisions",
    "verifier.verify": "verification_results",
    "knowledge_graph.apply_verification": None,
    "reporter.report": "diagnosis_report",
    "reporter.evaluate": "evaluation_results",
}


class RunnerError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RunnerSettings:
    runs_dir: Path | None = None
    semantic_config_path: Path | None = None
    scenario_config_path: Path | None = None
    policy_config_path: Path | None = None
    matching_profile: str = "default-v1"
    project_root: Path = PROJECT_ROOT

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> RunnerSettings:
        def configured_path(name: str) -> Path | None:
            value = values.get(name, "")
            return Path(value) if value else None

        return cls(
            runs_dir=configured_path("LOCAL_WEB_RUNS_PATH"),
            semantic_config_path=configured_path("SEMANTIC_ANALYZER_CONFIG_PATH"),
            scenario_config_path=configured_path("SCENARIO_GENERATOR_CONFIG_PATH"),
            policy_config_path=configured_path("SAFETY_POLICY_CONFIG_PATH"),
            matching_profile=values.get("LOCAL_WEB_MATCHING_PROFILE", "default-v1"),
        )

    def issues(self) -> list[str]:
        issues = []
        if not self.project_root.is_absolute() or not trusted_file(self.project_root / "pipeline.py"):
            issues.append("RUNNER_UNAVAILABLE")
        root = self.runs_dir
        if not writable_run_storage(root):
            issues.append("RUN_STORAGE_UNAVAILABLE")
        for path, code in (
            (self.semantic_config_path, "SEMANTIC_CONFIG_INVALID"),
            (self.scenario_config_path, "SCENARIO_CONFIG_INVALID"),
        ):
            if not actual_model_config(path):
                issues.append(code)
        if self.policy_config_path is None or not trusted_file(self.policy_config_path):
            issues.append("POLICY_CONFIG_UNAVAILABLE")
        if not PROFILE_PATTERN.fullmatch(self.matching_profile):
            issues.append("MATCHING_PROFILE_INVALID")
        return issues


def has_symlink(path: Path) -> bool:
    return any(parent.is_symlink() for parent in (path, *path.parents))


def trusted_directory(path: Path) -> bool:
    try:
        return not has_symlink(path) and path.is_dir()
    except OSError:
        return False


def trusted_file(path: Path) -> bool:
    try:
        return path.is_absolute() and not has_symlink(path) and stat.S_ISREG(path.stat().st_mode) and os.access(path, os.R_OK)
    except OSError:
        return False


def writable_run_storage(path: Path | None) -> bool:
    try:
        return (path is not None and path.is_absolute() and trusted_directory(path)
                and os.access(path, os.W_OK | os.X_OK) and not path.stat().st_mode & 0o022)
    except OSError:
        return False


def actual_model_config(path: Path | None) -> bool:
    # 공개 TOML의 최소 실행 방향만 확인한다. 전체 설정 검증·추론은 모듈 책임이다.
    if path is None or not trusted_file(path):
        return False
    try:
        with path.open("rb") as source:
            body = source.read(MAX_INPUT_BYTES + 1)
        if len(body) > MAX_INPUT_BYTES:
            return False
        llm = tomllib.loads(body.decode("utf-8")).get("llm")
        return (isinstance(llm, dict) and llm.get("provider") == "ollama"
                and isinstance(llm.get("model_id"), str) and bool(llm["model_id"].strip()))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return False


def artifact_path(artifact_type: str) -> str:
    return f"artifacts/iteration-000/{ARTIFACT_FILES[artifact_type]}"


def public_artifacts(run_root: Path) -> list[dict[str, object]]:
    # GT·private·로그·JSON 본문은 읽지 않고 고정 공개 파일의 존재만 확인한다.
    return [{"artifact_type": name, "is_present": trusted_file(run_root / artifact_path(name))}
            for name in ARTIFACT_FILES]


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate summary key")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError("nonfinite summary value")


def safe_summary(body: bytes, run_root: Path, return_code: int) -> dict[str, object]:
    try:
        if len(body) > MAX_SUMMARY_BYTES:
            raise ValueError("summary too large")
        raw = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
        if not isinstance(raw, dict):
            raise ValueError("invalid summary")
        if (raw.get("run_id") != run_root.name or raw.get("run_root") != str(run_root)
                or raw.get("mode") != "development" or type(raw.get("iteration")) is not int
                or raw["iteration"] != 0 or type(raw.get("exit_code")) is not int
                or raw["exit_code"] != return_code or return_code not in {0, 1, 2}):
            raise ValueError("summary identity mismatch")
        steps = raw.get("steps")
        if not isinstance(steps, list) or len(steps) != len(STEP_ARTIFACTS):
            raise ValueError("invalid steps")
        clean_steps = [safe_step(item, name) for item, name in zip(steps, STEP_ARTIFACTS, strict=True)]
        session = raw.get("session_window")
        halted_by = raw.get("halted_by")
        if session not in {"not_opened", "opened", "failed"} or (halted_by is not None and halted_by not in STEP_ARTIFACTS):
            raise ValueError("invalid summary state")
        statuses = {item["status"] for item in clean_steps}
        expected_exit = 2 if statuses & {"failed", "skipped"} else 1 if "partial" in statuses or session == "failed" else 0
        if return_code != expected_exit:
            raise ValueError("summary exit mismatch")
        return {"steps": clean_steps, "session_window": session, "halted_by": halted_by}
    except (UnicodeError, ValueError, TypeError, KeyError, RecursionError):
        raise RunnerError("RUNNER_SUMMARY_INVALID") from None


def safe_step(raw: object, step: str) -> dict[str, object]:
    if not isinstance(raw, dict) or raw.get("step") != step or raw.get("status") not in {"completed", "partial", "failed", "skipped"}:
        raise ValueError("invalid step")
    count = raw.get("error_count")
    if type(count) is not int or count < 0:
        raise ValueError("invalid error count")
    name = STEP_ARTIFACTS[step]
    expected = artifact_path(name) if name else None
    if raw.get("artifact") is not None and raw["artifact"] != expected:
        raise ValueError("invalid artifact")
    digest = raw.get("sha256")
    if digest is not None and (not isinstance(digest, str) or not HASH_PATTERN.fullmatch(digest)):
        raise ValueError("invalid artifact hash")
    # detail/error_codes/control은 임의 문자열을 포함할 수 있어 응답으로 옮기지 않는다.
    return {"step": step, "status": raw["status"], "error_count": count,
            "artifact_type": name if raw.get("artifact") else None}


@dataclass
class RunRecord:
    run_id: str
    owner_token: str = field(repr=False)
    run_root: Path = field(repr=False)
    process: subprocess.Popen[bytes] = field(repr=False)
    started_at: float
    worker: threading.Thread | None = field(default=None, repr=False)
    state: str = "running"
    exit_code: int | None = None
    summary: dict[str, object] | None = None
    issue: str | None = None
    elapsed_seconds: int | None = None
    artifact_hashes: dict[str, str] = field(default_factory=dict, repr=False)


class RunnerStore:
    def __init__(self, settings: RunnerSettings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._records: dict[str, RunRecord] = {}
        self._active: RunRecord | None = None
        self._is_closed = False

    def readiness(self) -> dict[str, object]:
        issues = self.settings.issues()
        with self._lock:
            return {"diagnosis_enabled": not issues and not self._is_closed,
                    "is_busy": self._active is not None, "issues": issues}

    def start(self, inputs: InputStore, authorization: tuple[str, str, str], owner_token: str | None) -> RunRecord:
        with self._lock:
            if self._is_closed or self.settings.issues():
                raise RunnerError("RUNNER_NOT_READY")
            if self._active is not None:
                raise RunnerError("RUNNER_BUSY")
            # 소유권은 같은 브라우저의 다음 진단에도 유지하되 입력 세션과 수명을 분리한다.
            owner = owner_token if owner_token in self._records else secrets.token_hex(32)
            if owner not in self._records and len(self._records) >= MAX_RUN_RECORDS:
                raise RunnerError("RUN_RECORD_LIMIT")
            prepared = inputs.consume(*authorization)
            record = self._launch(prepared, owner)
            self._records[owner] = record
            self._active = record
            worker = threading.Thread(target=self._watch, args=(record, prepared), daemon=True)
            record.worker = worker
            try:
                worker.start()
            except RuntimeError:
                self._stop_process(record)
                prepared.close()
                record.state, record.issue = "failed", "RUNNER_START_FAILED"
                record.exit_code = record.process.returncode
                record.elapsed_seconds = max(0, int(time.monotonic() - record.started_at))
                self._active = None
                raise RunnerError("RUNNER_START_FAILED") from None
            return record

    def _launch(self, prepared: PreparedInput, owner: str) -> RunRecord:
        run_id = "web-" + secrets.token_hex(16)
        run_root = self.settings.runs_dir / run_id
        environment = prepared.make_collector_environment(os.environ)
        # Compose's optional blank means unset; invalid nonempty values belong to Reporter validation.
        if environment.get("REPORTER_EVALUATION_CONFIG_PATH") == "":
            environment.pop("REPORTER_EVALUATION_CONFIG_PATH")
        environment.update({
            "SEMANTIC_ANALYZER_CONFIG_PATH": str(self.settings.semantic_config_path),
            "SCENARIO_GENERATOR_CONFIG_PATH": str(self.settings.scenario_config_path),
            "SAFETY_POLICY_CONFIG_PATH": str(self.settings.policy_config_path),
        })
        arguments = [sys.executable, str(self.settings.project_root / "pipeline.py"),
                     "--mode", "development", "--target-url", prepared.target_url,
                     "--dataset-id", prepared.dataset_id, "--matching-profile", self.settings.matching_profile,
                     "--run-id", run_id, "--runs-dir", str(self.settings.runs_dir),
                     "--project-root", str(self.settings.project_root)]
        try:
            process = subprocess.Popen(arguments, cwd=self.settings.project_root, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       start_new_session=True, umask=0o077)
            # Popen 후에는 자식 환경만 인증값을 보유한다. 부모 계정 참조는 즉시 폐기한다.
            prepared.accounts = ()
            return RunRecord(run_id, owner, run_root, process, time.monotonic())
        except (OSError, ValueError):
            prepared.close()
            logger.error("runner_start_failed")
            raise RunnerError("RUNNER_START_FAILED") from None
        finally:
            environment.clear()

    def _watch(self, record: RunRecord, prepared: PreparedInput) -> None:
        summary, state, issue = None, "failed", None
        hashes = {}
        try:
            body, is_oversized = read_summary(record.process)
            code = record.process.wait()
            if is_oversized:
                raise RunnerError("RUNNER_SUMMARY_TOO_LARGE")
            summary = safe_summary(body, record.run_root, code)
            # 검증한 종료 통지의 hash만 내부에 보관한다. 원문 detail은 전달하지 않는다.
            raw_steps = json.loads(body)["steps"]
            hashes = {name: step["sha256"] for step, name in zip(raw_steps, STEP_ARTIFACTS.values(), strict=True)
                      if name is not None and step.get("artifact") and step.get("sha256")}
            state = {0: "completed", 1: "partial", 2: "failed"}[code]
        except (OSError, RunnerError):
            logger.error("runner_result_unavailable")
            self._stop_process(record)
            issue = "RUNNER_RESULT_UNAVAILABLE"
        finally:
            try:
                prepared.close()
            except OSError:
                logger.error("runner_input_cleanup_failed")
                issue = "INPUT_CLEANUP_FAILED"
            if record.process.stdout is not None:
                record.process.stdout.close()
            with self._lock:
                if record.state != "stopped":
                    record.state, record.summary, record.issue = state, summary, issue
                    record.artifact_hashes = hashes
                record.exit_code = record.process.returncode
                record.elapsed_seconds = max(0, int(time.monotonic() - record.started_at))
                if self._active is record:
                    self._active = None

    def status(self, owner_token: str | None) -> dict[str, object]:
        with self._lock:
            if owner_token is None:
                return {"state": "idle", "is_busy": self._active is not None}
            if not TOKEN_PATTERN.fullmatch(owner_token) or owner_token not in self._records:
                raise RunnerError("RUN_UNAVAILABLE")
            record = self._records[owner_token]
            elapsed = record.elapsed_seconds
            return {"run_id": record.run_id, "mode": "development", "iteration": 0,
                    "state": record.state, "exit_code": record.exit_code,
                    "is_busy": self._active is not None,
                    "elapsed_seconds": elapsed if elapsed is not None else max(0, int(time.monotonic() - record.started_at)),
                    "artifacts": public_artifacts(record.run_root),
                    "summary": record.summary, "issue": record.issue}

    def results(self, owner_token: str | None) -> dict[str, object]:
        with self._lock:
            if owner_token is None:
                return {"state": "idle", "artifacts": []}
            if not TOKEN_PATTERN.fullmatch(owner_token) or owner_token not in self._records:
                raise RunnerError("RUN_UNAVAILABLE")
            record = self._records[owner_token]
            run_root, hashes = record.run_root, dict(record.artifact_hashes)
            run_id, state = record.run_id, record.state
        # 큰 JSON 읽기 중에도 실행 상태·종료 처리의 lock은 잡지 않는다.
        return {"run_id": run_id, "state": state, "artifacts": read_results(run_root, hashes)}

    def _stop_process(self, record: RunRecord) -> None:
        # 새 process group의 소유 자식만 대상으로 Playwright 자손도 정리한다.
        try:
            os.killpg(record.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            record.process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning("runner_stop_escalated")
        # 리더가 먼저 종료해도 자손이 남을 수 있으므로 소유 group에만 한정한다.
        try:
            os.killpg(record.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        record.process.wait()

    def close(self) -> None:
        with self._lock:
            self._is_closed = True
            record = self._active
            if record is not None:
                record.state, record.issue = "stopped", "WEB_SHUTDOWN"
        if record is not None:
            self._stop_process(record)
            if record.worker is not None:
                record.worker.join(timeout=STOP_GRACE_SECONDS + 1)


def read_summary(process: subprocess.Popen[bytes]) -> tuple[bytes, bool]:
    if process.stdout is None:
        raise RunnerError("RUNNER_SUMMARY_INVALID")
    body = bytearray()
    is_oversized = False
    while chunk := process.stdout.read(READ_CHUNK_BYTES):
        if len(body) + len(chunk) <= MAX_SUMMARY_BYTES and not is_oversized:
            body.extend(chunk)
        else:
            is_oversized = True
    return bytes(body), is_oversized
