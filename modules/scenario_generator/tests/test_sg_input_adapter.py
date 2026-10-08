import copy
import json
from pathlib import Path
from typing import Any

import pytest

from modules.scenario_generator.input_adapter import (
    InputError,
    InputErrorCode,
    InputSource,
    load_input_artifact,
)
from modules.scenario_generator.utils.hashing import compute_sha256_of_bytes

FIXTURE_RUN_ROOT = Path(__file__).parent / "fixtures" / "runs" / "run_demo_001"
CRAWL_RELATIVE_PATH = "artifacts/iteration-000/collector/crawl_result.json"
CANDIDATES_RELATIVE_PATH = "artifacts/iteration-000/access_analyzer/vulnerability_candidates.json"
UPSTREAM_ERROR = {"code": "UPSTREAM", "message": "m", "item_ref": None, "retryable": False}


def read_fixture(relative_path: str) -> dict[str, Any]:
    return json.loads((FIXTURE_RUN_ROOT / relative_path).read_text(encoding="utf-8"))


def fixture_run_id() -> str:
    return read_fixture(CRAWL_RELATIVE_PATH)["run_id"]


def write_run_file(run_root: Path, relative_path: str, content: dict[str, Any] | bytes) -> Path:
    path = run_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode("utf-8"))
    return path


def load_crawl(path: Path, run_root: Path, expected_sha256: str | None = None) -> Any:
    return load_input_artifact(InputSource("crawl_result", path, expected_sha256), run_root, fixture_run_id())


def expect_error(code: InputErrorCode, path: Path, run_root: Path, **kwargs: Any) -> InputError:
    with pytest.raises(InputError) as caught:
        load_crawl(path, run_root, **kwargs)
    assert caught.value.code is code
    return caught.value


@pytest.mark.parametrize(
    ("artifact_type", "relative_path"),
    [("crawl_result", CRAWL_RELATIVE_PATH), ("vulnerability_candidates", CANDIDATES_RELATIVE_PATH)],
)
def test_valid_fixtures_load_with_artifact_ref(artifact_type: str, relative_path: str) -> None:
    path = FIXTURE_RUN_ROOT / relative_path
    loaded = load_input_artifact(InputSource(artifact_type, path), FIXTURE_RUN_ROOT, fixture_run_id())

    assert loaded.status == "completed"
    assert loaded.document["artifact_type"] == artifact_type
    assert loaded.artifact_ref["path"] == relative_path
    assert loaded.artifact_ref["sha256"] == compute_sha256_of_bytes(path.read_bytes())
    assert loaded.artifact_ref["artifact_id"] == loaded.document["artifact_id"]


def test_matching_expected_hash_is_accepted() -> None:
    path = FIXTURE_RUN_ROOT / CRAWL_RELATIVE_PATH
    load_crawl(path, FIXTURE_RUN_ROOT, expected_sha256=compute_sha256_of_bytes(path.read_bytes()))


def test_wrong_expected_hash_is_rejected() -> None:
    path = FIXTURE_RUN_ROOT / CRAWL_RELATIVE_PATH
    expect_error(InputErrorCode.HASH_MISMATCH, path, FIXTURE_RUN_ROOT, expected_sha256="0" * 64)


def test_run_id_mismatch_is_rejected(tmp_path: Path) -> None:
    document = read_fixture(CRAWL_RELATIVE_PATH)
    document["run_id"] = "another_run"
    path = write_run_file(tmp_path, CRAWL_RELATIVE_PATH, document)
    expect_error(InputErrorCode.RUN_MISMATCH, path, tmp_path)


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    document = read_fixture(CRAWL_RELATIVE_PATH)
    document["schema_version"] = "9.9.9"
    path = write_run_file(tmp_path, CRAWL_RELATIVE_PATH, document)
    expect_error(InputErrorCode.VERSION_UNSUPPORTED, path, tmp_path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc["data"].pop("accounts"),
        lambda doc: doc["data"]["requests"][0].pop("session_ref"),
        lambda doc: doc["data"].update({"unexpected_key": 1}),
        lambda doc: doc["data"]["requests"][0].update({"method": 1}),
        lambda doc: doc.update({"producer": "someone_else"}),
        lambda doc: doc.update({"errors": [UPSTREAM_ERROR]}),  # completed인데 errors가 있다
    ],
)
def test_schema_violations_are_rejected(tmp_path: Path, mutate: Any) -> None:
    document = copy.deepcopy(read_fixture(CRAWL_RELATIVE_PATH))
    mutate(document)
    path = write_run_file(tmp_path, CRAWL_RELATIVE_PATH, document)
    expect_error(InputErrorCode.SCHEMA_INVALID, path, tmp_path)


def test_schema_error_message_never_contains_input_values(tmp_path: Path) -> None:
    secret_like_value = "super-secret-value-123"
    document = read_fixture(CRAWL_RELATIVE_PATH)
    document["status"] = secret_like_value
    path = write_run_file(tmp_path, CRAWL_RELATIVE_PATH, document)
    error = expect_error(InputErrorCode.SCHEMA_INVALID, path, tmp_path)
    assert secret_like_value not in error.message
    assert "$.status" in error.message


def test_wrong_artifact_type_is_rejected_by_schema(tmp_path: Path) -> None:
    candidates_bytes = (FIXTURE_RUN_ROOT / CANDIDATES_RELATIVE_PATH).read_bytes()
    path = write_run_file(tmp_path, CRAWL_RELATIVE_PATH, candidates_bytes)
    expect_error(InputErrorCode.SCHEMA_INVALID, path, tmp_path)


@pytest.mark.parametrize(
    "raw",
    [b"{", b'{"a": 1, "a": 2}', b'{"a": NaN}', b'{"a": "\xff"}'],
)
def test_broken_json_is_rejected(tmp_path: Path, raw: bytes) -> None:
    path = write_run_file(tmp_path, CRAWL_RELATIVE_PATH, raw)
    expect_error(InputErrorCode.JSON_INVALID, path, tmp_path)


def test_non_object_json_is_rejected(tmp_path: Path) -> None:
    path = write_run_file(tmp_path, CRAWL_RELATIVE_PATH, b"[1, 2, 3]")
    expect_error(InputErrorCode.SCHEMA_INVALID, path, tmp_path)


def test_failed_upstream_artifact_is_rejected_not_hidden(tmp_path: Path) -> None:
    document = read_fixture(CRAWL_RELATIVE_PATH)
    document.update({"status": "failed", "errors": [UPSTREAM_ERROR], "data": None})
    path = write_run_file(tmp_path, CRAWL_RELATIVE_PATH, document)
    expect_error(InputErrorCode.UPSTREAM_FAILED, path, tmp_path)


def test_partial_upstream_artifact_is_loaded_and_marked_partial(tmp_path: Path) -> None:
    document = read_fixture(CRAWL_RELATIVE_PATH)
    document.update({"status": "partial", "errors": [UPSTREAM_ERROR]})
    path = write_run_file(tmp_path, CRAWL_RELATIVE_PATH, document)
    loaded = load_crawl(path, tmp_path)
    assert loaded.status == "partial"
    assert loaded.document["data"] is not None


def test_missing_file_is_reported_as_unreadable(tmp_path: Path) -> None:
    expect_error(InputErrorCode.UNREADABLE, tmp_path / CRAWL_RELATIVE_PATH, tmp_path)


def test_file_outside_run_root_is_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_a"
    run_root.mkdir()
    other_run_file = write_run_file(tmp_path / "run_b", CRAWL_RELATIVE_PATH, read_fixture(CRAWL_RELATIVE_PATH))
    expect_error(InputErrorCode.PATH_UNSAFE, other_run_file, run_root)


def test_symlink_pointing_outside_run_root_is_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run_a"
    run_root.mkdir()
    outside_file = write_run_file(tmp_path / "elsewhere", "crawl.json", read_fixture(CRAWL_RELATIVE_PATH))
    link = run_root / "link.json"
    link.symlink_to(outside_file)
    expect_error(InputErrorCode.PATH_UNSAFE, link, run_root)


def test_unknown_input_type_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        load_input_artifact(InputSource("graph_query", Path("x.json")), FIXTURE_RUN_ROOT, "run")


def test_error_item_matches_output_contract_shape() -> None:
    item = InputError(InputErrorCode.HASH_MISMATCH, "message").to_error_item()
    assert set(item) == {"code", "message", "item_ref", "retryable"}
    assert item["code"] == "INPUT_HASH_MISMATCH"
    assert item["item_ref"] is None
