"""공개 산출물을 최소 화면 모델로 변환한다. 진단·정책 판단은 수행하지 않는다."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_VIEW_ITEMS = 2000
MAX_TEXT_LENGTH = 8192
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
BASIS_VALUES = {"observed", "inferred", "verified"}
NODE_TYPES = {"User", "Role", "Page", "Action", "Endpoint", "Parameter", "Resource"}
RELATION_TYPES = {"HAS_ROLE", "ACCESS", "CALL", "USE", "REFERENCE", "OWNS", "PRECEDES",
                  "REQUIRES", "VERIFIED_ACCESS", "VERIFIED_DENIAL"}
PUBLIC_FILES = {
    "crawl_result": "collector/crawl_result.json",
    "semantic_analysis": "semantic_analyzer/semantic_analysis.json",
    "graph_query_result": "knowledge_graph/graph_query_result.json",
    "vulnerability_candidates": "access_analyzer/vulnerability_candidates.json",
    "test_scenarios": "scenario_generator/test_scenarios.json",
    "safety_decisions": "safety_policy/safety_decisions.json",
    "verification_results": "verifier/verification_results.json",
    "diagnosis_report": "reporter/diagnosis_report.json",
    "evaluation_results": "reporter/evaluation_results.json",
}
ENVELOPE_FIELDS = {"schema_version", "artifact_type", "artifact_id", "run_id", "iteration",
                   "producer", "mode", "created_at", "status", "input_refs", "errors",
                   "runtime_metrics", "data"}
REPORT_COUNTS = ("candidate_count", "confirmed_count", "not_confirmed_count", "suspected_count",
                 "indeterminate_count", "policy_blocked_count", "approval_pending_count")


class ArtifactError(ValueError):
    def __init__(self, code: str = "ARTIFACT_INVALID") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LoadedArtifact:
    payload: dict[str, object]
    sha256: str


def object_fields(value: object, names: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ArtifactError()
    return value


def text_value(value: object, choices: set[str] | None = None) -> str:
    if (not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH
            or any(ord(character) < 32 and character not in "\n\t\r" for character in value)
            or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
            or (choices is not None and value not in choices)):
        raise ArtifactError()
    return value


def identifier(value: object) -> str:
    result = text_value(value)
    if not result.strip():
        raise ArtifactError()
    return result


def integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ArtifactError()
    return value


def items(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ArtifactError()
    if len(value) > MAX_VIEW_ITEMS:
        raise ArtifactError("ARTIFACT_VIEW_LIMIT")
    return value


def identifiers(value: object) -> list[str]:
    return [identifier(item) for item in items(value)]


def nullable_identifier(value: object) -> str | None:
    return None if value is None else identifier(value)


def records(value: object, key: str) -> list[dict[str, object]]:
    result, seen = [], set()
    for row in items(value):
        if not isinstance(row, dict):
            raise ArtifactError()
        row_id = identifier(row.get(key))
        if row_id in seen:
            raise ArtifactError()
        seen.add(row_id)
        result.append(row)
    return result


def reference_path(value: object, prefix: str) -> str:
    raw = identifier(value)
    path = PurePosixPath(raw)
    if ("\\" in raw or path.is_absolute() or str(path) != raw
            or ".." in path.parts or not raw.startswith(prefix)):
        raise ArtifactError()
    return raw


def digest_value(value: object) -> str:
    result = text_value(value)
    if not HASH_PATTERN.fullmatch(result):
        raise ArtifactError()
    return result


def evidence(value: object) -> list[dict[str, object]]:
    result = []
    for row in records(value, "evidence_id"):
        object_fields(row, "evidence_id kind path sha256 redacted")
        reference_path(row["path"], "evidence/")
        if row["redacted"] is not True:
            raise ArtifactError("UNSAFE_EVIDENCE_REFERENCE")
        result.append({"evidence_id": identifier(row["evidence_id"]),
                       "kind": text_value(row["kind"], {"request", "response", "dom", "screenshot", "state", "execution_log"}),
                       "sha256": digest_value(row["sha256"]), "redacted": True})
    # 원문 파일·경로·본문을 UI로 보내거나 열지 않는다.
    return result


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError()
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ArtifactError()


def finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ArtifactError()
    return result


def read_bytes(run_root: Path, suffix: str) -> bytes:
    """각 경로 요소를 dir_fd/O_NOFOLLOW로 열어 symlink 교체도 따라가지 않는다."""
    if not run_root.is_absolute():
        raise ArtifactError("ARTIFACT_UNAVAILABLE")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in (*run_root.parts[1:], "artifacts", "iteration-000", *PurePosixPath(suffix).parts[:-1]):
            next_descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        file_descriptor = os.open(PurePosixPath(suffix).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ArtifactError("ARTIFACT_UNAVAILABLE")
            body = source.read(MAX_ARTIFACT_BYTES + 1)
        if len(body) > MAX_ARTIFACT_BYTES:
            raise ArtifactError("ARTIFACT_TOO_LARGE")
        return body
    finally:
        os.close(descriptor)


def validate_envelope(raw: object, name: str, run_id: str) -> dict[str, object]:
    value = object_fields(raw, " ".join(ENVELOPE_FIELDS))
    if (value["schema_version"] != "0.3.0" or value["artifact_type"] != name
            or value["run_id"] != run_id or type(value["iteration"]) is not int
            or value["iteration"] != 0 or value["mode"] != "development"
            or value["producer"] != PUBLIC_FILES[name].split("/")[0]):
        raise ArtifactError("ARTIFACT_IDENTITY_MISMATCH")
    identifier(value["artifact_id"])
    timestamp = datetime.fromisoformat(text_value(value["created_at"]))
    if timestamp.utcoffset() != timedelta(0):
        raise ArtifactError()
    status = text_value(value["status"], {"completed", "partial", "failed"})
    errors = items(value["errors"])
    if (status == "completed") != (not errors) or (status == "failed") != (value["data"] is None):
        raise ArtifactError()
    if status != "failed" and not isinstance(value["data"], dict):
        raise ArtifactError()
    for error in errors:
        object_fields(error, "code message item_ref retryable")
    for reference in items(value["input_refs"]):
        object_fields(reference, "artifact_id artifact_type iteration path sha256")
        identifier(reference["artifact_id"])
        identifier(reference["artifact_type"])
        integer(reference["iteration"])
        reference_path(reference["path"], "artifacts/")
        digest_value(reference["sha256"])
    return value


def load_artifact(run_root: Path, name: str, expected_hash: str | None) -> LoadedArtifact:
    body = read_bytes(run_root, PUBLIC_FILES[name])
    digest = hashlib.sha256(body).hexdigest()
    if expected_hash is not None and digest != expected_hash:
        raise ArtifactError("ARTIFACT_HASH_MISMATCH")
    raw = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object,
                     parse_constant=reject_constant, parse_float=finite_float)
    return LoadedArtifact(validate_envelope(raw, name, run_root.name), digest)


def crawl_view(data: object) -> dict[str, object]:
    # crawl_result 0.3.0: discovered_endpoints(발견만 한 엔드포인트)가 필수 키로 추가됐다. 화면 집계는 그대로다.
    value = object_fields(data, "target_url roles accounts pages actions requests discovered_endpoints")
    counts = {name: len(records(value[name], key)) for name, key in (
        ("roles", "role_id"), ("accounts", "account_id"), ("pages", "page_id"),
        ("actions", "action_id"), ("requests", "request_id"))}
    return {"counts": counts}


def graph_nodes(value: object) -> list[dict[str, object]]:
    result = []
    for row in records(value, "node_id"):
        object_fields(row, "node_id node_type properties basis evidence_refs")
        node_type = text_value(row["node_type"], NODE_TYPES)
        properties = row["properties"]
        if not isinstance(properties, dict):
            raise ArtifactError()
        scope = text_value(properties.get("resource_scope"), {"type", "instance"}) if node_type == "Resource" else None
        result.append({"node_id": identifier(row["node_id"]), "node_type": node_type,
                       "basis": text_value(row["basis"], BASIS_VALUES), "resource_scope": scope})
    return result


def graph_edges(value: object, nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    result, node_ids = [], {row["node_id"] for row in nodes}
    for row in records(value, "relationship_id"):
        object_fields(row, "relationship_id source_id target_id relation_type properties basis evidence_refs")
        source, target = identifier(row["source_id"]), identifier(row["target_id"])
        if source not in node_ids or target not in node_ids:
            raise ArtifactError("GRAPH_REFERENCE_INVALID")
        result.append({"relationship_id": identifier(row["relationship_id"]), "source_id": source,
                       "target_id": target, "relation_type": text_value(row["relation_type"], RELATION_TYPES),
                       "basis": text_value(row["basis"], BASIS_VALUES)})
    return result


def workflows(value: object) -> list[dict[str, object]]:
    result = []
    for row in records(value, "workflow_id"):
        object_fields(row, "workflow_id name role_ids steps dependencies basis evidence_refs")
        steps = []
        orders = set()
        for step in records(row["steps"], "step_id"):
            object_fields(step, "step_id order action request_ids")
            order = integer(step["order"])
            if order in orders:
                raise ArtifactError()
            orders.add(order)
            steps.append({"step_id": identifier(step["step_id"]), "order": order,
                          "action": text_value(step["action"])})
        result.append({"workflow_id": identifier(row["workflow_id"]), "name": text_value(row["name"]),
                       "basis": text_value(row["basis"], BASIS_VALUES),
                       "steps": sorted(steps, key=lambda step: step["order"]),
                       "dependency_count": len(items(row["dependencies"]))})
    return result


def semantic_view(data: object) -> dict[str, object]:
    value = object_fields(data, "normalized_requests nodes relationships workflows model_info")
    nodes = graph_nodes(value["nodes"])
    return {"counts": {"normalized_requests": len(records(value["normalized_requests"], "request_id")),
                       "nodes": len(nodes), "relationships": len(graph_edges(value["relationships"], nodes)),
                       "workflows": len(workflows(value["workflows"]))},
            "node_types": dict(Counter(row["node_type"] for row in nodes))}


def snapshot_view(data: object) -> dict[str, object]:
    value = object_fields(data, "graph_id graph_revision results")
    snapshots = []
    for row in records(value["results"], "query_id"):
        object_fields(row, "query_id query_key status rows errors")
        text_value(row["query_key"], {"structure_snapshot", "resource_ownership", "role_resource_access", "workflow_dependencies"})
        text_value(row["status"], {"completed", "failed"})
        if row["query_key"] == "structure_snapshot":
            snapshots.append(row)
    if len(snapshots) > 1:
        raise ArtifactError("GRAPH_REFERENCE_INVALID")
    result = {"graph_id": identifier(value["graph_id"]), "graph_revision": integer(value["graph_revision"]),
              "snapshot_state": "missing", "nodes": None, "relationships": None, "workflows": None}
    if not snapshots:
        return result
    snapshot = snapshots[0]
    result["snapshot_state"] = snapshot["status"]
    rows = items(snapshot["rows"])
    if snapshot["status"] == "failed":
        if rows or not items(snapshot["errors"]):
            raise ArtifactError()
        return result
    if len(rows) != 1 or items(snapshot["errors"]):
        raise ArtifactError()
    payload = object_fields(rows[0], "nodes relationships workflows")
    nodes = graph_nodes(payload["nodes"])
    result.update({"nodes": nodes, "relationships": graph_edges(payload["relationships"], nodes),
                   "workflows": workflows(payload["workflows"])})
    return result


def candidates_view(data: object) -> dict[str, object]:
    value = object_fields(data, "source_graph_revision candidates model_info")
    result = []
    for row in records(value["candidates"], "candidate_id"):
        object_fields(row, "candidate_id category vulnerability_type rule_id actor_account_id actor_role_id reference_account_id resource_ids source_request_ids workflow_id hypothesis expected_behavior expected_basis evidence_refs")
        result.append({"candidate_id": identifier(row["candidate_id"]),
                       "category": text_value(row["category"], {"authorization", "business_logic"}),
                       "vulnerability_type": text_value(row["vulnerability_type"]),
                       "actor_account_id": identifier(row["actor_account_id"]),
                       "actor_role_id": identifier(row["actor_role_id"]),
                       "resource_ids": identifiers(row["resource_ids"]),
                       "hypothesis": text_value(row["hypothesis"]),
                       "expected_behavior": text_value(row["expected_behavior"]),
                       "expected_basis": text_value(row["expected_basis"], {"rule", "inferred", "unknown"}),
                       "evidence_refs": evidence(row["evidence_refs"])})
        if not result[-1]["resource_ids"] or not identifiers(row["source_request_ids"]):
            raise ArtifactError()
    return {"source_graph_revision": integer(value["source_graph_revision"]), "candidates": result}


def scenarios_view(data: object) -> dict[str, object]:
    value = object_fields(data, "scenarios model_info")
    result = []
    for row in records(value["scenarios"], "scenario_id"):
        object_fields(row, "scenario_id candidate_id expected_basis resource_ids preconditions steps assertions")
        steps, orders = [], set()
        for step in records(row["steps"], "step_id"):
            object_fields(step, "step_id order source_request_id account_id role_id session_ref request bindings state_change")
            request = object_fields(step["request"], "method url_template parameters body_ref")
            order = integer(step["order"])
            if order in orders:
                raise ArtifactError()
            orders.add(order)
            steps.append({"step_id": identifier(step["step_id"]), "order": order,
                          "source_request_id": identifier(step["source_request_id"]),
                          "account_id": identifier(step["account_id"]), "role_id": identifier(step["role_id"]),
                          "method": text_value(request["method"]),
                          "state_change": text_value(step["state_change"], {"none", "possible", "expected", "unknown"})})
        result.append({"scenario_id": identifier(row["scenario_id"]), "candidate_id": identifier(row["candidate_id"]),
                       "resource_ids": identifiers(row["resource_ids"]), "steps": sorted(steps, key=lambda step: step["order"]),
                       "precondition_count": len(items(row["preconditions"])), "assertion_count": len(items(row["assertions"]))})
        if not steps or not result[-1]["resource_ids"] or not result[-1]["assertion_count"]:
            raise ArtifactError()
    return {"scenarios": result}


def decisions_view(data: object) -> dict[str, object]:
    value = object_fields(data, "policy_id policy_version scenarios_sha256 decisions")
    result = []
    for row in records(value["decisions"], "decision_id"):
        object_fields(row, "decision_id scenario_id decision reason_codes reason assessment effective_origins effective_account_ids limits approval_ref")
        result.append({"decision_id": identifier(row["decision_id"]), "scenario_id": identifier(row["scenario_id"]),
                       "decision": text_value(row["decision"], {"allow", "block", "require_approval"}),
                       "reason_codes": identifiers(row["reason_codes"]), "reason": text_value(row["reason"])})
    return {"policy_id": identifier(value["policy_id"]), "policy_version": identifier(value["policy_version"]),
            "scenarios_sha256": digest_value(value["scenarios_sha256"]), "decisions": result}


def verification_view(data: object) -> dict[str, object]:
    value = object_fields(data, "source_graph_revision scenarios_sha256 results graph_updates")
    result = []
    for row in records(value["results"], "verification_id"):
        object_fields(row, "verification_id candidate_id scenario_id decision_id policy_decision execution_status result reason steps evidence_refs errors")
        steps = []
        for step in records(row["steps"], "step_id"):
            object_fields(step, "step_id status request_url response_status request_ref response_ref check_results evidence_refs errors")
            response_status = step["response_status"]
            if response_status is not None and (type(response_status) is not int or not 100 <= response_status <= 599):
                raise ArtifactError()
            steps.append({"step_id": identifier(step["step_id"]),
                          "status": text_value(step["status"], {"completed", "error", "skipped"}),
                          "response_status": response_status})
        result.append({key: identifier(row[key]) for key in ("verification_id", "candidate_id", "scenario_id", "decision_id")})
        result[-1].update({"policy_decision": text_value(row["policy_decision"], {"allow", "block", "require_approval"}),
                           "execution_status": text_value(row["execution_status"], {"completed", "not_executed", "error"}),
                           "result": text_value(row["result"], {"success", "failure", "blocked", "indeterminate"}),
                           "reason": text_value(row["reason"]), "steps": steps,
                           "error_count": len(items(row["errors"])), "evidence_refs": evidence(row["evidence_refs"])})
    return {"source_graph_revision": integer(value["source_graph_revision"]),
            "scenarios_sha256": digest_value(value["scenarios_sha256"]), "results": result}


def report_view(data: object) -> dict[str, object]:
    value = object_fields(data, "target_url summary findings limitations")
    summary = object_fields(value["summary"], " ".join(REPORT_COUNTS))
    confirmed, other, status_counts, candidate_ids = [], [], Counter(), set()
    for row in records(value["findings"], "finding_id"):
        object_fields(row, "finding_id candidate_id scenario_id verification_id category vulnerability_type status title description evidence_refs")
        status = text_value(row["status"], {"confirmed", "suspected", "not_confirmed", "indeterminate", "policy_blocked", "approval_pending"})
        finding = {"finding_id": identifier(row["finding_id"]), "candidate_id": identifier(row["candidate_id"]),
                   "scenario_id": nullable_identifier(row["scenario_id"]),
                   "verification_id": nullable_identifier(row["verification_id"]), "status": status}
        status_counts[status] += 1
        candidate_ids.add(finding["candidate_id"])
        if status == "confirmed":
            finding.update({"category": text_value(row["category"], {"authorization", "business_logic"}),
                            "vulnerability_type": text_value(row["vulnerability_type"]),
                            "title": text_value(row["title"]), "description": text_value(row["description"]),
                            "evidence_refs": evidence(row["evidence_refs"])})
            confirmed.append(finding)
        else:
            other.append(finding)
    counts = {key: integer(summary[key]) for key in REPORT_COUNTS}
    if (counts["candidate_count"] != len(candidate_ids)
            or any(counts[key] != status_counts[key.removesuffix("_count")] for key in REPORT_COUNTS if key != "candidate_count")):
        raise ArtifactError("ARTIFACT_REFERENCE_MISMATCH")
    return {"summary": counts,
            "confirmed_findings": confirmed, "other_findings": other,
            "limitations": [text_value(item) for item in items(value["limitations"])]}


def evaluation_metrics(value: object) -> list[dict[str, object]]:
    result = []
    for row in records(value, "metric_id"):
        object_fields(row, "metric_id value numerator denominator")
        numerator, denominator = integer(row["numerator"]), integer(row["denominator"])
        ratio = row["value"]
        if ratio is not None and (type(ratio) not in {int, float} or not 0 <= ratio <= 1 or denominator == 0):
            raise ArtifactError()
        # 분자/분모로 비율을 재계산하지 않고 Reporter의 값과 미측정 상태를 보존한다.
        result.append({"metric_id": identifier(row["metric_id"]), "value": ratio,
                       "numerator": numerator, "denominator": denominator})
    return result


def evaluation_models(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        raise ArtifactError()
    items(list(value))
    result = {}
    for name, row in value.items():
        object_fields(row, "model_id model_version prompt_version temperature seed")
        temperature, seed = row["temperature"], row["seed"]
        if temperature is not None and (type(temperature) not in {int, float}
                                        or (type(temperature) is float and not math.isfinite(temperature))):
            raise ArtifactError()
        if seed is not None and type(seed) is not int:
            raise ArtifactError()
        result[identifier(name)] = {key: identifier(row[key]) for key in ("model_id", "model_version", "prompt_version")}
        result[name].update({"temperature": temperature, "seed": seed})
    return result


def evaluation_runtime(value: object) -> dict[str, object]:
    keys = "duration_ms llm_calls input_tokens output_tokens peak_memory_mb"
    row = object_fields(value, keys)
    result = {name: None if row[name] is None else integer(row[name]) for name in keys.split()[:-1]}
    memory = row["peak_memory_mb"]
    if memory is not None and (type(memory) not in {int, float} or memory < 0
                               or (type(memory) is float and not math.isfinite(memory))):
        raise ArtifactError()
    result["peak_memory_mb"] = memory
    return result


def evaluation_view(data: object) -> dict[str, object]:
    value = object_fields(data, "ground_truth_ref source_graph_revision matching_profile metrics candidate_counts confirmed_counts unverified_counts models run_metrics notes")
    reference = object_fields(value["ground_truth_ref"], "dataset_id dataset_version path sha256")
    dataset_id = identifier(reference["dataset_id"])
    if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", dataset_id) is None
            or reference_path(reference["path"], "datasets/") != f"datasets/{dataset_id}/ground_truth.json"):
        raise ArtifactError("ARTIFACT_REFERENCE_MISMATCH")
    result = {"dataset": {"dataset_id": dataset_id, "dataset_version": identifier(reference["dataset_version"]),
                          "sha256": digest_value(reference["sha256"])},
              "source_graph_revision": integer(value["source_graph_revision"]),
              "matching_profile": identifier(value["matching_profile"]),
              "metrics": evaluation_metrics(value["metrics"]),
              "models": evaluation_models(value["models"]), "run_metrics": evaluation_runtime(value["run_metrics"]),
              "notes": [text_value(note) for note in items(value["notes"])]}
    for name, keys in (("candidate_counts", "tp fp fn"), ("confirmed_counts", "tp fp fn"),
                       ("unverified_counts", "policy_blocked approval_pending indeterminate")):
        counts = object_fields(value[name], keys)
        result[name] = {key: integer(counts[key]) for key in keys.split()}
    # 정답 참조의 메타데이터만 표시한다. 정답 원문·참조 경로를 열거나 응답에 넣지 않는다.
    return result


VIEW_BUILDERS = {"crawl_result": crawl_view, "semantic_analysis": semantic_view,
                 "graph_query_result": snapshot_view, "vulnerability_candidates": candidates_view,
                 "test_scenarios": scenarios_view, "safety_decisions": decisions_view,
                 "verification_results": verification_view, "diagnosis_report": report_view,
                 "evaluation_results": evaluation_view}


def check_references(loaded: LoadedArtifact, available: dict[str, LoadedArtifact]) -> None:
    for reference in loaded.payload["input_refs"]:
        upstream = available.get(reference["artifact_type"])
        if upstream is not None and (reference["artifact_id"] != upstream.payload["artifact_id"]
                                     or reference["sha256"] != upstream.sha256
                                     or reference["iteration"] != 0
                                     or reference["path"] != "artifacts/iteration-000/" + PUBLIC_FILES[reference["artifact_type"]]):
            raise ArtifactError("ARTIFACT_REFERENCE_MISMATCH")
    data = loaded.payload["data"]
    scenario = available.get("test_scenarios")
    if (loaded.payload["artifact_type"] in {"safety_decisions", "verification_results"}
            and isinstance(data, dict) and scenario is not None
            and data.get("scenarios_sha256") != scenario.sha256):
        raise ArtifactError("ARTIFACT_REFERENCE_MISMATCH")
    graph = available.get("graph_query_result")
    if (loaded.payload["artifact_type"] == "evaluation_results" and isinstance(data, dict)
            and graph is not None and isinstance(graph.payload["data"], dict)
            and data.get("source_graph_revision") != graph.payload["data"].get("graph_revision")):
        raise ArtifactError("ARTIFACT_REFERENCE_MISMATCH")


def read_results(run_root: Path, expected_hashes: dict[str, str] | None = None) -> list[dict[str, object]]:
    loaded, result = {}, []
    for name in PUBLIC_FILES:
        entry = {"artifact_type": name, "state": "missing", "error_count": None, "issue": None, "view": None}
        result.append(entry)
        try:
            loaded[name] = load_artifact(run_root, name, (expected_hashes or {}).get(name))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, UnicodeError, RecursionError) as error:
            entry.update({"state": "invalid", "issue": error.code if isinstance(error, ArtifactError) else "ARTIFACT_UNAVAILABLE"})
    for entry in result:
        name = entry["artifact_type"]
        if name not in loaded:
            continue
        source = loaded[name]
        try:
            check_references(source, loaded)
            raw = source.payload
            view = None if raw["status"] == "failed" else VIEW_BUILDERS[name](raw["data"])
            entry.update({"state": raw["status"], "artifact_id": raw["artifact_id"],
                          "sha256": source.sha256, "error_count": len(raw["errors"]), "view": view})
        except (ArtifactError, TypeError, KeyError, RecursionError) as error:
            entry.update({"state": "invalid", "issue": error.code if isinstance(error, ArtifactError) else "ARTIFACT_INVALID"})
    return result
