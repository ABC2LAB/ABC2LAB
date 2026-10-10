"""pipeline.py 런너 검증. 8개 모듈은 가짜 entrypoint로 바꾸고 런너의 연결·중단·창구·요약·종료 코드만 본다.

가짜 모듈은 실제 모듈의 반환 모양(경로 키 이름·절대/상대·errors 형식)을 그대로 따른다. 산출물 내용은 일부러 JSON이
아닌 바이트로 써서 런너가 본문을 읽지 않고 해시만 하는지 드러나게 한다.
기대값은 명세의 파일 경로·키 이름(literal)으로 적는다. 런너의 상수로 기대값을 만들지 않는다.
"""

import ast
import hashlib
import importlib
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import pipeline

RUN_ID = "run_test_001"
TARGET_URL = "http://localhost:18080"
DATASET_ID = "demo_set"
MATCHING_PROFILE = "profile-test"
GRAPH_ID = "graph-test-1"
INGEST_REVISION = 3
# 연결을 구분하려고 ingest와 다른 값을 돌려준다(prepare는 ingest 값, analyze·verify는 query 값을 써야 한다).
QUERY_REVISION = 5
GROUND_TRUTH_BYTES = b"ground-truth-bytes"
SESSION_SENTINEL = object()

DEVELOPMENT_ORDER = [
    "collector.collect",
    "semantic_analyzer.analyze",
    "knowledge_graph.ingest",
    "access_analyzer.prepare_queries",
    "knowledge_graph.query",
    "access_analyzer.analyze",
    "scenario_generator.generate",
    "safety_policy.evaluate",
    "verifier.verify",
    "knowledge_graph.apply_verification",
    "reporter.report",
    "reporter.evaluate",
]
DIAGNOSIS_ORDER = DEVELOPMENT_ORDER[:-1]
# 실패하면 뒤 단계를 모두 멈추는 단계(뒤 단계가 이 단계의 산출물을 입력으로 쓴다).
HALTING_STEPS = DEVELOPMENT_ORDER[:9]

# 명세 02: runs/<run_id>/artifacts/iteration-<NNN>/<producer>/<file>
RELATIVE_PATH_BY_STEP = {
    "collector.collect": "artifacts/iteration-000/collector/crawl_result.json",
    "semantic_analyzer.analyze": "artifacts/iteration-000/semantic_analyzer/semantic_analysis.json",
    "access_analyzer.prepare_queries": "artifacts/iteration-000/access_analyzer/graph_query.json",
    "knowledge_graph.query": "artifacts/iteration-000/knowledge_graph/graph_query_result.json",
    "access_analyzer.analyze": "artifacts/iteration-000/access_analyzer/vulnerability_candidates.json",
    "scenario_generator.generate": "artifacts/iteration-000/scenario_generator/test_scenarios.json",
    "safety_policy.evaluate": "artifacts/iteration-000/safety_policy/safety_decisions.json",
    "verifier.verify": "artifacts/iteration-000/verifier/verification_results.json",
    "reporter.report": "artifacts/iteration-000/reporter/diagnosis_report.json",
    "reporter.evaluate": "artifacts/iteration-000/reporter/evaluation_results.json",
}
# 실제 모듈 반환 모양: artifact_path(절대) / output_path(절대) / output_path(run_root 상대)
ARTIFACT_PATH_MODULES = {"collector", "access_analyzer", "verifier"}
ABSOLUTE_OUTPUT_PATH_MODULES = {"semantic_analyzer", "scenario_generator"}


def content_of(step: str) -> bytes:
    """가짜 산출물 내용. JSON이 아니어서 런너가 본문을 파싱하면 바로 드러난다."""
    return b"\x00not-json:" + step.encode()


def sha_of(step: str) -> str:
    return hashlib.sha256(content_of(step)).hexdigest()


@dataclass
class Behavior:
    status: str = "completed"
    error: Exception | None = None
    writes_file: bool = True
    reported_path: str | None = None
    reported_sha256: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Call:
    step: str
    input_paths: Any
    output_dir: str
    context: dict[str, Any]
    keyword_arguments: dict[str, Any]


class Harness:
    """가짜 모듈 묶음. 호출·사건 순서를 기록하고 단계별 동작(behaviors)을 바꿀 수 있다."""

    def __init__(self, tmp_path: Path) -> None:
        self.runs_dir = tmp_path / "runs"
        self.run_root = (self.runs_dir / RUN_ID).resolve()
        self.project_root = tmp_path / "project"
        ground_truth = self.project_root / "datasets" / DATASET_ID / "ground_truth.json"
        ground_truth.parent.mkdir(parents=True)
        ground_truth.write_bytes(GROUND_TRUTH_BYTES)
        self.behaviors: dict[str, Behavior] = {}
        self.calls: list[Call] = []
        self.events: list[str] = []
        self.window_call_error: Exception | None = None
        self.window_enter_error: Exception | None = None

    def entrypoints(self) -> dict[str, Any]:
        modules = {module_id: FakeModule(module_id, self) for module_id in pipeline.MODULE_IDS}
        modules["collector"].open_session_executor = self.open_session_executor
        return modules

    def options(self, mode: str = "development") -> pipeline.PipelineOptions:
        is_development = mode == "development"
        return pipeline.PipelineOptions(
            mode=mode,
            target_url=TARGET_URL,
            run_id=RUN_ID,
            runs_dir=self.runs_dir,
            project_root=self.project_root.resolve(),
            dataset_id=DATASET_ID if is_development else None,
            matching_profile=MATCHING_PROFILE if is_development else None,
        )

    def run(self, mode: str = "development") -> pipeline.PipelineResult:
        return pipeline.run_pipeline(self.options(mode), self.entrypoints())

    def call(self, step: str) -> Call:
        [found] = [call for call in self.calls if call.step == step]
        return found

    def open_session_executor(self) -> Any:
        self.events.append("window:call")
        if self.window_call_error is not None:
            raise self.window_call_error
        return self._session_window()

    @contextmanager
    def _session_window(self) -> Iterator[object]:
        if self.window_enter_error is not None:
            raise self.window_enter_error
        self.events.append("window:enter")
        try:
            yield SESSION_SENTINEL
        finally:
            self.events.append("window:exit")


class FakeModule:
    def __init__(self, module_id: str, harness: Harness) -> None:
        self.module_id = module_id
        self.harness = harness

    def run(
        self, operation: str, input_paths: Any, output_dir: str, context: Any, **keyword_arguments: Any
    ) -> dict[str, Any]:
        step = f"{self.module_id}.{operation}"
        self.harness.calls.append(Call(step, input_paths, output_dir, dict(context), dict(keyword_arguments)))
        self.harness.events.append(f"call:{step}")
        behavior = self.harness.behaviors.get(step, Behavior())
        if behavior.error is not None:
            raise behavior.error
        response = self._response(step, Path(output_dir), behavior)
        response.update(behavior.extra)
        return response

    def _response(self, step: str, output_dir: Path, behavior: Behavior) -> dict[str, Any]:
        response: dict[str, Any] = {"status": behavior.status}
        if step == "knowledge_graph.ingest":
            return {**response, "graph_id": GRAPH_ID, "graph_revision": INGEST_REVISION, "is_ready": True, "errors": []}
        if step == "knowledge_graph.apply_verification":
            control = {
                "graph_id": GRAPH_ID,
                "previous_graph_revision": QUERY_REVISION,
                "graph_revision": QUERY_REVISION,
            }
            return {**response, **control, "applied_verification_ids": [], "is_applied": False, "errors": []}
        relative_path = RELATIVE_PATH_BY_STEP[step]
        if behavior.writes_file:
            path = output_dir / Path(relative_path).name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content_of(step))
        reported_path = behavior.reported_path or self._reported_path(relative_path)
        if not behavior.writes_file and behavior.reported_path is None:
            reported_path = None
        response["sha256"] = behavior.reported_sha256 or (sha_of(step) if behavior.writes_file else None)
        if self.module_id == "semantic_analyzer":
            response["errors"] = 0
        elif self.module_id == "scenario_generator":
            response.update(scenario_count=0, error_count=0)
        else:
            response["errors"] = []
        if step == "knowledge_graph.query":
            response.update(graph_id=GRAPH_ID, graph_revision=QUERY_REVISION)
        path_key = "artifact_path" if self.module_id in ARTIFACT_PATH_MODULES else "output_path"
        response[path_key] = reported_path
        return response

    def _reported_path(self, relative_path: str) -> str:
        if self.module_id in ARTIFACT_PATH_MODULES | ABSOLUTE_OUTPUT_PATH_MODULES:
            return str(self.harness.run_root / relative_path)
        return relative_path


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def steps_by_name(result: pipeline.PipelineResult) -> dict[str, dict[str, Any]]:
    return {step["step"]: step for step in result.summary["steps"]}


def statuses(result: pipeline.PipelineResult) -> list[tuple[str, str]]:
    return [(step["step"], step["status"]) for step in result.summary["steps"]]


# ---- 순서와 모드 ----


def test_development_runs_every_step_in_order(harness: Harness) -> None:
    result = harness.run()

    assert [call.step for call in harness.calls] == DEVELOPMENT_ORDER
    assert statuses(result) == [(step, "completed") for step in DEVELOPMENT_ORDER]
    assert result.exit_code == 0


def test_diagnosis_skips_evaluation_and_never_reads_ground_truth(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    datasets_dir = harness.project_root / "datasets"
    touched: list[Path] = []
    original_read_bytes = Path.read_bytes

    def read_bytes_recorder(self: Path) -> bytes:
        if datasets_dir in self.parents:
            touched.append(self)
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes_recorder)

    result = harness.run("diagnosis")

    assert [call.step for call in harness.calls] == DIAGNOSIS_ORDER
    assert "reporter.evaluate" not in steps_by_name(result)
    assert touched == []
    assert result.exit_code == 0


# ---- 입력 연결 ----


def test_path_list_and_name_to_path_inputs_are_absolute_previous_outputs(harness: Harness) -> None:
    harness.run()
    absolute = {step: str(harness.run_root / path) for step, path in RELATIVE_PATH_BY_STEP.items()}

    assert harness.call("collector.collect").input_paths == []
    assert harness.call("semantic_analyzer.analyze").input_paths == {"crawl_result": absolute["collector.collect"]}
    assert harness.call("access_analyzer.prepare_queries").input_paths == []
    assert harness.call("access_analyzer.analyze").input_paths == [absolute["knowledge_graph.query"]]
    assert harness.call("scenario_generator.generate").input_paths == {
        "vulnerability_candidates": absolute["access_analyzer.analyze"],
        "crawl_result": absolute["collector.collect"],
    }
    # verifier는 순서를 보지 않는다(artifact_type으로 가른다).
    assert sorted(harness.call("verifier.verify").input_paths) == sorted(
        [absolute["scenario_generator.generate"], absolute["safety_policy.evaluate"], absolute["collector.collect"]]
    )


def descriptor(step: str) -> dict[str, str]:
    return {"path": RELATIVE_PATH_BY_STEP[step], "sha256": sha_of(step)}


def test_descriptor_inputs_carry_run_root_relative_path_and_file_sha256(harness: Harness) -> None:
    harness.run()
    report_inputs = {
        "vulnerability_candidates": descriptor("access_analyzer.analyze"),
        "test_scenarios": descriptor("scenario_generator.generate"),
        "safety_decisions": descriptor("safety_policy.evaluate"),
        "verification_results": descriptor("verifier.verify"),
    }

    assert harness.call("knowledge_graph.ingest").input_paths == {
        "semantic_analysis": descriptor("semantic_analyzer.analyze")
    }
    assert harness.call("knowledge_graph.query").input_paths == {
        "graph_query": descriptor("access_analyzer.prepare_queries")
    }
    assert harness.call("safety_policy.evaluate").input_paths == {
        "test_scenarios": descriptor("scenario_generator.generate")
    }
    assert harness.call("knowledge_graph.apply_verification").input_paths == {
        "verification_results": descriptor("verifier.verify")
    }
    assert harness.call("reporter.report").input_paths == report_inputs
    assert harness.call("reporter.evaluate").input_paths == {
        **report_inputs,
        "crawl_result": descriptor("collector.collect"),
        "semantic_analysis": descriptor("semantic_analyzer.analyze"),
        "graph_query_result": descriptor("knowledge_graph.query"),
        "ground_truth": {
            "path": f"datasets/{DATASET_ID}/ground_truth.json",
            "sha256": hashlib.sha256(GROUND_TRUTH_BYTES).hexdigest(),
        },
    }


def test_context_keys_and_graph_values_per_step(harness: Harness) -> None:
    harness.run()
    base = {"run_id": RUN_ID, "iteration": 0, "mode": "development", "run_root": str(harness.run_root)}
    expected_context_by_step = {
        "collector.collect": base,
        "semantic_analyzer.analyze": base,
        "knowledge_graph.ingest": base,
        "access_analyzer.prepare_queries": {**base, "graph_id": GRAPH_ID, "expected_graph_revision": INGEST_REVISION},
        "knowledge_graph.query": base,
        "access_analyzer.analyze": {**base, "graph_id": GRAPH_ID, "expected_graph_revision": QUERY_REVISION},
        "scenario_generator.generate": {
            **base,
            "expected_sha256": {
                "vulnerability_candidates": sha_of("access_analyzer.analyze"),
                "crawl_result": sha_of("collector.collect"),
            },
        },
        # C 목표 모양: policy_config를 넘기지 않는다.
        "safety_policy.evaluate": base,
        "verifier.verify": {**base, "source_graph_revision": QUERY_REVISION},
        "knowledge_graph.apply_verification": {**base, "graph_id": GRAPH_ID},
        "reporter.report": {**base, "target_url": TARGET_URL},
        "reporter.evaluate": {
            **base,
            "target_url": TARGET_URL,
            "project_root": str(harness.project_root.resolve()),
            "matching_profile": MATCHING_PROFILE,
        },
    }

    for step, expected in expected_context_by_step.items():
        assert harness.call(step).context == expected, step


def test_contexts_hold_only_json_values_and_session_goes_as_argument(harness: Harness) -> None:
    harness.run()

    for call in harness.calls:
        json.dumps(call.context)  # 객체(세션·drafter 등)가 섞이면 실패한다
    keyword_arguments_by_step = {call.step: call.keyword_arguments for call in harness.calls}
    assert keyword_arguments_by_step.pop("verifier.verify") == {"session_executor": SESSION_SENTINEL}
    assert all(arguments == {} for arguments in keyword_arguments_by_step.values())


def test_output_dir_is_the_producer_folder_of_iteration_0(harness: Harness) -> None:
    harness.run()

    for call in harness.calls:
        module_id = call.step.split(".")[0]
        assert call.output_dir == str(harness.run_root / "artifacts" / "iteration-000" / module_id)


# ---- 실패·중단 ----


@pytest.mark.parametrize("failing_step", HALTING_STEPS)
def test_failed_step_halts_and_later_steps_are_skipped(harness: Harness, failing_step: str) -> None:
    harness.behaviors[failing_step] = Behavior(status="failed")

    result = harness.run()

    index = DEVELOPMENT_ORDER.index(failing_step)
    assert [call.step for call in harness.calls] == DEVELOPMENT_ORDER[: index + 1]
    steps = steps_by_name(result)
    assert steps[failing_step]["status"] == "failed"
    for later_step in DEVELOPMENT_ORDER[index + 1 :]:
        assert steps[later_step]["status"] == "skipped"
        assert failing_step in steps[later_step]["detail"]
    assert result.summary["halted_by"] == failing_step
    assert result.exit_code == 2


@pytest.mark.parametrize(
    "behavior",
    [
        pytest.param(Behavior(status="failed"), id="failed_with_file"),
        pytest.param(Behavior(status="failed", writes_file=False), id="failed_without_file"),
        pytest.param(Behavior(error=ValueError("context 오류")), id="value_error"),
        pytest.param(Behavior(error=RuntimeError("저장 실패")), id="runtime_error"),
        pytest.param(Behavior(status="completed", writes_file=False), id="completed_without_path"),
        pytest.param(Behavior(status="done"), id="unknown_status"),
        pytest.param(
            Behavior(reported_path="artifacts/iteration-000/scenario_generator/other.json"), id="wrong_reported_path"
        ),
        pytest.param(Behavior(reported_sha256="0" * 64), id="sha256_mismatch"),
    ],
)
def test_every_kind_of_failure_halts_the_run(harness: Harness, behavior: Behavior) -> None:
    harness.behaviors["scenario_generator.generate"] = behavior

    result = harness.run()

    assert steps_by_name(result)["scenario_generator.generate"]["status"] == "failed"
    assert "safety_policy.evaluate" not in [call.step for call in harness.calls]
    assert result.exit_code == 2


def test_module_exception_is_recorded_with_its_type(harness: Harness) -> None:
    harness.behaviors["scenario_generator.generate"] = Behavior(error=ValueError("context 오류"))

    result = harness.run()

    assert "ValueError" in steps_by_name(result)["scenario_generator.generate"]["detail"]


@pytest.mark.parametrize(
    ("step", "extra"),
    [
        pytest.param("knowledge_graph.ingest", {"is_ready": False}, id="ingest_not_ready"),
        pytest.param("knowledge_graph.ingest", {"graph_id": None}, id="ingest_without_graph_id"),
        pytest.param("knowledge_graph.ingest", {"graph_revision": True}, id="ingest_bool_revision"),
        pytest.param("knowledge_graph.query", {"graph_id": "graph-other"}, id="query_other_graph"),
        pytest.param("knowledge_graph.query", {"graph_revision": None}, id="query_without_revision"),
    ],
)
def test_knowledge_graph_control_values_are_checked(harness: Harness, step: str, extra: dict[str, Any]) -> None:
    harness.behaviors[step] = Behavior(extra=extra)

    result = harness.run()

    assert steps_by_name(result)[step]["status"] == "failed"
    assert result.summary["halted_by"] == step


def test_verify_failure_skips_apply_report_and_evaluate(harness: Harness) -> None:
    harness.behaviors["verifier.verify"] = Behavior(status="failed")

    result = harness.run()

    assert statuses(result)[-3:] == [
        ("knowledge_graph.apply_verification", "skipped"),
        ("reporter.report", "skipped"),
        ("reporter.evaluate", "skipped"),
    ]


def test_apply_verification_failure_keeps_report_and_evaluate(harness: Harness) -> None:
    harness.behaviors["knowledge_graph.apply_verification"] = Behavior(status="failed")

    result = harness.run()

    assert statuses(result)[-3:] == [
        ("knowledge_graph.apply_verification", "failed"),
        ("reporter.report", "completed"),
        ("reporter.evaluate", "completed"),
    ]
    assert result.summary["halted_by"] is None
    assert result.exit_code == 2


def test_report_failure_keeps_evaluate(harness: Harness) -> None:
    harness.behaviors["reporter.report"] = Behavior(status="failed")

    result = harness.run()

    assert statuses(result)[-2:] == [("reporter.report", "failed"), ("reporter.evaluate", "completed")]
    assert result.exit_code == 2


def test_partial_step_continues(harness: Harness) -> None:
    harness.behaviors["semantic_analyzer.analyze"] = Behavior(status="partial")

    result = harness.run()

    assert [call.step for call in harness.calls] == DEVELOPMENT_ORDER
    assert steps_by_name(result)["semantic_analyzer.analyze"]["status"] == "partial"
    assert result.exit_code == 1


def test_missing_ground_truth_fails_evaluate_only(harness: Harness) -> None:
    (harness.project_root / "datasets" / DATASET_ID / "ground_truth.json").unlink()

    result = harness.run()

    assert steps_by_name(result)["reporter.evaluate"]["status"] == "failed"
    assert steps_by_name(result)["reporter.report"]["status"] == "completed"
    assert "reporter.evaluate" not in [call.step for call in harness.calls]


# ---- 세션 창구 ----


def test_session_window_opens_right_before_verify_and_closes_right_after(harness: Harness) -> None:
    result = harness.run()

    events = harness.events
    window_events = events[events.index("window:call") : events.index("window:exit") + 1]
    assert window_events == ["window:call", "window:enter", "call:verifier.verify", "window:exit"]
    assert events[events.index("window:call") - 1] == "call:safety_policy.evaluate"
    assert events[events.index("window:exit") + 1] == "call:knowledge_graph.apply_verification"
    assert result.summary["session_window"] == "opened"


def test_verify_exception_is_a_step_failure_not_a_window_failure(harness: Harness) -> None:
    harness.behaviors["verifier.verify"] = Behavior(error=RuntimeError("verify 내부 오류"))

    result = harness.run()

    assert harness.events.count("window:exit") == 1
    assert result.summary["session_window"] == "opened"
    assert steps_by_name(result)["verifier.verify"]["status"] == "failed"
    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("on_call", "on_enter", "error_type"),
    [
        pytest.param(ValueError("설정 오류"), None, "ValueError", id="config_error_on_call"),
        pytest.param(OSError("읽기 실패"), None, "OSError", id="os_error_on_call"),
        pytest.param(None, RuntimeError("브라우저 실패"), "RuntimeError", id="browser_error_on_enter"),
    ],
)
def test_window_failure_calls_verify_without_session(
    harness: Harness, on_call: Exception | None, on_enter: Exception | None, error_type: str
) -> None:
    harness.window_call_error, harness.window_enter_error = on_call, on_enter

    result = harness.run()

    assert harness.call("verifier.verify").keyword_arguments == {"session_executor": None}
    assert steps_by_name(result)["verifier.verify"]["status"] == "completed"
    assert (result.summary["session_window"], result.summary["session_window_error"]) == ("failed", error_type)
    assert [call.step for call in harness.calls] == DEVELOPMENT_ORDER
    assert result.exit_code == 1


# ---- 종료 코드 ----


@pytest.mark.parametrize(
    ("setup", "expected_exit_code"),
    [
        pytest.param({}, 0, id="all_completed"),
        pytest.param({"behaviors": {"access_analyzer.analyze": Behavior(status="partial")}}, 1, id="partial"),
        pytest.param({"window_enter_error": RuntimeError("브라우저 실패")}, 1, id="session_window_failed"),
        pytest.param({"behaviors": {"reporter.evaluate": Behavior(status="failed")}}, 2, id="failed_last_step"),
        pytest.param({"behaviors": {"collector.collect": Behavior(status="failed")}}, 2, id="failed_and_skipped"),
    ],
)
def test_exit_code_of_a_run(harness: Harness, setup: dict[str, Any], expected_exit_code: int) -> None:
    harness.behaviors.update(setup.get("behaviors", {}))
    harness.window_enter_error = setup.get("window_enter_error")

    result = harness.run()

    assert result.exit_code == expected_exit_code
    assert result.summary["exit_code"] == expected_exit_code


def cli_arguments(harness: Harness, *extra: str) -> list[str]:
    return [
        "--target-url", TARGET_URL,
        "--run-id", RUN_ID,
        "--runs-dir", str(harness.runs_dir),
        "--project-root", str(harness.project_root),
        *extra,
    ]


DEVELOPMENT_ARGUMENTS = ("--mode", "development", "--dataset-id", DATASET_ID, "--matching-profile", MATCHING_PROFILE)


def test_existing_run_root_exits_2_without_calling_modules(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pipeline, "load_entrypoints", harness.entrypoints)
    harness.run_root.mkdir(parents=True)

    exit_code = pipeline.main(cli_arguments(harness, *DEVELOPMENT_ARGUMENTS))

    assert exit_code == 2
    assert harness.calls == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(("--mode", "development"), id="development_without_evaluation_arguments"),
        pytest.param(("--mode", "development", "--dataset-id", DATASET_ID), id="development_without_matching_profile"),
        pytest.param(("--mode", "diagnosis", "--dataset-id", DATASET_ID), id="diagnosis_with_dataset_id"),
        pytest.param(("--mode", "diagnosis", "--matching-profile", MATCHING_PROFILE), id="diagnosis_with_profile"),
        pytest.param((*DEVELOPMENT_ARGUMENTS[:2], "--dataset-id", "../x", "--matching-profile", "p"), id="bad_dataset"),
        pytest.param(("--mode", "diagnosis", "--run-id", "../evil"), id="bad_run_id"),
        pytest.param(("--mode", "production"), id="unknown_mode"),
    ],
)
def test_cli_error_exits_2_before_any_module(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, extra: tuple[str, ...]
) -> None:
    monkeypatch.setattr(pipeline, "load_entrypoints", harness.entrypoints)

    with pytest.raises(SystemExit) as caught:
        pipeline.main(cli_arguments(harness, *extra))

    assert caught.value.code == 2
    assert harness.calls == []
    assert not harness.run_root.exists()


def test_cli_without_target_url_exits_2(harness: Harness) -> None:
    with pytest.raises(SystemExit) as caught:
        pipeline.main(["--mode", "diagnosis", "--runs-dir", str(harness.runs_dir)])
    assert caught.value.code == 2


# ---- 모듈 경계·비밀값 ----


def test_runner_imports_only_the_standard_library() -> None:
    tree = ast.parse(Path(pipeline.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= set(sys.stdlib_module_names)


def test_only_module_entrypoints_are_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(importlib, "import_module", lambda name: loaded.append(name) or object())

    pipeline.load_entrypoints()

    assert loaded == [
        "modules.collector.entrypoint",
        "modules.semantic_analyzer.entrypoint",
        "modules.knowledge_graph.entrypoint",
        "modules.access_analyzer.entrypoint",
        "modules.scenario_generator.entrypoint",
        "modules.safety_policy.entrypoint",
        "modules.verifier.entrypoint",
        "modules.reporter.entrypoint",
    ]


def test_runner_hashes_artifacts_without_reading_their_json(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("런너가 산출물 JSON 본문을 읽음")

    original_read_text = Path.read_text

    def read_text_guard(self: Path, *args: Any, **kwargs: Any) -> str:
        if harness.runs_dir.resolve() in self.resolve().parents:
            forbidden()
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(json, "load", forbidden)
    monkeypatch.setattr(json, "loads", forbidden)
    monkeypatch.setattr(Path, "read_text", read_text_guard)

    result = harness.run()

    assert result.exit_code == 0


def test_secrets_in_environment_never_reach_output_or_logs(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    secrets_by_name = {
        "NEO4J_PASSWORD": "neo4j-secret-value-41",
        "COLLECTOR_ACCOUNT_USER_A_PASSWORD": "account-secret-value-42",
    }
    for name, value in secrets_by_name.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(pipeline, "load_entrypoints", harness.entrypoints)
    caplog.set_level("DEBUG")

    exit_code = pipeline.main(cli_arguments(harness, *DEVELOPMENT_ARGUMENTS))

    captured = capsys.readouterr()
    assert exit_code == 0
    [summary_line] = captured.out.splitlines()
    assert json.loads(summary_line)["exit_code"] == 0
    for value in secrets_by_name.values():
        assert value not in captured.out
        assert value not in captured.err
        assert value not in caplog.text
    assert all(os.environ[name] == value for name, value in secrets_by_name.items())
