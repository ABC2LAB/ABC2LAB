"""Run-specific identity mapping without changing the Ground Truth contract."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from modules.reporter.contracts import prepare_evaluation_inputs
from modules.reporter.entrypoint import run
from modules.reporter.evaluation_config import (
    EVALUATION_CONFIG_PATH_ENV,
    EVALUATION_CONFIG_SCHEMA,
    load_evaluation_settings,
)
from modules.reporter.evaluation_output_adapter import (
    EVALUATION_RESULTS_SCHEMA,
    build_evaluation_artifact,
    publish_evaluation_artifact,
)
from modules.reporter.evaluation_service import build_evaluation_results
from modules.reporter.exceptions import ContractValidationError, PathValidationError
from modules.reporter.input_adapter import parse_evaluate_request
from modules.reporter.matching import ActorMatcher
from modules.reporter.models import ActorBinding, EvaluationInputs, EvaluationSettings
from modules.reporter.service import build_diagnosis_report
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.validation import load_json, validate_schema

Arguments = tuple[dict[str, Any], str, dict[str, Any]]
SETTINGS_PATH = "configs/evaluation.json"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EVALUATION_CONFIG_PATH_ENV, raising=False)


@pytest.fixture
def evaluation_inputs(evaluate_arguments: Arguments) -> EvaluationInputs:
    return prepare_evaluation_inputs(parse_evaluate_request(*evaluate_arguments))


def _settings_value(inputs: EvaluationInputs) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "dataset_id": inputs.ground_truth.dataset_id,
        "dataset_version": inputs.ground_truth.dataset_version,
        "actor_bindings": [{"actor_alias": "logical_actor", "account_id": "account_user_a"}],
    }


def _write_settings(inputs: EvaluationInputs, value: dict[str, Any]) -> Path:
    assert inputs.request.project_root is not None
    path = inputs.request.project_root / SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _evaluate(inputs: EvaluationInputs) -> dict[str, Any]:
    result = build_evaluation_results(inputs)
    assert result.status == "completed"
    assert result.data is not None
    return result.data.to_mapping()


def _metric(data: dict[str, Any], metric_id: str) -> dict[str, Any]:
    return next(item for item in data["metrics"] if item["metric_id"] == metric_id)


def test_candidate_binding_matches_logical_bob_to_execution_account(
    evaluation_inputs: EvaluationInputs,
) -> None:
    candidate = replace(
        evaluation_inputs.report_inputs.candidates[0], actor_account_id="account:user_b",
    )
    case = {**evaluation_inputs.ground_truth.cases[0], "case_id": "V1", "actor_alias": "bob",
            "resource_match_key": {"name": "order"}}
    crawl = deepcopy(evaluation_inputs.crawl_result.value)
    crawl["data"]["accounts"] = [{"account_id": "account:user_b", "alias": "user_b"}]
    changed = replace(
        evaluation_inputs,
        crawl_result=replace(evaluation_inputs.crawl_result, value=crawl),
        ground_truth=replace(evaluation_inputs.ground_truth, cases=(case,)),
        report_inputs=replace(evaluation_inputs.report_inputs, candidates=(candidate,)),
        settings=EvaluationSettings((ActorBinding("bob", "account:user_b"),)),
    )

    data = _evaluate(changed)

    assert data["candidate_counts"] == {"tp": 1, "fp": 0, "fn": 0}
    assert _metric(data, "candidate_precision")["value"] == 1.0


@pytest.mark.parametrize("direction", ["source", "target"])
def test_user_and_relationship_recall_share_the_account_binding(
    evaluation_inputs: EvaluationInputs, direction: str,
) -> None:
    snapshot = evaluation_inputs.graph_snapshot
    assert snapshot is not None
    user = next(node for node in snapshot.nodes if node["node_type"] == "User")
    resource = next(node for node in snapshot.nodes if node["node_type"] == "Resource")
    observed_user = {**user, "properties": {
        **user["properties"], "account_id": "account:user_a", "alias": "user_a",
    }}
    expected_user = {"gt_id": "e_user_alice", "entity_type": "User", "match_key": {"alias": "alice"}}
    expected_resource = {"gt_id": "e_res_order", "entity_type": "Resource", "match_key": {"name": "order"}}
    source, target = ((expected_user, expected_resource) if direction == "source"
                      else (expected_resource, expected_user))
    source_id, target_id = ((user["node_id"], resource["node_id"]) if direction == "source"
                            else (resource["node_id"], user["node_id"]))
    changed = replace(
        evaluation_inputs,
        ground_truth=replace(evaluation_inputs.ground_truth, entities=(expected_user, expected_resource),
            relationships=({"gt_relation_id": "owns", "source_gt_id": source["gt_id"],
                            "target_gt_id": target["gt_id"], "relation_type": "OWNS"},)),
        graph_snapshot=replace(snapshot, nodes=(observed_user, resource), relationships=({
            "relationship_id": "opaque-owns", "source_id": source_id, "target_id": target_id,
            "relation_type": "OWNS", "properties": {}, "basis": "observed", "evidence_refs": [],
        },)),
        settings=EvaluationSettings((ActorBinding("alice", "account:user_a"),)),
    )
    original = deepcopy(changed)

    data = _evaluate(changed)

    assert _metric(data, "user_recall") == {
        "metric_id": "user_recall", "numerator": 1, "denominator": 1, "value": 1.0,
    }
    assert _metric(data, "relationship_recall")["value"] == 1.0
    assert changed == original


def test_legacy_alias_matching_is_preserved(evaluation_inputs: EvaluationInputs) -> None:
    data = _evaluate(evaluation_inputs)
    assert evaluation_inputs.settings.actor_bindings == ()
    assert "평가 계정 매핑 설정 없음: 기존 alias 기준으로 비교함" in data["notes"]
    assert data["candidate_counts"] == {"tp": 1, "fp": 2, "fn": 0}
    assert data["confirmed_counts"] == {"tp": 1, "fp": 0, "fn": 0}
    matcher = ActorMatcher({}, {"legacy-id": "ALICE"})
    assert matcher.matches_actor("alice", "legacy-id")
    assert matcher.matches_entity("User", {"alias": "alice"}, {"alias": "ALICE"})


@pytest.mark.parametrize("account_id", ["wrong-account", "ACCOUNT:user_a", "account:user_a "])
def test_explicit_binding_never_falls_back_to_alias(account_id: str) -> None:
    matcher = ActorMatcher({"alice": "account:user_a"}, {account_id: "alice"})
    assert not matcher.matches_actor("alice", account_id)
    assert not matcher.matches_entity("User", {"alias": "alice"}, {
        "account_id": account_id, "alias": "alice",
    })


def test_user_binding_preserves_other_match_constraints() -> None:
    matcher = ActorMatcher({"alice": "account:user_a"}, {})
    observed = {"account_id": "account:user_a", "alias": "user_a", "role_id": "role_user"}
    assert matcher.matches_entity("User", {"alias": "alice", "role_id": "role_user"}, observed)
    assert not matcher.matches_entity("User", {"alias": "alice", "role_id": "role_admin"}, observed)
    assert not matcher.matches_entity("User", {"alias": "alice", "account_id": "other"}, observed)
    assert not matcher.matches_entity("User", {"alias": "alice"}, {"alias": "alice"})
    # A partially supplied mapping does not guess another actor's identity.
    assert not matcher.matches_entity("User", {"alias": "bob"}, observed)


@pytest.mark.parametrize("expected,observed", [("user", "id"), ("product", "products"), ("order", "orders")])
def test_binding_does_not_correct_upstream_resource_meaning(expected: str, observed: str) -> None:
    matcher = ActorMatcher({"alice": "account:user_a"}, {})
    assert not matcher.matches_entity("Resource", {"name": expected}, {
        "resource_key": observed, "resource_scope": "type", "match_key": None,
    })


def test_non_user_matching_is_unchanged() -> None:
    matcher = ActorMatcher({"alice": "account:user_a"}, {})
    assert matcher.matches_entity("Role", {"name": "ADMIN"}, {"name": "admin"})
    assert matcher.matches_entity("Endpoint", {"method": "get", "path_template": "/api/"},
                                  {"method": "GET", "path_template": "/api"})


def test_settings_are_optional_and_do_not_require_all_expected_accounts(
    evaluation_inputs: EvaluationInputs, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load_evaluation_settings(None, evaluation_inputs.ground_truth) == EvaluationSettings()
    value = _settings_value(evaluation_inputs)
    value["actor_bindings"][0]["account_id"] = "not-observed-account"
    path = _write_settings(evaluation_inputs, value)
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, SETTINGS_PATH)
    settings = load_evaluation_settings(evaluation_inputs.request.project_root, evaluation_inputs.ground_truth)
    assert settings.actor_bindings == (ActorBinding("logical_actor", "not-observed-account"),)
    assert settings.sha256 == calculate_sha256(path)


@pytest.mark.parametrize("mutation", [
    "dataset_id", "dataset_version", "version", "duplicate_alias", "duplicate_account",
    "password", "cookie", "session", "extra_field", "blank_alias", "blank_account", "wrong_type",
])
def test_invalid_settings_are_rejected(
    evaluation_inputs: EvaluationInputs, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    value = _settings_value(evaluation_inputs)
    binding = value["actor_bindings"][0]
    if mutation in {"dataset_id", "dataset_version"}:
        value[mutation] = "wrong"
    elif mutation == "version":
        value["schema_version"] = "0.2.0"
    elif mutation == "duplicate_alias":
        value["actor_bindings"].append({"actor_alias": " LOGICAL_ACTOR ", "account_id": "other"})
    elif mutation == "duplicate_account":
        value["actor_bindings"].append({"actor_alias": "other", "account_id": binding["account_id"]})
    elif mutation in {"blank_alias", "blank_account"}:
        binding["actor_alias" if mutation == "blank_alias" else "account_id"] = "   "
    elif mutation == "wrong_type":
        binding["account_id"] = 42
    elif mutation == "extra_field":
        value["unexpected"] = True
    else:
        binding[mutation] = "forbidden-test-value"
    _write_settings(evaluation_inputs, value)
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, SETTINGS_PATH)
    with pytest.raises(ContractValidationError):
        prepare_evaluation_inputs(evaluation_inputs.request)


@pytest.mark.parametrize("relative_path", ["", "/tmp/settings.json", "../settings.json", "missing.json"])
def test_settings_paths_stay_inside_project_root(
    evaluation_inputs: EvaluationInputs, monkeypatch: pytest.MonkeyPatch, relative_path: str,
) -> None:
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, relative_path)
    with pytest.raises(PathValidationError):
        load_evaluation_settings(evaluation_inputs.request.project_root, evaluation_inputs.ground_truth)


def test_settings_reject_symlink_escape(
    evaluation_inputs: EvaluationInputs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    external = tmp_path / "external.json"
    external.write_text(json.dumps(_settings_value(evaluation_inputs)), encoding="utf-8")
    path = _write_settings(evaluation_inputs, _settings_value(evaluation_inputs))
    path.unlink()
    path.symlink_to(external)
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, SETTINGS_PATH)
    with pytest.raises(PathValidationError):
        prepare_evaluation_inputs(evaluation_inputs.request)


def test_public_evaluate_keeps_gt_contract_and_records_settings_hash(
    evaluation_inputs: EvaluationInputs, evaluate_arguments: Arguments, monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_paths, output_dir, context = evaluate_arguments
    settings_path = _write_settings(evaluation_inputs, _settings_value(evaluation_inputs))
    gt_path = context["project_root"] / input_paths["ground_truth"]["path"]
    gt = load_json(gt_path)
    gt["data"]["cases"][0]["actor_alias"] = "logical_actor"
    gt_path.write_text(json.dumps(gt), encoding="utf-8")
    input_paths["ground_truth"]["sha256"] = calculate_sha256(gt_path)
    original_hashes = {name: descriptor["sha256"] for name, descriptor in input_paths.items()}
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, SETTINGS_PATH)
    output_path = context["run_root"] / output_dir / "evaluation_results.json"
    output_path.unlink()

    response = run("evaluate", input_paths, output_dir, context)

    assert response["status"] == "completed"
    artifact = load_json(output_path)
    validate_schema(artifact, EVALUATION_RESULTS_SCHEMA)
    assert artifact["schema_version"] == "0.3.0"
    assert artifact["data"]["candidate_counts"] == {"tp": 1, "fp": 2, "fn": 0}
    assert artifact["data"]["confirmed_counts"] == {"tp": 1, "fp": 0, "fn": 0}
    assert any(calculate_sha256(settings_path) in note for note in artifact["data"]["notes"])
    assert gt["schema_version"] == "0.1.0" and "actor_bindings" not in gt["data"]
    for name, descriptor in input_paths.items():
        root = context["project_root"] if name == "ground_truth" else context["run_root"]
        assert calculate_sha256(root / descriptor["path"]) == original_hashes[name]


def test_settings_changed_before_publication_are_rejected(
    evaluation_inputs: EvaluationInputs, monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _settings_value(evaluation_inputs)
    path = _write_settings(evaluation_inputs, value)
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, SETTINGS_PATH)
    prepared = prepare_evaluation_inputs(evaluation_inputs.request)
    artifact = build_evaluation_artifact(prepared, perf_counter())
    value["actor_bindings"] = []
    path.write_text(json.dumps(value), encoding="utf-8")
    prepared.request.output_path.unlink()
    with pytest.raises(ContractValidationError, match="계정 매핑 설정 파일이 변경됨"):
        publish_evaluation_artifact(prepared, artifact)
    assert not prepared.request.output_path.exists()


def test_settings_are_pinned_after_preparation(
    evaluation_inputs: EvaluationInputs, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_settings(evaluation_inputs, _settings_value(evaluation_inputs))
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, SETTINGS_PATH)
    prepared = prepare_evaluation_inputs(evaluation_inputs.request)
    artifact = build_evaluation_artifact(prepared, perf_counter())
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, "missing-settings.json")
    prepared.request.output_path.unlink()
    response = publish_evaluation_artifact(prepared, artifact)
    assert response.status == "completed"
    assert prepared.settings.sha256 == calculate_sha256(path)


def test_settings_cannot_escape_project_root_before_publication(
    evaluation_inputs: EvaluationInputs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    path = _write_settings(evaluation_inputs, _settings_value(evaluation_inputs))
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, SETTINGS_PATH)
    prepared = prepare_evaluation_inputs(evaluation_inputs.request)
    artifact = build_evaluation_artifact(prepared, perf_counter())
    external = tmp_path / "outside-project.json"
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)
    prepared.request.output_path.unlink()
    with pytest.raises(PathValidationError):
        publish_evaluation_artifact(prepared, artifact)
    assert not prepared.request.output_path.exists()


def test_settings_reject_duplicate_json_keys_and_malformed_json(
    evaluation_inputs: EvaluationInputs, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_settings(evaluation_inputs, _settings_value(evaluation_inputs))
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, SETTINGS_PATH)
    for contents in ('{"actor_bindings": [], "actor_bindings": []}', '{malformed'):
        path.write_text(contents, encoding="utf-8")
        with pytest.raises(ContractValidationError):
            prepare_evaluation_inputs(evaluation_inputs.request)


def test_report_does_not_load_evaluation_configuration(
    evaluation_inputs: EvaluationInputs, report_arguments: Arguments, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = build_diagnosis_report(evaluation_inputs.report_inputs)
    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, "/missing/forbidden.json")
    input_paths, output_dir, context = report_arguments
    input_paths = {name: descriptor for name, descriptor in input_paths.items()
                   if name in {"vulnerability_candidates", "test_scenarios", "safety_decisions", "verification_results"}}
    context = {name: value for name, value in context.items() if name not in {"project_root", "matching_profile"}}
    context["mode"] = "diagnosis"
    for name, descriptor in input_paths.items():
        path = context["run_root"] / descriptor["path"]
        artifact = load_json(path)
        artifact["mode"] = "diagnosis"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        descriptor["sha256"] = calculate_sha256(path)
    # Scenario bytes changed with mode, so maintain the existing exact plan hash.
    for name in ("safety_decisions", "verification_results"):
        descriptor = input_paths[name]
        path = context["run_root"] / descriptor["path"]
        artifact = load_json(path)
        artifact["data"]["scenarios_sha256"] = input_paths["test_scenarios"]["sha256"]
        path.write_text(json.dumps(artifact), encoding="utf-8")
        descriptor["sha256"] = calculate_sha256(path)
    output_path = context["run_root"] / output_dir / "diagnosis_report.json"
    output_path.unlink()
    response = run("report", input_paths, output_dir, context)
    assert response["status"] == "completed"
    assert baseline.data is not None
    assert load_json(output_path)["data"]["summary"] == baseline.data.summary.to_mapping()


@pytest.mark.parametrize("filename", ["shop_demo.evaluation.example.json", "shop_demo.evaluation.json"])
def test_supplied_settings_satisfy_own_schema(filename: str) -> None:
    path = Path(__file__).parents[1] / "configs" / filename
    validate_schema(load_json(path), EVALUATION_CONFIG_SCHEMA)


def test_shop_demo_profile_loads_explicitly_for_its_dataset(
    evaluation_inputs: EvaluationInputs, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = Path(__file__).resolve().parents[3]
    relative_path = "modules/reporter/configs/shop_demo.evaluation.json"
    ground_truth = replace(evaluation_inputs.ground_truth, dataset_id="shop_demo", dataset_version="1.0.0")
    assert load_evaluation_settings(project_root, ground_truth) == EvaluationSettings()

    monkeypatch.setenv(EVALUATION_CONFIG_PATH_ENV, relative_path)
    settings = load_evaluation_settings(project_root, ground_truth)

    assert settings.actor_bindings == (
        ActorBinding("alice", "account:user_a"), ActorBinding("bob", "account:user_b"),
        ActorBinding("admin", "account:admin_a"), ActorBinding("guest", "account:guest"),
    )
    assert settings.relative_path == relative_path
    assert settings.sha256 == calculate_sha256(project_root / relative_path)
    with pytest.raises(ContractValidationError):
        load_evaluation_settings(project_root, replace(ground_truth, dataset_id="another_dataset"))
