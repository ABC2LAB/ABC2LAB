"""JSON Schema 검증 + 참조 무결성 검사. 모듈 자체 구현(중앙 검증기 금지)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"
_INPUT_SCHEMA = _SCHEMAS_DIR / "input" / "crawl_result.schema.json"
_OUTPUT_SCHEMA = _SCHEMAS_DIR / "output" / "semantic_analysis.schema.json"


@lru_cache(maxsize=None)
def _validator(schema_path: Path) -> Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _schema_errors(instance: dict, schema_path: Path) -> list[str]:
    validator = _validator(schema_path)
    messages: list[str] = []
    for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
        location = "/".join(str(part) for part in error.path) or "<root>"
        messages.append(f"{location}: {error.message}")
    return messages


def validate_crawl_result(instance: dict) -> list[str]:
    """입력 crawl_result를 소비자 측 Schema로 검증한다. 빈 리스트면 통과."""
    return _schema_errors(instance, _INPUT_SCHEMA)


def validate_semantic_analysis(instance: dict) -> list[str]:
    """출력 semantic_analysis를 자기 Schema로 검증한다. 빈 리스트면 통과."""
    return _schema_errors(instance, _OUTPUT_SCHEMA)


def check_referential_integrity(data: dict) -> list[str]:
    """출력 data 안의 ID 참조가 전부 선언된 노드·단계를 가리키는지 확인한다.

    없는 ID를 가리키면 조용히 통과하지 않고 오류로 돌려준다(절대 규칙 4: 없는 ID는 버린다/기록).
    """
    errors: list[str] = []
    node_ids = {node["node_id"] for node in data["nodes"]}
    resource_ids = {n["node_id"] for n in data["nodes"] if n["node_type"] == "Resource"}
    endpoint_ids = {n["node_id"] for n in data["nodes"] if n["node_type"] == "Endpoint"}

    for edge in data["relationships"]:
        if edge["source_id"] not in node_ids:
            errors.append(f"관계 {edge['relationship_id']}: source_id 미선언 → {edge['source_id']}")
        if edge["target_id"] not in node_ids:
            errors.append(f"관계 {edge['relationship_id']}: target_id 미선언 → {edge['target_id']}")

    for request in data["normalized_requests"]:
        if request["endpoint_id"] not in endpoint_ids:
            errors.append(
                f"정규화요청 {request['request_id']}: endpoint_id 미선언 → {request['endpoint_id']}"
            )
        for resource_id in request["resource_ids"]:
            if resource_id not in resource_ids:
                errors.append(
                    f"정규화요청 {request['request_id']}: resource_id 미선언 → {resource_id}"
                )

    for workflow in data["workflows"]:
        step_ids = {step["step_id"] for step in workflow["steps"]}
        for dependency in workflow["dependencies"]:
            for key in ("before_step_id", "after_step_id"):
                if dependency[key] not in step_ids:
                    errors.append(
                        f"업무흐름 {workflow['workflow_id']}: {key} 미선언 → {dependency[key]}"
                    )
    return errors
