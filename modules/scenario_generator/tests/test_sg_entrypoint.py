import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from modules.scenario_generator import entrypoint
from modules.scenario_generator.entrypoint import OutputWriteError, main, run
from modules.scenario_generator.input_adapter import InputErrorCode
from modules.scenario_generator.output_adapter import validate_output_document
from modules.scenario_generator.replay_drafter import ReplayScenarioDrafter
from modules.scenario_generator.utils.hashing import compute_sha256_of_bytes

ARTIFACTS = "artifacts/iteration-000"
CRAWL_PATH = f"{ARTIFACTS}/collector/crawl_result.json"
CANDIDATES_PATH = f"{ARTIFACTS}/access_analyzer/vulnerability_candidates.json"
OUTPUT_DIR = f"{ARTIFACTS}/scenario_generator"
EXPECTED_OUTPUT_PATH = f"{OUTPUT_DIR}/test_scenarios.json"


class Workspace:
    """fixture 입력만 복사한 임시 run 폴더. 기대 출력 fixture는 복사하지 않는다."""

    def __init__(self, run_root: Path, run_id: str) -> None:
        self.run_root = run_root
        self.run_id = run_id
        self.input_paths = {
            "vulnerability_candidates": run_root / CANDIDATES_PATH,
            "crawl_result": run_root / CRAWL_PATH,
        }
        self.output_dir = run_root / OUTPUT_DIR

    def context(self, drafter: Any = None, **overrides: Any) -> dict[str, Any]:
        base = {"run_root": self.run_root, "run_id": self.run_id, "iteration": 0, "mode": "development", "drafter": drafter}
        return {**base, **overrides}

    def run(self, drafter: Any = None, **overrides: Any) -> dict[str, Any]:
        return run("generate", self.input_paths, self.output_dir, self.context(drafter, **overrides))

    def read_output(self) -> dict[str, Any]:
        return json.loads((self.output_dir / "test_scenarios.json").read_text(encoding="utf-8"))

    def rewrite(self, artifact_type: str, mutate: Any) -> None:
        path = self.input_paths[artifact_type]
        document = json.loads(path.read_text(encoding="utf-8"))
        mutate(document)
        path.write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path, fixture_run_root: Path, run_id: str) -> Workspace:
    run_root = tmp_path / "run"
    for producer in ("collector", "access_analyzer"):
        shutil.copytree(fixture_run_root / ARTIFACTS / producer, run_root / ARTIFACTS / producer)
    return Workspace(run_root, run_id)


@pytest.fixture
def replay_drafter(drafts: dict[str, Any]) -> ReplayScenarioDrafter:
    return ReplayScenarioDrafter(drafts)


def test_full_run_writes_a_valid_completed_file(workspace: Workspace, replay_drafter: ReplayScenarioDrafter) -> None:
    response = workspace.run(replay_drafter)

    output_path = workspace.output_dir / "test_scenarios.json"
    document = workspace.read_output()
    validate_output_document(document)
    assert response["status"] == document["status"] == "completed"
    assert response["output_path"] == str(output_path)
    assert response["sha256"] == compute_sha256_of_bytes(output_path.read_bytes())
    assert response["scenario_count"] == len(document["data"]["scenarios"]) > 0
    assert response["error_count"] == len(document["errors"]) == 0
    assert (document["run_id"], document["iteration"], document["mode"]) == (workspace.run_id, 0, "development")


def test_input_refs_describe_the_exact_bytes_that_were_read(
    workspace: Workspace, replay_drafter: ReplayScenarioDrafter
) -> None:
    workspace.run(replay_drafter)
    refs_by_type = {ref["artifact_type"]: ref for ref in workspace.read_output()["input_refs"]}

    assert set(refs_by_type) == {"crawl_result", "vulnerability_candidates"}
    for artifact_type, path in workspace.input_paths.items():
        assert refs_by_type[artifact_type]["path"] == path.relative_to(workspace.run_root).as_posix()
        assert refs_by_type[artifact_type]["sha256"] == compute_sha256_of_bytes(path.read_bytes())


def test_output_matches_the_published_sample_fixture(
    workspace: Workspace, replay_drafter: ReplayScenarioDrafter, fixture_run_root: Path
) -> None:
    workspace.run(replay_drafter)
    actual = workspace.read_output()
    expected = json.loads((fixture_run_root / EXPECTED_OUTPUT_PATH).read_text(encoding="utf-8"))
    for document in (actual, expected):
        document["created_at"] = "normalized"
        document["runtime_metrics"]["duration_ms"] = 0
    assert actual == expected


def test_existing_output_is_never_overwritten(workspace: Workspace, replay_drafter: ReplayScenarioDrafter) -> None:
    workspace.run(replay_drafter)
    first_bytes = (workspace.output_dir / "test_scenarios.json").read_bytes()
    with pytest.raises(OutputWriteError):
        workspace.run(replay_drafter)
    assert (workspace.output_dir / "test_scenarios.json").read_bytes() == first_bytes


def test_partial_when_one_candidate_has_no_draft(workspace: Workspace, drafts: dict[str, Any]) -> None:
    missing_id = next(iter(drafts))
    incomplete = {key: value for key, value in drafts.items() if key != missing_id}
    response = workspace.run(ReplayScenarioDrafter(incomplete))

    document = workspace.read_output()
    assert response["status"] == document["status"] == "partial"
    assert [e["item_ref"] for e in document["errors"]] == [missing_id]
    assert len(document["data"]["scenarios"]) == len(drafts) - 1


def test_failed_when_no_candidate_yields_a_scenario(workspace: Workspace, drafts: dict[str, Any]) -> None:
    response = workspace.run(ReplayScenarioDrafter({}))

    document = workspace.read_output()
    assert response["status"] == document["status"] == "failed"
    assert document["data"] is None and response["scenario_count"] == 0
    assert sorted(e["item_ref"] for e in document["errors"]) == sorted(drafts)


def test_zero_candidates_is_a_normal_empty_result(workspace: Workspace, replay_drafter: ReplayScenarioDrafter) -> None:
    workspace.rewrite("vulnerability_candidates", lambda doc: doc["data"].update({"candidates": []}))
    response = workspace.run(replay_drafter)

    document = workspace.read_output()
    assert response["status"] == "completed"
    assert document["data"]["scenarios"] == [] and document["errors"] == []


def test_partial_upstream_makes_the_result_partial(workspace: Workspace, replay_drafter: ReplayScenarioDrafter) -> None:
    upstream_error = {"code": "UPSTREAM", "message": "m", "item_ref": None, "retryable": False}
    workspace.rewrite("crawl_result", lambda doc: doc.update({"status": "partial", "errors": [upstream_error]}))
    response = workspace.run(replay_drafter)

    document = workspace.read_output()
    assert response["status"] == "partial"
    assert [e["code"] for e in document["errors"]] == [InputErrorCode.UPSTREAM_PARTIAL]
    assert document["data"]["scenarios"]


def test_broken_input_gives_a_failed_file_with_the_cause(
    workspace: Workspace, replay_drafter: ReplayScenarioDrafter
) -> None:
    workspace.rewrite("crawl_result", lambda doc: doc["data"].pop("accounts"))
    response = workspace.run(replay_drafter)

    document = workspace.read_output()
    assert response["status"] == document["status"] == "failed"
    assert document["data"] is None
    assert [e["code"] for e in document["errors"]] == [InputErrorCode.SCHEMA_INVALID]
    assert [ref["artifact_type"] for ref in document["input_refs"]] == ["vulnerability_candidates"]


def test_failed_upstream_input_is_not_hidden(workspace: Workspace, replay_drafter: ReplayScenarioDrafter) -> None:
    upstream_error = {"code": "UPSTREAM", "message": "m", "item_ref": None, "retryable": False}
    workspace.rewrite(
        "vulnerability_candidates", lambda doc: doc.update({"status": "failed", "errors": [upstream_error], "data": None})
    )
    workspace.run(replay_drafter)

    document = workspace.read_output()
    assert document["status"] == "failed"
    assert [e["code"] for e in document["errors"]] == [InputErrorCode.UPSTREAM_FAILED]


def test_both_input_problems_are_reported_together(workspace: Workspace, replay_drafter: ReplayScenarioDrafter) -> None:
    workspace.rewrite("crawl_result", lambda doc: doc.update({"schema_version": "9.9.9"}))
    workspace.rewrite("vulnerability_candidates", lambda doc: doc.update({"run_id": "another_run"}))
    workspace.run(replay_drafter)

    codes = {e["code"] for e in workspace.read_output()["errors"]}
    assert codes == {InputErrorCode.VERSION_UNSUPPORTED, InputErrorCode.RUN_MISMATCH}


def test_wrong_expected_hash_gives_failed_file(workspace: Workspace, replay_drafter: ReplayScenarioDrafter) -> None:
    workspace.run(replay_drafter, expected_sha256={"crawl_result": "0" * 64})
    document = workspace.read_output()
    assert document["status"] == "failed"
    assert [e["code"] for e in document["errors"]] == [InputErrorCode.HASH_MISMATCH]


def test_correct_expected_hash_is_accepted(workspace: Workspace, replay_drafter: ReplayScenarioDrafter) -> None:
    digest = compute_sha256_of_bytes(workspace.input_paths["crawl_result"].read_bytes())
    response = workspace.run(replay_drafter, expected_sha256={"crawl_result": digest})
    assert response["status"] == "completed"


def test_missing_drafter_gives_failed_file(workspace: Workspace) -> None:
    workspace.run(None)
    document = workspace.read_output()
    assert document["status"] == "failed"
    assert [e["code"] for e in document["errors"]] == [entrypoint.DRAFTER_NOT_CONFIGURED_CODE]
    assert len(document["input_refs"]) == 2


def test_duplicate_ids_in_crawl_result_give_failed_file(
    workspace: Workspace, replay_drafter: ReplayScenarioDrafter
) -> None:
    workspace.rewrite("crawl_result", lambda doc: doc["data"]["roles"].append(copy.deepcopy(doc["data"]["roles"][0])))
    workspace.run(replay_drafter)
    assert [e["code"] for e in workspace.read_output()["errors"]] == [InputErrorCode.DUPLICATE_ID]


def test_unsupported_operation_is_a_caller_error(workspace: Workspace) -> None:
    with pytest.raises(ValueError):
        run("analyze", workspace.input_paths, workspace.output_dir, workspace.context())


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": ""},
        {"run_id": None},
        {"iteration": -1},
        {"iteration": True},
        {"iteration": "0"},
        {"mode": "production"},
        {"run_root": None},
        {"expected_sha256": {"crawl_result": "not-a-hash"}},
        {"expected_sha256": {"graph_query": "0" * 64}},
    ],
)
def test_invalid_context_is_a_caller_error(workspace: Workspace, overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        workspace.run(None, **overrides)
    assert not workspace.output_dir.exists()


def test_input_paths_must_name_exactly_the_two_inputs(workspace: Workspace) -> None:
    with pytest.raises(ValueError):
        run("generate", {"crawl_result": workspace.input_paths["crawl_result"]}, workspace.output_dir, workspace.context())


def test_output_dir_outside_run_root_is_refused(workspace: Workspace, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    with pytest.raises(OutputWriteError):
        run("generate", workspace.input_paths, outside, workspace.context(None))
    assert not outside.exists()


def test_save_failure_is_raised_not_reported_as_done(
    workspace: Workspace, replay_drafter: ReplayScenarioDrafter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(path: Path, document: Any) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(entrypoint, "write_json_atomically", fail_write)
    with pytest.raises(OutputWriteError):
        workspace.run(replay_drafter)


def cli_args(workspace: Workspace, drafts_path: Path | None, *extra: str) -> list[str]:
    args = [
        "generate",
        "--run-root", str(workspace.run_root),
        "--run-id", workspace.run_id,
        "--iteration", "0",
        "--mode", "development",
        "--candidates", str(workspace.input_paths["vulnerability_candidates"]),
        "--crawl-result", str(workspace.input_paths["crawl_result"]),
        "--output-dir", str(workspace.output_dir),
    ]
    if drafts_path is not None:
        args += ["--drafts", str(drafts_path)]
    return args + list(extra)


def test_cli_writes_the_file_and_prints_the_response(
    workspace: Workspace, drafts_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(cli_args(workspace, drafts_path))

    printed = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert printed["status"] == workspace.read_output()["status"] == "completed"


def test_cli_without_drafts_still_writes_a_failed_file(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(cli_args(workspace, None))

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_cli_returns_2_when_no_file_can_be_written(
    workspace: Workspace, drafts_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(cli_args(workspace, drafts_path))
    capsys.readouterr()
    assert main(cli_args(workspace, drafts_path)) == 2  # 같은 경로에 두 번째 쓰기는 거부된다


def test_cli_rejects_malformed_expected_sha256(workspace: Workspace, drafts_path: Path) -> None:
    assert main(cli_args(workspace, drafts_path, "--expected-sha256", "no_equals_sign")) == 2


def test_cli_rejects_missing_drafts_file(workspace: Workspace, tmp_path: Path) -> None:
    assert main(cli_args(workspace, tmp_path / "no_such_drafts.json")) == 2
