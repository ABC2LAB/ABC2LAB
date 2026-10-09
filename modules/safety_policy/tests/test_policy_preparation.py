import json
from pathlib import Path
from typing import Any

import pytest

from modules.safety_policy import config_adapter
from modules.safety_policy.config_adapter import (
    POLICY_CONFIG_PATH_ENV,
    load_policy_configuration,
    prepare_policy_configuration,
)
from modules.safety_policy.evaluate_adapter import (
    POLICY_CONFIG_RELATIVE_PATH,
    bind_policy_configuration,
    parse_evaluate_request,
)
from modules.safety_policy.exceptions import (
    PathValidationError,
    PolicyConfigHashMismatchError,
    PolicyConfigurationError,
    StorageError,
)
from modules.safety_policy.utils import atomic_writer
from modules.safety_policy.utils.hashing import calculate_sha256
from modules.safety_policy.utils.validation import load_json


@pytest.fixture
def policy_source_path(
    tmp_path: Path,
    completed_policy_config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    source_path = tmp_path / "settings" / "policy.json"
    source_path.parent.mkdir()
    source_path.write_bytes(completed_policy_config_path.read_bytes())
    monkeypatch.setenv(POLICY_CONFIG_PATH_ENV, str(source_path))
    return source_path


@pytest.fixture
def policy_run_root(tmp_path: Path) -> Path:
    run_root = tmp_path / "run_demo_001"
    run_root.mkdir()
    return run_root


def test_prepare_policy_configuration_publishes_exact_source_bytes(
    policy_source_path: Path,
    policy_run_root: Path,
) -> None:
    original_bytes = policy_source_path.read_bytes()

    prepared = prepare_policy_configuration(policy_run_root)

    assert prepared.path == policy_run_root / POLICY_CONFIG_RELATIVE_PATH
    assert prepared.relative_path == POLICY_CONFIG_RELATIVE_PATH
    assert prepared.path.read_bytes() == original_bytes
    assert policy_source_path.read_bytes() == original_bytes
    assert prepared.sha256 == calculate_sha256(prepared.path)
    assert prepared.sha256 == calculate_sha256(policy_source_path)
    assert list(prepared.path.parent.iterdir()) == [prepared.path]


def test_prepare_policy_configuration_accepts_relative_source_path(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(policy_source_path.parent)
    monkeypatch.setenv(POLICY_CONFIG_PATH_ENV, policy_source_path.name)

    prepared = prepare_policy_configuration(policy_run_root)

    assert prepared.path.read_bytes() == policy_source_path.read_bytes()


@pytest.mark.parametrize("configured_path", [None, "", " ", "\t\n"])
def test_prepare_policy_configuration_requires_explicit_source_path(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_path: str | None,
) -> None:
    if configured_path is None:
        monkeypatch.delenv(POLICY_CONFIG_PATH_ENV)
    else:
        monkeypatch.setenv(POLICY_CONFIG_PATH_ENV, configured_path)

    with pytest.raises(PolicyConfigurationError, match=POLICY_CONFIG_PATH_ENV):
        prepare_policy_configuration(policy_run_root)

    assert not (policy_run_root / "private").exists()


@pytest.mark.parametrize("source_kind", ["missing", "directory"])
def test_prepare_policy_configuration_rejects_non_file_source(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_kind: str,
) -> None:
    source_path = (
        policy_source_path.parent / "missing.json"
        if source_kind == "missing"
        else policy_source_path.parent
    )
    monkeypatch.setenv(POLICY_CONFIG_PATH_ENV, str(source_path))

    with pytest.raises(PolicyConfigurationError):
        prepare_policy_configuration(policy_run_root)

    assert not (policy_run_root / "private").exists()


@pytest.mark.parametrize(
    "content",
    [
        b"{",
        b"[]",
        b"null",
        b"\xff",
        b'{"policy_id":"first","policy_id":"second"}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
    ],
)
def test_prepare_policy_configuration_rejects_invalid_json_before_storage(
    policy_source_path: Path,
    policy_run_root: Path,
    content: bytes,
) -> None:
    policy_source_path.write_bytes(content)

    with pytest.raises(PolicyConfigurationError, match="계약"):
        prepare_policy_configuration(policy_run_root)

    assert not (policy_run_root / "private").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "0.2.0"),
        ("unexpected", True),
        ("limits", {"max_requests": 0, "max_duration_ms": 1000}),
    ],
)
def test_prepare_policy_configuration_rejects_schema_violation_before_storage(
    policy_source_path: Path,
    policy_run_root: Path,
    field: str,
    value: Any,
) -> None:
    configuration = load_json(policy_source_path)
    configuration[field] = value
    policy_source_path.write_text(json.dumps(configuration), encoding="utf-8")

    with pytest.raises(PolicyConfigurationError, match="계약"):
        prepare_policy_configuration(policy_run_root)

    assert not (policy_run_root / "private").exists()


def test_prepare_policy_configuration_reports_source_read_failure(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_read(path: Path) -> bytes:
        raise PermissionError("fixture read failure")

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    with pytest.raises(PolicyConfigurationError, match="읽을 수 없음"):
        prepare_policy_configuration(policy_run_root)

    assert not (policy_run_root / "private").exists()


def test_prepare_policy_configuration_reports_storage_failure_and_cleans_temp(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_flush(descriptor: int) -> None:
        raise OSError("fixture storage failure")

    monkeypatch.setattr(atomic_writer.os, "fsync", fail_flush)

    with pytest.raises(StorageError, match="저장하거나 읽을 수 없음"):
        prepare_policy_configuration(policy_run_root)

    policy_path = policy_run_root / POLICY_CONFIG_RELATIVE_PATH
    assert not policy_path.exists()
    assert list(policy_path.parent.iterdir()) == []


def test_prepare_policy_configuration_reuses_identical_snapshot_without_writing(
    policy_source_path: Path,
    policy_run_root: Path,
) -> None:
    prepared = prepare_policy_configuration(policy_run_root)
    original_bytes = prepared.path.read_bytes()
    original_stat = prepared.path.stat()

    reused = prepare_policy_configuration(policy_run_root)

    assert reused == prepared
    assert prepared.path.read_bytes() == original_bytes
    assert prepared.path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert prepared.path.stat().st_ino == original_stat.st_ino
    assert list(prepared.path.parent.iterdir()) == [prepared.path]


def test_prepare_policy_configuration_accepts_identical_source_at_another_path(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_policy_configuration(policy_run_root)
    alternate_path = policy_source_path.with_name("same_policy.json")
    alternate_path.write_bytes(policy_source_path.read_bytes())
    monkeypatch.setenv(POLICY_CONFIG_PATH_ENV, str(alternate_path))

    assert prepare_policy_configuration(policy_run_root) == prepared


@pytest.mark.parametrize("change_kind", ["limits", "formatting"])
def test_prepare_policy_configuration_rejects_changed_source_and_preserves_snapshot(
    policy_source_path: Path,
    policy_run_root: Path,
    change_kind: str,
) -> None:
    prepared = prepare_policy_configuration(policy_run_root)
    original_bytes = prepared.path.read_bytes()
    original_modified_at = prepared.path.stat().st_mtime_ns
    if change_kind == "limits":
        configuration = load_json(policy_source_path)
        configuration["limits"]["max_requests"] += 1
        policy_source_path.write_text(json.dumps(configuration), encoding="utf-8")
    else:
        policy_source_path.write_bytes(original_bytes + b"\n")
    source_configuration = load_json(policy_source_path)
    snapshot_configuration = load_json(prepared.path)
    assert source_configuration["policy_id"] == snapshot_configuration["policy_id"]
    assert source_configuration["policy_version"] == snapshot_configuration["policy_version"]

    with pytest.raises(PolicyConfigHashMismatchError):
        prepare_policy_configuration(policy_run_root)

    assert prepared.path.read_bytes() == original_bytes
    assert prepared.path.stat().st_mtime_ns == original_modified_at
    assert list(prepared.path.parent.iterdir()) == [prepared.path]


@pytest.mark.parametrize("snapshot_kind", ["changed_policy", "invalid_json"])
def test_prepare_policy_configuration_rejects_changed_snapshot_without_repair(
    policy_source_path: Path,
    policy_run_root: Path,
    snapshot_kind: str,
) -> None:
    prepared = prepare_policy_configuration(policy_run_root)
    if snapshot_kind == "changed_policy":
        configuration = load_json(prepared.path)
        configuration["limits"]["max_requests"] += 1
        changed_bytes = json.dumps(configuration).encode("utf-8")
    else:
        changed_bytes = b"invalid policy snapshot"
    prepared.path.write_bytes(changed_bytes)
    modified_at = prepared.path.stat().st_mtime_ns

    with pytest.raises(PolicyConfigHashMismatchError):
        prepare_policy_configuration(policy_run_root)

    assert prepared.path.read_bytes() == changed_bytes
    assert prepared.path.stat().st_mtime_ns == modified_at


def test_prepare_policy_configuration_rejects_snapshot_directory(
    policy_source_path: Path,
    policy_run_root: Path,
) -> None:
    policy_path = policy_run_root / POLICY_CONFIG_RELATIVE_PATH
    policy_path.mkdir(parents=True)

    with pytest.raises(PolicyConfigurationError, match="파일이어야 함"):
        prepare_policy_configuration(policy_run_root)

    assert policy_path.is_dir()
    assert list(policy_path.iterdir()) == []


@pytest.mark.parametrize("source_failure", ["unset", "missing", "invalid_json"])
def test_prepare_policy_configuration_does_not_fallback_to_existing_snapshot(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_failure: str,
) -> None:
    prepared = prepare_policy_configuration(policy_run_root)
    original_bytes = prepared.path.read_bytes()
    if source_failure == "unset":
        monkeypatch.delenv(POLICY_CONFIG_PATH_ENV)
    elif source_failure == "missing":
        monkeypatch.setenv(
            POLICY_CONFIG_PATH_ENV,
            str(policy_source_path.with_name("missing.json")),
        )
    else:
        policy_source_path.write_bytes(b"invalid source")

    with pytest.raises(PolicyConfigurationError):
        prepare_policy_configuration(policy_run_root)

    assert prepared.path.read_bytes() == original_bytes


def test_prepare_policy_configuration_reports_existing_snapshot_read_failure(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_policy_configuration(policy_run_root)
    original_bytes = prepared.path.read_bytes()

    def fail_hash(path: Path) -> str:
        raise PermissionError("fixture snapshot read failure")

    monkeypatch.setattr(config_adapter, "calculate_sha256", fail_hash)

    with pytest.raises(StorageError, match="저장하거나 읽을 수 없음"):
        prepare_policy_configuration(policy_run_root)

    assert prepared.path.read_bytes() == original_bytes


@pytest.mark.parametrize("has_same_policy", [True, False])
def test_prepare_policy_configuration_checks_snapshot_published_by_another_writer(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_same_policy: bool,
) -> None:
    competing_bytes = policy_source_path.read_bytes()
    if not has_same_policy:
        configuration = load_json(policy_source_path)
        configuration["limits"]["max_requests"] += 1
        competing_bytes = json.dumps(configuration).encode("utf-8")
    original_writer = config_adapter.write_bytes_atomically

    def publish_competing_snapshot(output_path: Path, content: bytes) -> None:
        original_writer(output_path, competing_bytes)
        original_writer(output_path, content)

    monkeypatch.setattr(
        config_adapter,
        "write_bytes_atomically",
        publish_competing_snapshot,
    )

    if has_same_policy:
        prepared = prepare_policy_configuration(policy_run_root)
        assert prepared.sha256 == calculate_sha256(prepared.path)
    else:
        with pytest.raises(PolicyConfigHashMismatchError):
            prepare_policy_configuration(policy_run_root)

    policy_path = policy_run_root / POLICY_CONFIG_RELATIVE_PATH
    assert policy_path.read_bytes() == competing_bytes
    assert list(policy_path.parent.iterdir()) == [policy_path]


@pytest.mark.parametrize("target_kind", ["outside_run", "other_module"])
def test_prepare_policy_configuration_rechecks_path_after_publication_conflict(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    target_path = (
        policy_run_root.parent / "outside_policy.json"
        if target_kind == "outside_run"
        else policy_run_root / "private" / "collector" / "policy.json"
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_bytes = policy_source_path.read_bytes()
    target_path.write_bytes(target_bytes)
    original_writer = config_adapter.write_bytes_atomically

    def redirect_snapshot_before_storage(output_path: Path, content: bytes) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.symlink_to(target_path)
        original_writer(output_path, content)

    monkeypatch.setattr(
        config_adapter,
        "write_bytes_atomically",
        redirect_snapshot_before_storage,
    )

    with pytest.raises(PathValidationError):
        prepare_policy_configuration(policy_run_root)

    assert target_path.read_bytes() == target_bytes


def test_changed_policy_can_be_prepared_in_a_new_run(
    policy_source_path: Path,
    policy_run_root: Path,
) -> None:
    original = prepare_policy_configuration(policy_run_root)
    original_bytes = original.path.read_bytes()
    configuration = load_json(policy_source_path)
    configuration["limits"]["max_requests"] += 1
    policy_source_path.write_text(json.dumps(configuration), encoding="utf-8")
    new_run_root = policy_run_root.with_name("run_new_policy")
    new_run_root.mkdir()

    changed = prepare_policy_configuration(new_run_root)

    assert changed.path == new_run_root / POLICY_CONFIG_RELATIVE_PATH
    assert changed.path.read_bytes() == policy_source_path.read_bytes()
    assert changed.sha256 != original.sha256
    assert original.path.read_bytes() == original_bytes


def test_prepared_policy_is_compatible_with_existing_configuration_loader(
    policy_source_path: Path,
    evaluate_arguments: tuple[dict[str, Any], str, dict[str, Any]],
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    prepared = prepare_policy_configuration(Path(context["run_root"]))

    configuration = load_policy_configuration(
        bind_policy_configuration(
            parse_evaluate_request(input_paths, output_dir, context),
            prepared,
        )
    )

    original_configuration = load_json(policy_source_path)
    assert configuration.policy_id == original_configuration["policy_id"]
    assert configuration.policy_version == original_configuration["policy_version"]
    assert configuration.max_requests == original_configuration["limits"]["max_requests"]


@pytest.mark.parametrize("target_kind", ["outside_run", "other_module"])
def test_prepare_policy_configuration_rejects_private_directory_symlink(
    policy_source_path: Path,
    policy_run_root: Path,
    target_kind: str,
) -> None:
    target_path = (
        policy_run_root.parent / "outside_run"
        if target_kind == "outside_run"
        else policy_run_root / "private" / "collector"
    )
    target_path.mkdir(parents=True)
    private_path = policy_run_root / "private"
    private_path.mkdir(exist_ok=True)
    (private_path / "safety_policy").symlink_to(target_path, target_is_directory=True)

    with pytest.raises(PathValidationError):
        prepare_policy_configuration(policy_run_root)

    assert list(target_path.iterdir()) == []


@pytest.mark.parametrize("root_kind", ["missing", "file"])
def test_prepare_policy_configuration_requires_existing_run_directory(
    policy_source_path: Path,
    policy_run_root: Path,
    root_kind: str,
) -> None:
    run_root = policy_run_root.parent / "invalid_root"
    if root_kind == "file":
        run_root.write_bytes(b"not a directory")

    with pytest.raises(PathValidationError):
        prepare_policy_configuration(run_root)


def test_prepare_policy_configuration_rejects_published_hash_mismatch(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_adapter, "calculate_sha256", lambda path: "0" * 64)

    with pytest.raises(PolicyConfigHashMismatchError, match="원본 바이트와 다름"):
        prepare_policy_configuration(policy_run_root)


def test_prepare_policy_configuration_stores_the_validated_read_once_bytes(
    policy_source_path: Path,
    policy_run_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_bytes = policy_source_path.read_bytes()
    original_sha256 = calculate_sha256(policy_source_path)
    original_writer = config_adapter.write_bytes_atomically

    def change_source_before_storage(output_path: Path, content: bytes) -> None:
        policy_source_path.write_bytes(b"invalid replacement")
        original_writer(output_path, content)

    monkeypatch.setattr(config_adapter, "write_bytes_atomically", change_source_before_storage)

    prepared = prepare_policy_configuration(policy_run_root)

    assert prepared.path.read_bytes() == original_bytes
    assert prepared.sha256 == original_sha256
