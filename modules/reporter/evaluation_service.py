"""Ground-truth matching and development evaluation metrics."""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from modules.reporter.exceptions import ContractValidationError
from modules.reporter.matching import (
    ActorMatcher,
    ObservedEntity,
    WORKFLOW_SUBSEQUENCE_PROFILE,
    case_signature,
    has_parameter_match_fields,
    mapping_signature,
    matches_key,
    matches_workflow,
    normalize_text,
    parameter_comparison_properties,
    require_supported_profile,
    select_workflow_match,
    workflow_actions,
    workflow_signature,
)
from modules.reporter.models import (
    Candidate,
    CountTriple,
    ErrorItem,
    EvaluationBuildResult,
    EvaluationData,
    EvaluationInputs,
    Finding,
    InputArtifact,
    Metric,
    UnverifiedCounts,
)
from modules.reporter.relationship_matching import RelationshipScore, score_relationships
from modules.reporter.service import build_diagnosis_report

ENTITY_TYPES = (
    "User",
    "Role",
    "Page",
    "Action",
    "Endpoint",
    "Parameter",
    "Resource",
)
CRAWL_ENTITY_TYPES = frozenset({"Page"})
SEMANTIC_ENTITY_TYPES = frozenset({"Endpoint", "Parameter"})
SNAPSHOT_ENTITY_TYPES = frozenset({"User", "Role", "Action", "Resource"})


@dataclass(frozen=True)
class _CaseScore:
    counts: CountTriple
    unmatched_prediction_count: int
    matched_negative_count: int


def _actor_matcher(inputs: EvaluationInputs) -> ActorMatcher:
    crawl_data = inputs.crawl_result.data
    return ActorMatcher(
        account_id_by_actor_alias={
            normalize_text(item.actor_alias): item.account_id
            for item in inputs.settings.actor_bindings
        },
        alias_by_account_id={
            item["account_id"]: item["alias"]
            for item in (crawl_data["accounts"] if crawl_data is not None else ())
        },
    )


def build_evaluation_results(inputs: EvaluationInputs) -> EvaluationBuildResult:
    profile = require_supported_profile(inputs.request.matching_profile)
    upstream_errors = _collect_upstream_errors(inputs.artifacts)
    critical_errors = _critical_input_errors(inputs)
    if critical_errors:
        return EvaluationBuildResult(
            status="failed",
            errors=(*upstream_errors, *critical_errors),
            data=None,
        )

    _validate_ground_truth_for_profile(inputs)
    report = build_diagnosis_report(inputs.report_inputs)
    if report.data is None or inputs.graph_snapshot is None:
        error = ErrorItem(
            code="EVALUATION_DATA_UNAVAILABLE",
            message="진단 분류 또는 KG snapshot을 평가에 사용할 수 없음",
            item_ref=None,
            retryable=False,
        )
        return EvaluationBuildResult(
            status="failed",
            errors=(*upstream_errors, error),
            data=None,
        )
    data = _build_evaluation_data(inputs, profile, report.data.findings)
    return EvaluationBuildResult(
        status="partial" if upstream_errors else "completed",
        errors=upstream_errors,
        data=data,
    )


def _build_evaluation_data(
    inputs: EvaluationInputs,
    profile: str,
    findings: tuple[Finding, ...],
) -> EvaluationData:
    if inputs.graph_snapshot is None:
        raise ContractValidationError("평가할 KG snapshot이 없음")
    observations, measured_types = _build_observations(inputs)
    relationship_score = score_relationships(
        inputs.graph_snapshot, inputs.ground_truth.entities,
        inputs.ground_truth.relationships, _actor_matcher(inputs),
    )
    metrics = _build_structure_metrics(inputs, observations, measured_types, relationship_score)
    candidate_score = _score_candidates(inputs, inputs.report_inputs.candidates)
    confirmed_ids = {
        item.candidate_id
        for item in findings
        if item.status == "confirmed"
    }
    confirmed_candidates = tuple(
        item
        for item in inputs.report_inputs.candidates
        if item.candidate_id in confirmed_ids
    )
    confirmed_score = _score_candidates(inputs, confirmed_candidates)
    metrics.extend(_case_metrics(candidate_score.counts, confirmed_score.counts))
    finding_counts = Counter(item.status for item in findings)
    notes = _build_notes(
        profile,
        inputs,
        candidate_score,
        confirmed_score,
        finding_counts,
    )
    notes.append(relationship_score.to_note())
    return EvaluationData(
        ground_truth=inputs.ground_truth,
        source_graph_revision=inputs.graph_snapshot.graph_revision,
        matching_profile=profile,
        metrics=tuple(metrics),
        candidate_counts=candidate_score.counts,
        confirmed_counts=confirmed_score.counts,
        unverified_counts=UnverifiedCounts(
            policy_blocked=finding_counts["policy_blocked"],
            approval_pending=finding_counts["approval_pending"],
            indeterminate=finding_counts["indeterminate"],
        ),
        models=_collect_models(inputs),
        run_metrics=_aggregate_run_metrics(inputs.artifacts),
        notes=tuple(notes),
    )


def _critical_input_errors(inputs: EvaluationInputs) -> tuple[ErrorItem, ...]:
    unavailable: list[str] = []
    if inputs.crawl_result.data is None:
        unavailable.append("crawl_result")
    if inputs.graph_snapshot is None:
        unavailable.append("graph_query_result.structure_snapshot")
    candidate_artifact = inputs.report_inputs.artifact_by_type(
        "vulnerability_candidates"
    )
    if candidate_artifact.data is None:
        unavailable.append("vulnerability_candidates")
    if not unavailable:
        return ()
    return (
        ErrorItem(
            code="EVALUATION_INPUT_UNAVAILABLE",
            message=f"필수 평가 입력을 사용할 수 없음: {', '.join(unavailable)}",
            item_ref=None,
            retryable=False,
        ),
    )


def _validate_ground_truth_for_profile(inputs: EvaluationInputs) -> None:
    entity_signatures: set[tuple[object, ...]] = set()
    for entity in inputs.ground_truth.entities:
        if not entity["match_key"]:
            raise ContractValidationError("ground truth entity match_key가 비어 있음")
        signature = (
            entity["entity_type"],
            mapping_signature(entity["match_key"], entity_type=entity["entity_type"]),
        )
        if signature in entity_signatures:
            raise ContractValidationError(
                "ground truth entity 정규화 키가 중복됨"
            )
        entity_signatures.add(signature)

    workflow_signatures: set[tuple[object, ...]] = set()
    for workflow in inputs.ground_truth.workflows:
        if not workflow["ordered_actions"]:
            raise ContractValidationError(
                "ground truth workflow ordered_actions가 비어 있음"
            )
        signature = (
            workflow_actions(workflow, is_ground_truth=True)
            if inputs.request.matching_profile == WORKFLOW_SUBSEQUENCE_PROFILE
            else workflow_signature(workflow, is_ground_truth=True)
        )
        if signature in workflow_signatures:
            raise ContractValidationError(
                "ground truth workflow 정규화 키가 중복됨"
            )
        workflow_signatures.add(signature)

    case_signatures: set[tuple[object, ...]] = set()
    for case in inputs.ground_truth.cases:
        if not case["resource_match_key"]:
            raise ContractValidationError(
                "ground truth case resource_match_key가 비어 있음"
            )
        signature = case_signature(case)
        if signature in case_signatures:
            raise ContractValidationError(
                "ground truth case 정규화 키가 중복됨"
            )
        case_signatures.add(signature)


def _build_observations(
    inputs: EvaluationInputs,
) -> tuple[dict[str, list[ObservedEntity]], dict[str, bool]]:
    observations = {entity_type: [] for entity_type in ENTITY_TYPES}
    measured_types = {entity_type: True for entity_type in ENTITY_TYPES}
    _add_crawl_observations(inputs, observations, measured_types)
    _add_semantic_observations(inputs, observations, measured_types)
    _add_snapshot_observations(inputs, observations, measured_types)
    return observations, measured_types


def _add_crawl_observations(
    inputs: EvaluationInputs,
    observations: dict[str, list[ObservedEntity]],
    measured_types: dict[str, bool],
) -> None:
    crawl_data = inputs.crawl_result.data
    if crawl_data is not None:
        for page in crawl_data["pages"]:
            observations["Page"].append(
                ObservedEntity(
                    entity_id=page["page_id"],
                    entity_type="Page",
                    properties={
                        "url": page["url"],
                        "path": page["url"],
                        "title": page["title"],
                    },
                )
            )
    else:
        for entity_type in CRAWL_ENTITY_TYPES:
            measured_types[entity_type] = False


def _add_semantic_observations(
    inputs: EvaluationInputs,
    observations: dict[str, list[ObservedEntity]],
    measured_types: dict[str, bool],
) -> None:
    semantic_data = inputs.semantic_analysis.data
    if semantic_data is not None:
        _add_endpoint_observations(semantic_data, observations)
        _add_parameter_node_observations(semantic_data, observations)
        for request in semantic_data["normalized_requests"]:
            endpoint_properties = {
                "method": request["method"],
                "path_template": request["path_template"],
            }
            for parameter in request["parameters"]:
                observations["Parameter"].append(
                    ObservedEntity(
                        entity_id=(
                            f"{request['endpoint_id']}:{parameter['parameter_id']}"
                        ),
                        entity_type="Parameter",
                        properties=parameter_comparison_properties(
                            endpoint_properties, parameter,
                        ),
                    )
                )
    else:
        for entity_type in SEMANTIC_ENTITY_TYPES:
            measured_types[entity_type] = False


def _add_endpoint_observations(
    semantic_data: Mapping[str, Any],
    observations: dict[str, list[ObservedEntity]],
) -> None:
    # Endpoint discovery includes inference, not proof of executed access.
    for node in semantic_data["nodes"]:
        if node["node_type"] == "Endpoint":
            observations["Endpoint"].append(
                ObservedEntity(
                    entity_id=node["node_id"],
                    entity_type="Endpoint",
                    properties=node["properties"],
                )
            )
    for request in semantic_data["normalized_requests"]:
        observations["Endpoint"].append(
            ObservedEntity(
                entity_id=request["endpoint_id"],
                entity_type="Endpoint",
                properties={
                    "method": request["method"],
                    "path_template": request["path_template"],
                },
            )
        )


def _add_parameter_node_observations(
    semantic_data: Mapping[str, Any],
    observations: dict[str, list[ObservedEntity]],
) -> None:
    node_by_id = {node["node_id"]: node for node in semantic_data["nodes"]}
    # USE supplies endpoint context; opaque Parameter IDs are never decoded.
    for relationship in semantic_data["relationships"]:
        if relationship["relation_type"] != "USE":
            continue
        endpoint = node_by_id.get(relationship["source_id"])
        parameter = node_by_id.get(relationship["target_id"])
        if (
            endpoint is None
            or endpoint["node_type"] != "Endpoint"
            or parameter is None
            or parameter["node_type"] != "Parameter"
        ):
            continue
        if not has_parameter_match_fields(
            endpoint["properties"], parameter["properties"],
        ):
            continue
        observations["Parameter"].append(
            ObservedEntity(
                entity_id=parameter["node_id"],
                entity_type="Parameter",
                properties=parameter_comparison_properties(
                    endpoint["properties"], parameter["properties"],
                ),
            )
        )


def _add_snapshot_observations(
    inputs: EvaluationInputs,
    observations: dict[str, list[ObservedEntity]],
    measured_types: dict[str, bool],
) -> None:
    if inputs.graph_snapshot is not None:
        for node in inputs.graph_snapshot.nodes:
            if node["node_type"] in SNAPSHOT_ENTITY_TYPES:
                observations[node["node_type"]].append(
                    ObservedEntity(
                        entity_id=node["node_id"],
                        entity_type=node["node_type"],
                        properties=node["properties"],
                    )
                )
    else:
        for entity_type in SNAPSHOT_ENTITY_TYPES:
            measured_types[entity_type] = False


def _build_structure_metrics(
    inputs: EvaluationInputs,
    observations: Mapping[str, Sequence[ObservedEntity]],
    measured_types: Mapping[str, bool],
    relationship_score: RelationshipScore,
) -> list[Metric]:
    metrics: list[Metric] = []
    actor_matcher = _actor_matcher(inputs)
    for entity_type in ENTITY_TYPES:
        expected = [
            item
            for item in inputs.ground_truth.entities
            if item["entity_type"] == entity_type
        ]
        matched_count = sum(
            any(
                actor_matcher.matches_entity(
                    entity_type, item["match_key"], observed.properties,
                )
                for observed in observations[entity_type]
            )
            for item in expected
        )
        metrics.append(
            _metric(
                f"{entity_type.casefold()}_recall",
                matched_count,
                len(expected),
                measured=measured_types[entity_type],
            )
        )
    metrics.append(_relationship_metric(inputs, relationship_score))
    metrics.append(_workflow_metric(inputs))
    if inputs.request.matching_profile == WORKFLOW_SUBSEQUENCE_PROFILE:
        metrics.extend(_workflow_partial_metrics(inputs))
    return metrics


def _relationship_metric(
    inputs: EvaluationInputs,
    score: RelationshipScore | None = None,
) -> Metric:
    snapshot = inputs.graph_snapshot
    if snapshot is None:
        return _metric(
            "relationship_recall",
            0,
            len(inputs.ground_truth.relationships),
            measured=False,
        )
    if score is None:
        score = score_relationships(
            snapshot, inputs.ground_truth.entities,
            inputs.ground_truth.relationships, _actor_matcher(inputs),
        )
    return _metric(
        "relationship_recall",
        score.matched_count,
        len(inputs.ground_truth.relationships),
    )


def _workflow_metric(inputs: EvaluationInputs) -> Metric:
    snapshot = inputs.graph_snapshot
    if snapshot is None:
        return _metric(
            "workflow_recall",
            0,
            len(inputs.ground_truth.workflows),
            measured=False,
        )
    if inputs.request.matching_profile == WORKFLOW_SUBSEQUENCE_PROFILE:
        matched_count = sum(
            any(matches_workflow(expected, observed) for observed in snapshot.workflows)
            for expected in inputs.ground_truth.workflows
        )
    else:
        observed_signatures = {
            workflow_signature(item, is_ground_truth=False)
            for item in snapshot.workflows
        }
        matched_count = sum(
            workflow_signature(item, is_ground_truth=True) in observed_signatures
            for item in inputs.ground_truth.workflows
        )
    return _metric(
        "workflow_recall",
        matched_count,
        len(inputs.ground_truth.workflows),
    )


def _workflow_partial_metrics(inputs: EvaluationInputs) -> list[Metric]:
    snapshot = inputs.graph_snapshot
    observed_workflows = snapshot.workflows if snapshot is not None else ()
    matches = tuple(
        select_workflow_match(expected, observed_workflows)
        for expected in inputs.ground_truth.workflows
    )
    expected_step_count = sum(item.expected_step_count for item in matches)
    return [
        _metric(
            "workflow_step_recall",
            sum(item.step_match_count for item in matches),
            expected_step_count,
            measured=snapshot is not None,
        ),
        _metric(
            "workflow_ordered_step_recall",
            sum(item.ordered_step_match_count for item in matches),
            expected_step_count,
            measured=snapshot is not None,
        ),
    ]


def _score_candidates(
    inputs: EvaluationInputs,
    candidates: tuple[Candidate, ...],
) -> _CaseScore:
    crawl_data = inputs.crawl_result.data
    snapshot = inputs.graph_snapshot
    if crawl_data is None or snapshot is None:
        raise ContractValidationError("후보 평가에 필요한 입력이 없음")
    actor_matcher = _actor_matcher(inputs)
    node_by_id = {item["node_id"]: item for item in snapshot.nodes}
    cases = inputs.ground_truth.cases
    compatible_cases = [
        _compatible_case_indexes(
            candidate,
            cases,
            actor_matcher,
            node_by_id,
        )
        for candidate in candidates
    ]

    return _score_compatible_cases(compatible_cases, cases)


def _score_compatible_cases(
    compatible_cases: Sequence[Sequence[int]],
    cases: tuple[Mapping[str, Any], ...],
) -> _CaseScore:
    # Maximize positive TP first; negative diagnostics must not consume a prediction.
    positive_matches = _maximum_matching([
        [index for index in indexes if cases[index]["is_vulnerable"]]
        for indexes in compatible_cases
    ])
    negative_matches = _maximum_matching([
        [index for index in indexes if not cases[index]["is_vulnerable"]]
        for candidate_index, indexes in enumerate(compatible_cases)
        if candidate_index not in positive_matches
    ])
    tp = len(positive_matches)
    positive_case_ids = {
        index for index, case in enumerate(cases) if case["is_vulnerable"]
    }
    return _CaseScore(
        counts=CountTriple(
            tp=tp,
            fp=len(compatible_cases) - tp,
            fn=len(positive_case_ids) - tp,
        ),
        unmatched_prediction_count=(
            len(compatible_cases) - tp - len(negative_matches)
        ),
        matched_negative_count=len(negative_matches),
    )


def _compatible_case_indexes(
    candidate: Candidate,
    cases: tuple[Mapping[str, Any], ...],
    actor_matcher: ActorMatcher,
    node_by_id: Mapping[str, Mapping[str, Any]],
) -> list[int]:
    resources = [
        node_by_id[resource_id]["properties"]
        for resource_id in candidate.resource_ids
        if resource_id in node_by_id
        and node_by_id[resource_id]["node_type"] == "Resource"
    ]
    indexes = [
        index
        for index, case in enumerate(cases)
        if normalize_text(candidate.category) == normalize_text(case["category"])
        and normalize_text(candidate.vulnerability_type)
        == normalize_text(case["vulnerability_type"])
        and actor_matcher.matches_actor(case["actor_alias"], candidate.actor_account_id)
        and any(
            matches_key(case["resource_match_key"], resource, entity_type="Resource")
            for resource in resources
        )
    ]
    indexes.sort(key=lambda index: (not cases[index]["is_vulnerable"], index))
    return indexes


def _maximum_matching(edges_by_candidate: Sequence[Sequence[int]]) -> dict[int, int]:
    case_by_candidate: dict[int, int] = {}
    candidate_by_case: dict[int, int] = {}
    for start_candidate in range(len(edges_by_candidate)):
        queue = deque([start_candidate])
        visited_candidates = {start_candidate}
        visited_cases: set[int] = set()
        parent_candidate_by_case: dict[int, int] = {}
        free_case: int | None = None
        while queue and free_case is None:
            candidate_index = queue.popleft()
            for case_index in edges_by_candidate[candidate_index]:
                if case_index in visited_cases:
                    continue
                visited_cases.add(case_index)
                parent_candidate_by_case[case_index] = candidate_index
                previous_candidate = candidate_by_case.get(case_index)
                if previous_candidate is None:
                    free_case = case_index
                    break
                if previous_candidate not in visited_candidates:
                    visited_candidates.add(previous_candidate)
                    queue.append(previous_candidate)
        if free_case is None:
            continue
        current_case = free_case
        while True:
            candidate_index = parent_candidate_by_case[current_case]
            previous_case = case_by_candidate.get(candidate_index)
            case_by_candidate[candidate_index] = current_case
            candidate_by_case[current_case] = candidate_index
            if previous_case is None:
                break
            current_case = previous_case
    return case_by_candidate


def _case_metrics(
    candidate_counts: CountTriple,
    confirmed_counts: CountTriple,
) -> list[Metric]:
    return [
        _metric(
            "candidate_recall",
            candidate_counts.tp,
            candidate_counts.tp + candidate_counts.fn,
        ),
        _metric(
            "candidate_precision",
            candidate_counts.tp,
            candidate_counts.tp + candidate_counts.fp,
        ),
        _metric(
            "confirmed_recall",
            confirmed_counts.tp,
            confirmed_counts.tp + confirmed_counts.fn,
        ),
        _metric(
            "confirmed_precision",
            confirmed_counts.tp,
            confirmed_counts.tp + confirmed_counts.fp,
        ),
    ]


def _metric(
    metric_id: str,
    numerator: int,
    denominator: int,
    measured: bool = True,
) -> Metric:
    value = numerator / denominator if measured and denominator else None
    return Metric(
        metric_id=metric_id,
        value=value,
        numerator=numerator,
        denominator=denominator,
    )


def _collect_models(
    inputs: EvaluationInputs,
) -> dict[str, Mapping[str, Any]]:
    sources = (
        ("semantic_analyzer", inputs.semantic_analysis),
        (
            "access_analyzer",
            inputs.report_inputs.artifact_by_type("vulnerability_candidates"),
        ),
        (
            "scenario_generator",
            inputs.report_inputs.artifact_by_type("test_scenarios"),
        ),
    )
    models: dict[str, Mapping[str, Any]] = {}
    for producer, artifact in sources:
        data = artifact.data
        if data is not None and data["model_info"] is not None:
            models[producer] = data["model_info"]
    return models


def _aggregate_run_metrics(
    artifacts: tuple[InputArtifact, ...],
) -> dict[str, int | float | None]:
    metric_values = [item.value["runtime_metrics"] for item in artifacts]

    def aggregate(field: str, use_maximum: bool = False) -> int | float | None:
        if any(item is None or item[field] is None for item in metric_values):
            return None
        values = [item[field] for item in metric_values]
        return max(values) if use_maximum else sum(values)

    return {
        "duration_ms": aggregate("duration_ms"),
        "llm_calls": aggregate("llm_calls"),
        "input_tokens": aggregate("input_tokens"),
        "output_tokens": aggregate("output_tokens"),
        "peak_memory_mb": aggregate("peak_memory_mb", use_maximum=True),
    }


def _collect_upstream_errors(
    artifacts: tuple[InputArtifact, ...],
) -> tuple[ErrorItem, ...]:
    return tuple(
        ErrorItem(
            code=f"UPSTREAM_{artifact.source.artifact_type.upper()}_{error.code}",
            message=(
                f"{artifact.source.artifact_type} 입력 오류: {error.message}"
            ),
            item_ref=error.item_ref,
            retryable=error.retryable,
        )
        for artifact in artifacts
        for error in artifact.errors
    )


def _workflow_matching_note(profile: str) -> str:
    if profile == WORKFLOW_SUBSEQUENCE_PROFILE:
        return (
            "workflow_recall은 이름을 제외하고 GT ordered_actions가 하나의 관찰 workflow에 "
            "순서 부분수열로 포함되는지 비교함(order 정렬·반복 횟수 유지). "
            "자유 서술 constraints는 평가하지 않으며 업무 완주·정책 준수를 증명하지 않음"
        )
    return (
        "workflow_recall은 정규화한 이름과 ordered_actions를 비교하며 "
        "자유 서술 constraints는 문자열 일치로 평가하지 않음"
    )


def _build_workflow_partial_notes() -> tuple[str, ...]:
    return (
        (
            "workflow_step_recall은 순서 무관 액션 중복 횟수 교집합, "
            "workflow_ordered_step_recall은 순서 보존 LCS 길이를 사용함. "
            "각 GT의 일치 수를 합산하고 전체 GT 단계 수 합계로 나눔"
        ),
        (
            "부분 점수는 GT마다 LCS 길이→단계 일치 수→원문 workflow_id 오름차순으로 "
            "관찰 workflow 하나를 선택해 같은 흐름에서 두 분자를 계산함. "
            "모두 0점이면 대응 없음이며 다른 workflow/계정의 단계를 합치지 않음"
        ),
        (
            "부분 점수는 관찰 단계 커버리지이며 같은 관찰을 여러 GT가 사용할 수 있음. "
            "업무흐름 분리·의존관계 정확도·LLM 생성 품질을 평가하지 않음. "
            "GT 단계 분모 0은 null, 유효한 빈 관찰은 분모가 있으면 0으로 기록함"
        ),
    )


def _build_notes(
    profile: str,
    inputs: EvaluationInputs,
    candidate_score: _CaseScore,
    confirmed_score: _CaseScore,
    finding_counts: Counter[str],
) -> list[str]:
    notes = [
        (
            f"{profile}: Page는 crawl_result, Endpoint는 semantic_analysis의 "
            "Endpoint 노드와 normalized_requests, Parameter는 Endpoint→USE→Parameter "
            "노드와 normalized_requests, "
            "User·Role·Action·Resource·관계·Flow는 "
            "실제 KG snapshot의 정규화 키로 평가함"
        ),
        "inferred Endpoint·Parameter도 발견율에 포함하며 실제 요청·접근 성공·취약점 확정을 뜻하지 않음",
        "미검증 항목을 정답 양성 분모에서 제외하지 않음",
        _workflow_matching_note(profile),
        (
            "run_metrics는 7개 실행 입력의 합계이며 peak_memory_mb는 "
            "최댓값, 하나라도 미측정이면 해당 값은 null임"
        ),
    ]
    if profile == WORKFLOW_SUBSEQUENCE_PROFILE:
        notes.extend(_build_workflow_partial_notes())
    notes.append("완전한 정답 집합 benchmark 가정: FP는 전체 prediction 수−양성 GT 일대일 TP 수")
    for label, score in (("후보", candidate_score), ("확정 후보", confirmed_score)):
        notes.append(
            f"{label} 매칭: 양성 {score.counts.tp}개, 명시적 음성 "
            f"{score.matched_negative_count}개, 일대일 미매칭 {score.unmatched_prediction_count}개"
        )
    if inputs.settings.relative_path is not None:
        notes.append(
            f"평가 계정 매핑 설정: {inputs.settings.relative_path}, "
            f"SHA-256={inputs.settings.sha256}, binding {len(inputs.settings.actor_bindings)}개"
        )
    elif not inputs.settings.actor_bindings:
        notes.append("평가 계정 매핑 설정 없음: 기존 alias 기준으로 비교함")
    if candidate_score.unmatched_prediction_count:
        notes.append(
            "Ground Truth 평가 범위에 일대일 매칭되지 않은 후보: "
            f"{candidate_score.unmatched_prediction_count}개"
        )
    if confirmed_score.unmatched_prediction_count:
        notes.append(
            "Ground Truth 평가 범위에 일대일 매칭되지 않은 확정 후보: "
            f"{confirmed_score.unmatched_prediction_count}개"
        )
    if finding_counts["suspected"]:
        notes.append(
            "확정 집계에서 제외한 suspected: "
            f"{finding_counts['suspected']}개"
        )
    if inputs.semantic_analysis.data is None:
        notes.append("semantic_analysis data가 없어 Endpoint·Parameter 지표는 미측정")
    return notes
