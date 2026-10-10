"""ABC2LAB 루트 런너: 8개 모듈의 공개 entrypoint를 순서대로 부르고 경로·실행 인자만 잇는다(명세 03).

모듈 내부(service·utils·schemas)는 import하지 않는다. 산출물 JSON 본문은 열지 않고 반환값(제어 응답)·파일 존재·
파일 바이트 SHA-256만 본다. 비밀값(.env·NEO4J_*·계정 값)은 읽지도 넘기지도 않는다. 모듈 설정은 각 모듈이
자기 설정 파일·환경변수에서 읽는다.

    .venv/bin/python pipeline.py --mode development --target-url <대상 URL> --dataset-id <id> --matching-profile <profile>
    .venv/bin/python pipeline.py --mode diagnosis --target-url <대상 URL>

stdout은 요약 JSON 한 줄, 로그는 stderr. 종료 코드: 0 전부 completed / 1 partial 또는 세션 창구 열기 실패 /
2 failed·skipped가 있음, CLI 오류, run_root가 이미 있음.
1차 구현은 iteration 0 한 회차만 돈다.
"""

import argparse
import hashlib
import importlib
import json
import logging
import re
import secrets
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("pipeline")

MODULE_IDS = (
    "collector",
    "semantic_analyzer",
    "knowledge_graph",
    "access_analyzer",
    "scenario_generator",
    "safety_policy",
    "verifier",
    "reporter",
)
ENTRYPOINT_MODULE_FORMAT = "modules.{}.entrypoint"

MODE_DIAGNOSIS = "diagnosis"
MODE_DEVELOPMENT = "development"
MODES = (MODE_DIAGNOSIS, MODE_DEVELOPMENT)

STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
MODULE_STATUSES = frozenset({STATUS_COMPLETED, STATUS_PARTIAL, STATUS_FAILED})

SESSION_WINDOW_NOT_OPENED = "not_opened"
SESSION_WINDOW_OPENED = "opened"
SESSION_WINDOW_FAILED = "failed"

EXIT_COMPLETED = 0
EXIT_PARTIAL = 1
EXIT_FAILED = 2

DEFAULT_RUNS_DIR = Path("runs")
PROJECT_ROOT = Path(__file__).resolve().parent
ARTIFACTS_DIR_NAME = "artifacts"
ITERATION_DIR_FORMAT = "iteration-{:03d}"
FIRST_ITERATION = 0
GROUND_TRUTH_RELATIVE_FORMAT = "datasets/{}/ground_truth.json"
# 폴더 이름으로 쓰이므로 경로 구분자·..가 들어갈 수 없는 모양만 받는다(모듈들의 run_id 규칙과 같다).
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RUN_ID_TIME_FORMAT = "%Y%m%d-%H%M%S"
RUN_ID_RANDOM_BYTES = 2
# 요약에 옮기는 제어 응답 값. 업무 JSON 필드가 아니라 모듈 반환값이다.
CONTROL_KEYS = ("graph_id", "graph_revision", "previous_graph_revision", "is_ready", "is_applied", "report_path")

REPORT_INPUT_TYPES = ("vulnerability_candidates", "test_scenarios", "safety_decisions", "verification_results")
EVALUATION_EXTRA_INPUT_TYPES = ("crawl_result", "semantic_analysis", "graph_query_result")


@dataclass(frozen=True)
class ArtifactSpec:
    producer: str
    file_name: str


# 명세 02 파일별 단일 작성자. 경로는 runs/<run_id>/artifacts/iteration-<NNN>/<producer>/<file_name>.
ARTIFACT_SPECS = {
    "crawl_result": ArtifactSpec("collector", "crawl_result.json"),
    "semantic_analysis": ArtifactSpec("semantic_analyzer", "semantic_analysis.json"),
    "graph_query": ArtifactSpec("access_analyzer", "graph_query.json"),
    "graph_query_result": ArtifactSpec("knowledge_graph", "graph_query_result.json"),
    "vulnerability_candidates": ArtifactSpec("access_analyzer", "vulnerability_candidates.json"),
    "test_scenarios": ArtifactSpec("scenario_generator", "test_scenarios.json"),
    "safety_decisions": ArtifactSpec("safety_policy", "safety_decisions.json"),
    "verification_results": ArtifactSpec("verifier", "verification_results.json"),
    "diagnosis_report": ArtifactSpec("reporter", "diagnosis_report.json"),
    "evaluation_results": ArtifactSpec("reporter", "evaluation_results.json"),
}


class StepError(RuntimeError):
    """단계를 진행할 수 없다(모듈 예외·잘못된 반환값·GT 없음). 메시지에는 비밀값을 넣지 않는다."""


@dataclass(frozen=True)
class PipelineOptions:
    mode: str
    target_url: str
    run_id: str
    runs_dir: Path
    project_root: Path
    dataset_id: str | None
    matching_profile: str | None


@dataclass(frozen=True)
class RunLayout:
    """runs/<run_id>/artifacts/iteration-<NNN>/<producer>/ 규칙(명세 02·03). run_root는 절대 경로."""

    run_root: Path
    iteration: int

    def output_dir(self, producer: str) -> Path:
        return self.run_root / ARTIFACTS_DIR_NAME / ITERATION_DIR_FORMAT.format(self.iteration) / producer

    def relative_path(self, artifact_type: str) -> str:
        spec = ARTIFACT_SPECS[artifact_type]
        return f"{ARTIFACTS_DIR_NAME}/{ITERATION_DIR_FORMAT.format(self.iteration)}/{spec.producer}/{spec.file_name}"

    def absolute_path(self, artifact_type: str) -> Path:
        return self.run_root / self.relative_path(artifact_type)


@dataclass(frozen=True)
class ArtifactRecord:
    relative_path: str
    sha256: str


@dataclass
class StepOutcome:
    step: str
    status: str
    artifact: str | None = None
    sha256: str | None = None
    error_codes: list[str] = field(default_factory=list)
    error_count: int = 0
    # 런너가 붙인 사유(모듈 예외 타입·경로 불일치·앞 단계 실패 등). 비밀값은 넣지 않는다.
    detail: str | None = None
    control: dict[str, Any] = field(default_factory=dict)

    def as_failed(self, detail: str) -> "StepOutcome":
        return replace(self, status=STATUS_FAILED, detail=detail)


@dataclass(frozen=True)
class StepSpec:
    step_id: str
    run: Callable[[str], StepOutcome]
    # False면 실패해도 다음 단계를 계속한다(다음 단계가 이 단계의 출력을 입력으로 쓰지 않을 때만).
    halts_on_failure: bool = True


@dataclass(frozen=True)
class PipelineResult:
    summary: dict[str, Any]
    exit_code: int


class PipelineRun:
    """한 번의 실행 상태: 단계 결과, 다음 단계로 넘길 산출물 경로·sha256, KG 제어값, 세션 창구 상태."""

    def __init__(self, options: PipelineOptions, layout: RunLayout, entrypoints: Mapping[str, Any]) -> None:
        self._options = options
        self._layout = layout
        self._entrypoints = entrypoints
        self._artifacts: dict[str, ArtifactRecord] = {}
        self._graph_id: str | None = None
        self._ingest_revision: int | None = None
        self._query_revision: int | None = None
        self.outcomes: list[StepOutcome] = []
        self.session_window = SESSION_WINDOW_NOT_OPENED
        self.session_window_error: str | None = None
        self.halted_by: str | None = None

    def execute(self) -> None:
        for step in self._plan():
            if self.halted_by is not None:
                # 실패를 빈 결과로 숨기지 않는다: 부르지 않은 단계는 skipped로 남긴다.
                self.outcomes.append(StepOutcome(step.step_id, STATUS_SKIPPED, detail=f"앞 단계 실패: {self.halted_by}"))
                continue
            outcome = self._run_step(step)
            self.outcomes.append(outcome)
            if outcome.status == STATUS_FAILED and step.halts_on_failure:
                self.halted_by = step.step_id

    def _plan(self) -> list[StepSpec]:
        return [*self._setup_steps(), *self._analysis_cycle_steps()]

    def _setup_steps(self) -> list[StepSpec]:
        """수집·의미 분석·KG 적재. 실행당 한 번."""
        return [
            StepSpec("collector.collect", self._collect),
            StepSpec("semantic_analyzer.analyze", self._analyze_semantics),
            StepSpec("knowledge_graph.ingest", self._ingest),
        ]

    def _analysis_cycle_steps(self) -> list[StepSpec]:
        """질의부터 리포트까지 한 회차. 다회차는 이 목록을 갱신된 graph_revision으로 다시 도는 것(아직 iteration 0만)."""
        steps = [
            StepSpec("access_analyzer.prepare_queries", self._prepare_queries),
            StepSpec("knowledge_graph.query", self._query),
            StepSpec("access_analyzer.analyze", self._analyze_access),
            StepSpec("scenario_generator.generate", self._generate),
            StepSpec("safety_policy.evaluate", self._call_safety_policy),
            StepSpec("verifier.verify", self._verify),
            # 실패해도 계속: 뒤 단계(report·evaluate)가 이 단계의 결과를 입력으로 쓰지 않음. 다음 회차만 막는다.
            StepSpec("knowledge_graph.apply_verification", self._apply_verification, halts_on_failure=False),
            # 실패해도 계속: 뒤 단계(evaluate)가 이 산출물(diagnosis_report)을 입력으로 쓰지 않음.
            StepSpec("reporter.report", self._report, halts_on_failure=False),
        ]
        if self._options.mode == MODE_DEVELOPMENT:
            steps.append(StepSpec("reporter.evaluate", self._evaluate_report))
        return steps

    def _run_step(self, step: StepSpec) -> StepOutcome:
        try:
            outcome = step.run(step.step_id)
        except StepError as error:
            outcome = StepOutcome(step.step_id, STATUS_FAILED, detail=str(error))
        logger.info(
            "%s: %s (오류 %d건)%s",
            outcome.step,
            outcome.status,
            outcome.error_count,
            f" — {outcome.detail}" if outcome.detail else "",
        )
        return outcome

    # ---- 단계 ----

    def _collect(self, step_id: str) -> StepOutcome:
        response = self._call("collector", "collect", [], self._base_context())
        return self._artifact_outcome(step_id, response, "crawl_result")

    def _analyze_semantics(self, step_id: str) -> StepOutcome:
        # 상대 경로는 모듈이 현재 폴더 기준으로 풀므로 절대 경로로 넘긴다.
        input_paths = {"crawl_result": self._absolute("crawl_result")}
        response = self._call("semantic_analyzer", "analyze", input_paths, self._base_context())
        return self._artifact_outcome(step_id, response, "semantic_analysis")

    def _ingest(self, step_id: str) -> StepOutcome:
        input_paths = {"semantic_analysis": self._descriptor("semantic_analysis")}
        response = self._call("knowledge_graph", "ingest", input_paths, self._base_context())
        outcome = _outcome_from_response(step_id, response)
        if outcome.status == STATUS_FAILED:
            return outcome
        if response.get("is_ready") is not True:
            return outcome.as_failed("KG가 준비되지 않음(is_ready)")
        graph_id, revision = response.get("graph_id"), response.get("graph_revision")
        if not _is_graph_id(graph_id) or not _is_revision(revision):
            return outcome.as_failed("ingest 응답에 graph_id·graph_revision이 없음")
        self._graph_id, self._ingest_revision = graph_id, revision
        return outcome

    def _prepare_queries(self, step_id: str) -> StepOutcome:
        context = self._graph_context(self._ingest_revision)
        response = self._call("access_analyzer", "prepare_queries", [], context)
        return self._artifact_outcome(step_id, response, "graph_query")

    def _query(self, step_id: str) -> StepOutcome:
        input_paths = {"graph_query": self._descriptor("graph_query")}
        response = self._call("knowledge_graph", "query", input_paths, self._base_context())
        outcome = self._artifact_outcome(step_id, response, "graph_query_result")
        if outcome.status == STATUS_FAILED:
            return outcome
        if response.get("graph_id") != self._graph_id:
            return outcome.as_failed("조회한 graph_id가 ingest graph_id와 다름")
        revision = response.get("graph_revision")
        if not _is_revision(revision):
            return outcome.as_failed("query 응답에 graph_revision이 없음")
        self._query_revision = revision
        return outcome

    def _analyze_access(self, step_id: str) -> StepOutcome:
        input_paths = [self._absolute("graph_query_result")]
        response = self._call("access_analyzer", "analyze", input_paths, self._graph_context(self._query_revision))
        return self._artifact_outcome(step_id, response, "vulnerability_candidates")

    def _generate(self, step_id: str) -> StepOutcome:
        input_types = ("vulnerability_candidates", "crawl_result")
        input_paths = {name: self._absolute(name) for name in input_types}
        expected_sha256 = {name: self._artifacts[name].sha256 for name in input_types}
        # drafter는 넘기지 않는다. scenario_generator가 자기 설정(SCENARIO_GENERATOR_CONFIG_PATH)으로 만든다.
        context = {**self._base_context(), "expected_sha256": expected_sha256}
        response = self._call("scenario_generator", "generate", input_paths, context)
        return self._artifact_outcome(step_id, response, "test_scenarios")

    def _call_safety_policy(self, step_id: str) -> StepOutcome:
        # #58로 확정: context는 run_id·iteration·mode·run_root 4개, Policy 설정은 safety_policy가 SAFETY_POLICY_CONFIG_PATH로 준비
        input_paths = {"test_scenarios": self._descriptor("test_scenarios")}
        response = self._call("safety_policy", "evaluate", input_paths, self._base_context())
        return self._artifact_outcome(step_id, response, "safety_decisions")

    def _verify(self, step_id: str) -> StepOutcome:
        input_paths = [self._absolute(name) for name in ("test_scenarios", "safety_decisions", "crawl_result")]
        context = {**self._base_context(), "source_graph_revision": self._query_revision}
        # 창구는 verify 직전에 열고 verify가 끝나거나 예외가 나면 닫는다. 세션 객체는 context가 아니라 인자로 넘긴다.
        with ExitStack() as stack:
            session_executor = self._open_session_window(stack)
            response = self._call("verifier", "verify", input_paths, context, session_executor=session_executor)
        return self._artifact_outcome(step_id, response, "verification_results")

    def _apply_verification(self, step_id: str) -> StepOutcome:
        input_paths = {"verification_results": self._descriptor("verification_results")}
        context = {**self._base_context(), "graph_id": self._graph_id}
        response = self._call("knowledge_graph", "apply_verification", input_paths, context)
        return _outcome_from_response(step_id, response)

    def _report(self, step_id: str) -> StepOutcome:
        input_paths = {name: self._descriptor(name) for name in REPORT_INPUT_TYPES}
        response = self._call("reporter", "report", input_paths, self._report_context())
        return self._artifact_outcome(step_id, response, "diagnosis_report")

    def _evaluate_report(self, step_id: str) -> StepOutcome:
        input_paths = {name: self._descriptor(name) for name in (*REPORT_INPUT_TYPES, *EVALUATION_EXTRA_INPUT_TYPES)}
        input_paths["ground_truth"] = self._ground_truth_descriptor()
        context = {
            **self._report_context(),
            "project_root": str(self._options.project_root),
            "matching_profile": self._options.matching_profile,
        }
        response = self._call("reporter", "evaluate", input_paths, context)
        return self._artifact_outcome(step_id, response, "evaluation_results")

    # ---- 호출·확인 ----

    def _call(
        self, module_id: str, operation: str, input_paths: Any, context: Mapping[str, Any], **keyword_arguments: Any
    ) -> Mapping[str, Any]:
        entrypoint = self._entrypoints[module_id]
        output_dir = str(self._layout.output_dir(module_id))
        try:
            response = entrypoint.run(operation, input_paths, output_dir, context, **keyword_arguments)
        except Exception as error:
            # 결과 객체로만 알린다는 모듈도 내부 예외가 새어 나올 수 있다. 삼키지 않고 단계 실패로 기록한다.
            logger.error("%s.%s 예외: %s: %s", module_id, operation, type(error).__name__, error)
            raise StepError(f"모듈 예외: {type(error).__name__}") from error
        if not isinstance(response, Mapping):
            raise StepError("모듈 반환값이 dict가 아님")
        return response

    def _open_session_window(self, stack: ExitStack) -> Any | None:
        """collector 세션 창구를 연다. 못 열면 None으로 verify를 부른다(verifier가 판단불가로 기록)."""
        try:
            session_executor = stack.enter_context(self._entrypoints["collector"].open_session_executor())
        except (ValueError, RuntimeError, OSError) as error:
            logger.warning("세션 창구를 열지 못함: %s: %s", type(error).__name__, error)
            self.session_window = SESSION_WINDOW_FAILED
            self.session_window_error = type(error).__name__
            return None
        self.session_window = SESSION_WINDOW_OPENED
        return session_executor

    def _artifact_outcome(self, step_id: str, response: Mapping[str, Any], artifact_type: str) -> StepOutcome:
        outcome = _outcome_from_response(step_id, response)
        # 모듈마다 경로 키 이름(artifact_path·output_path)과 절대/상대가 다르다.
        reported_path = response.get("artifact_path", response.get("output_path"))
        if reported_path is None:
            return outcome if outcome.status == STATUS_FAILED else outcome.as_failed("결과 파일 경로가 없음")
        problem = self._check_artifact(artifact_type, reported_path, response.get("sha256"))
        if problem is not None:
            return outcome.as_failed(problem)
        record = ArtifactRecord(self._layout.relative_path(artifact_type), response["sha256"])
        if outcome.status != STATUS_FAILED:
            self._artifacts[artifact_type] = record
        return replace(outcome, artifact=record.relative_path, sha256=record.sha256)

    def _check_artifact(self, artifact_type: str, reported_path: Any, reported_sha256: Any) -> str | None:
        """반환 경로가 기대 경로인지, 파일 바이트 SHA-256이 반환값과 같은지만 본다. 내용은 읽지 않는다."""
        expected = self._layout.absolute_path(artifact_type)
        reported = Path(str(reported_path))
        if not reported.is_absolute():
            reported = self._layout.run_root / reported
        if reported.resolve() != expected.resolve():
            return "반환 경로가 기대 경로와 다름"
        if not expected.is_file():
            return "결과 파일이 없음"
        if _file_sha256(expected) != reported_sha256:
            return "파일 SHA-256이 반환값과 다름"
        return None

    def _ground_truth_descriptor(self) -> dict[str, str]:
        """개발 평가 전용. 런너는 GT를 해시만 하고 내용은 reporter.evaluate만 읽는다(절대 규칙 8)."""
        relative_path = GROUND_TRUTH_RELATIVE_FORMAT.format(self._options.dataset_id)
        path = self._options.project_root / relative_path
        if not path.is_file():
            raise StepError("ground_truth 파일이 없음")
        return {"path": relative_path, "sha256": _file_sha256(path)}

    # ---- 입력·context 조립 ----

    def _base_context(self) -> dict[str, Any]:
        return {
            "run_id": self._options.run_id,
            "iteration": self._layout.iteration,
            "mode": self._options.mode,
            "run_root": str(self._layout.run_root),
        }

    def _graph_context(self, expected_revision: int | None) -> dict[str, Any]:
        return {**self._base_context(), "graph_id": self._graph_id, "expected_graph_revision": expected_revision}

    def _report_context(self) -> dict[str, Any]:
        return {**self._base_context(), "target_url": self._options.target_url}

    def _descriptor(self, artifact_type: str) -> dict[str, str]:
        record = self._artifacts[artifact_type]
        return {"path": record.relative_path, "sha256": record.sha256}

    def _absolute(self, artifact_type: str) -> str:
        return str(self._layout.run_root / self._artifacts[artifact_type].relative_path)


def _outcome_from_response(step_id: str, response: Mapping[str, Any]) -> StepOutcome:
    error_codes, error_count = _read_errors(response)
    control = {key: response[key] for key in CONTROL_KEYS if key in response}
    status = response.get("status")
    if status not in MODULE_STATUSES:
        return StepOutcome(step_id, STATUS_FAILED, None, None, error_codes, error_count, "알 수 없는 status", control)
    return StepOutcome(step_id, status, None, None, error_codes, error_count, None, control)


def _read_errors(response: Mapping[str, Any]) -> tuple[list[str], int]:
    """오류 코드와 건수. 모듈마다 errors가 목록·건수(int)이거나 error_count로 온다."""
    errors = response.get("errors")
    if isinstance(errors, list):
        return [str(item.get("code")) for item in errors if isinstance(item, Mapping)], len(errors)
    count = errors if errors is not None else response.get("error_count", 0)
    return [], count if _is_count(count) else 0


def _is_count(value: Any) -> bool:
    # bool은 int의 하위 클래스라 따로 막는다.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_revision(value: Any) -> bool:
    return _is_count(value)


def _is_graph_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decide_exit_code(outcomes: Sequence[StepOutcome], session_window: str) -> int:
    statuses = {outcome.status for outcome in outcomes}
    if statuses & {STATUS_FAILED, STATUS_SKIPPED}:
        return EXIT_FAILED
    if STATUS_PARTIAL in statuses or session_window == SESSION_WINDOW_FAILED:
        return EXIT_PARTIAL
    return EXIT_COMPLETED


def build_summary(run: PipelineRun, options: PipelineOptions, layout: RunLayout, exit_code: int) -> dict[str, Any]:
    return {
        "run_id": options.run_id,
        "mode": options.mode,
        "iteration": layout.iteration,
        "run_root": str(layout.run_root),
        "exit_code": exit_code,
        "halted_by": run.halted_by,
        "session_window": run.session_window,
        "session_window_error": run.session_window_error,
        "steps": [asdict(outcome) for outcome in run.outcomes],
    }


def load_entrypoints() -> dict[str, Any]:
    """각 모듈의 공개 창구(entrypoint)만 불러온다. 내부 모듈은 import하지 않는다."""
    return {module_id: importlib.import_module(ENTRYPOINT_MODULE_FORMAT.format(module_id)) for module_id in MODULE_IDS}


def create_run_root(options: PipelineOptions) -> Path:
    """새 run_root를 만든다. 이미 있으면 FileExistsError(완료 파일 불변·근거 중복 방지)."""
    run_root = (options.runs_dir / options.run_id).resolve()
    run_root.parent.mkdir(parents=True, exist_ok=True)
    run_root.mkdir()
    return run_root


def run_pipeline(options: PipelineOptions, entrypoints: Mapping[str, Any] | None = None) -> PipelineResult:
    """모든 단계를 한 회차 돌린다. entrypoints는 테스트 대역 주입용(없으면 실제 모듈)."""
    loaded = entrypoints if entrypoints is not None else load_entrypoints()
    layout = RunLayout(create_run_root(options), FIRST_ITERATION)
    run = PipelineRun(options, layout, loaded)
    run.execute()
    exit_code = decide_exit_code(run.outcomes, run.session_window)
    return PipelineResult(build_summary(run, options, layout, exit_code), exit_code)


def make_run_id(now: datetime) -> str:
    """시각 + 짧은 난수. 같은 초에 두 번 돌려도 실행이 갈린다(모듈 CLI와 같은 형식)."""
    return f"{now.strftime(RUN_ID_TIME_FORMAT)}-{secrets.token_hex(RUN_ID_RANDOM_BYTES)}"


def parse_arguments(argv: Sequence[str] | None = None) -> PipelineOptions:
    """CLI 인자를 읽는다. 잘못된 조합은 parser.error로 종료 코드 2."""
    parser = argparse.ArgumentParser(prog="pipeline.py", description="8개 모듈을 순서대로 실행한다(iteration 0)")
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument(
        "--target-url", required=True, help="reporter에 넘길 대상 URL. collector 설정의 target_url과 글자 그대로 같아야 한다"
    )
    parser.add_argument("--run-id", help="없으면 시각+난수로 만든다")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR, help=f"기본 {DEFAULT_RUNS_DIR}")
    parser.add_argument(
        "--project-root", type=Path, default=PROJECT_ROOT, help="ground_truth 경로 기준(기본: 이 파일이 있는 레포 루트)"
    )
    parser.add_argument("--dataset-id", help="development 필수. datasets/<dataset_id>/ground_truth.json")
    parser.add_argument("--matching-profile", help="development 필수. reporter.evaluate 매칭 프로파일")
    args = parser.parse_args(argv)
    _check_mode_arguments(parser, args)
    run_id = args.run_id or make_run_id(datetime.now(UTC))
    if not NAME_PATTERN.fullmatch(run_id):
        parser.error("--run-id는 영문·숫자로 시작하고 영문·숫자·._-만 쓸 수 있다")
    return PipelineOptions(
        mode=args.mode,
        target_url=args.target_url,
        run_id=run_id,
        runs_dir=args.runs_dir,
        project_root=args.project_root.resolve(),
        dataset_id=args.dataset_id,
        matching_profile=args.matching_profile,
    )


def _check_mode_arguments(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """개발 평가 인자는 development에서 필수, diagnosis에서 금지(조용히 무시하지 않는다)."""
    evaluation_arguments = {"--dataset-id": args.dataset_id, "--matching-profile": args.matching_profile}
    if args.mode == MODE_DEVELOPMENT:
        missing = [name for name, value in evaluation_arguments.items() if value is None]
        if missing:
            parser.error(f"development 모드에는 {', '.join(missing)}가 필요하다")
    else:
        given = [name for name, value in evaluation_arguments.items() if value is not None]
        if given:
            parser.error(f"diagnosis 모드에서는 {', '.join(given)}를 쓰지 않는다(개발 평가 전용)")
    if args.dataset_id is not None and not NAME_PATTERN.fullmatch(args.dataset_id):
        parser.error("--dataset-id는 영문·숫자로 시작하고 영문·숫자·._-만 쓸 수 있다")


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    options = parse_arguments(argv)
    try:
        result = run_pipeline(options)
    except FileExistsError:
        logger.error("run_root가 이미 있음: 새 --run-id로 다시 실행한다")
        return EXIT_FAILED
    # stdout은 호출자가 읽는 요약 한 줄이다. 로그는 stderr로 간다.
    sys.stdout.write(json.dumps(result.summary, ensure_ascii=False) + "\n")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
