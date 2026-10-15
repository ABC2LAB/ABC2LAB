"""시나리오 생성 본체: 후보 대조 → 초안(LLM, steps만) → 조립·기준 단계·재현 조건(프로그램) → 검증 → 시나리오 목록.

후보 하나가 실패해도 나머지는 계속 처리하고, 실패는 candidate_id와 함께 오류로 남긴다
(실패를 가짜 정상 계획으로 대체하지 않는다).
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from modules.scenario_generator.candidate_matcher import CrawlIndex, MatchedCandidate, build_crawl_index, match_candidates
from modules.scenario_generator.draft_diagnostics import build_draft_invalid_record, build_drafter_failed_record
from modules.scenario_generator.input_adapter import InputErrorCode, LoadedArtifact
from modules.scenario_generator.reproduction_checks import (
    ReproductionProblem,
    build_reproduction,
    estimate_state_change,
    find_baseline_blocker,
)
from modules.scenario_generator.scenario_drafter import (
    ACCOUNT_ROLE_ACTOR,
    ACCOUNT_ROLE_REFERENCE,
    DRAFT_KEYS,
    DRAFT_REQUEST_KEYS,
    DRAFT_STEP_KEYS,
    DrafterError,
    ScenarioDrafter,
    build_draft_request,
)
from modules.scenario_generator.scenario_validator import (
    ValidationContext,
    find_scenario_problems,
    find_steps_schema_problem,
    summarize_problems,
)

logger = logging.getLogger(__name__)

# 후보 하나당 시나리오 하나(1:1)로 보고, 후보 ID에서 결정적으로 만든다. LLM이 ID를 정하지 않게 한다.
SCENARIO_ID_PREFIX = "scenario_"
# 세션 사전조건 check_id 접두어. 프로그램이 steps에 쓰인 계정마다 하나씩 생성한다(LLM이 만들지 않는다).
SESSION_PRECONDITION_PREFIX = "precondition_session_"
# 출력 ParameterValue의 키. path 파라미터는 URL로 흡수돼 출력에 남지 않으므로 Schema 대신 여기서 모양을 확인한다.
PARAMETER_KEYS = frozenset({"name", "location", "value", "binding_ref"})
PATH_LOCATION = "path"
PATH_SEPARATOR = "/"


class GenerationErrorCode(StrEnum):
    DRAFTER_FAILED = "DRAFTER_FAILED"
    DRAFT_INVALID = "DRAFT_INVALID"
    # 이 후보로는 기준 응답(기준 계정이 같은 요청을 보낸 응답)을 만들 수 없어 재현 조건을 세우지 못함. 시나리오를 만들지 않는다.
    ASSERTION_BASELINE_UNAVAILABLE = "ASSERTION_BASELINE_UNAVAILABLE"


@dataclass(frozen=True)
class ComposeFailure:
    code: GenerationErrorCode
    message: str


@dataclass(frozen=True)
class GenerationOutcome:
    scenarios: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    candidate_count: int
    llm_calls: int
    input_tokens: int | None
    output_tokens: int | None
    # 실패한 초안의 개발용 진단 레코드(비밀값 제거). entrypoint가 private 영역에만 저장한다.
    diagnostics: list[dict[str, Any]]


def _make_error_item(code: str, message: str, item_ref: str | None, is_retryable: bool) -> dict[str, Any]:
    return {"code": code, "message": message, "item_ref": item_ref, "retryable": is_retryable}


def _sum_known(values: list[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _resolve_step_account(account_role: Any, matched: MatchedCandidate) -> dict[str, Any] | None:
    """account_role(actor|reference) → 실제 계정. reference인데 없으면 None."""
    if account_role == ACCOUNT_ROLE_ACTOR:
        return matched.actor_account
    if account_role == ACCOUNT_ROLE_REFERENCE:
        return matched.reference_account
    return None


def _bind_path_segment(segments: list[str], parameter: dict[str, Any], observed_by_name: dict[str, Any]) -> str | None:
    """path 파라미터 관찰값과 같은 경로 조각 하나를 {binding_ref}로 바꾼다(segments를 고친다). 못 하면 문제 문구."""
    if set(parameter) != PARAMETER_KEYS:
        return f"path 파라미터 키는 {sorted(PARAMETER_KEYS)}여야 한다"
    binding_ref = parameter["binding_ref"]
    # verifier는 URL에 자리가 없는 path 파라미터를 조용히 무시한다. 리터럴 path 값은 효과 없이 "실행됨"으로 보이므로 받지 않는다.
    if not isinstance(binding_ref, str) or not binding_ref or parameter["value"] is not None:
        return "path 파라미터는 value 없이 binding_ref로만 바꿀 수 있다"
    name = parameter["name"]
    observed = observed_by_name.get(name) if isinstance(name, str) else None
    if observed is None:
        return "path 파라미터가 원본 요청에 없거나 관찰값이 없다"
    positions = [position for position, segment in enumerate(segments) if unquote(segment) == str(observed)]
    if len(positions) != 1:
        # 어느 조각을 바꿀지 하나로 정할 수 없으면 추측하지 않는다.
        return "path 파라미터 관찰값과 같은 경로 조각이 하나가 아니다"
    segments[positions[0]] = f"{{{binding_ref}}}"
    return None


def _compose_url_template(source: dict[str, Any], draft_parameters: Any, step_id: Any) -> tuple[str, Any, str | None]:
    """선택한 원본 요청 URL로 url_template을 만든다. 돌려주는 것: (url_template, 출력 parameters, 문제|None).

    초안은 URL을 쓰지 않는다. path 파라미터에 binding_ref를 걸면 그 조각만 {binding_ref}로 바꾸고 파라미터는 URL로 흡수한다.
    """
    if not isinstance(draft_parameters, list):
        return source["url"], draft_parameters, None  # 모양 오류는 출력 Schema 검증이 잡는다
    observed_by_name = {p["name"]: p["value"] for p in source["parameters"] if p["location"] == PATH_LOCATION}
    url_parts = urlsplit(source["url"])
    segments = url_parts.path.split(PATH_SEPARATOR)
    remaining_parameters = []
    for parameter in draft_parameters:
        if not isinstance(parameter, dict) or parameter.get("location") != PATH_LOCATION:
            remaining_parameters.append(parameter)
            continue
        problem = _bind_path_segment(segments, parameter, observed_by_name)
        if problem is not None:
            return source["url"], draft_parameters, f"step {step_id}: {problem}"
    if len(remaining_parameters) == len(draft_parameters):
        return source["url"], draft_parameters, None  # 바꾼 조각이 없으면 원본 URL 문자열을 그대로 쓴다
    return urlunsplit(url_parts._replace(path=PATH_SEPARATOR.join(segments))), remaining_parameters, None


def _compose_step(draft_step: Any, matched: MatchedCandidate, index: CrawlIndex) -> tuple[dict[str, Any] | None, str | None]:
    """초안 step(선택만)을 출력 step으로 조립한다. 식별자·method·url_template·body_ref·state_change는 원본에서 채운다."""
    if not isinstance(draft_step, dict) or set(draft_step) != DRAFT_STEP_KEYS:
        return None, f"step 키는 {sorted(DRAFT_STEP_KEYS)}여야 한다"
    account = _resolve_step_account(draft_step["account_role"], matched)
    if account is None:
        return None, f"step {draft_step['step_id']}: account_role가 {ACCOUNT_ROLE_ACTOR}/{ACCOUNT_ROLE_REFERENCE}가 아니거나 기준 계정이 없다"
    # 단계 요청은 crawl_result에 실제로 있는 요청만 쓴다(목록 등 setup 단계는 후보 source_requests 밖일 수 있다).
    source = index.requests_by_id.get(draft_step["source_request_id"])
    if source is None:
        return None, f"step {draft_step['step_id']}: source_request_id가 crawl_result에 없다"
    draft_request = draft_step["request"]
    if not isinstance(draft_request, dict) or set(draft_request) != DRAFT_REQUEST_KEYS:
        return None, f"step {draft_step['step_id']}: request 키는 {sorted(DRAFT_REQUEST_KEYS)}여야 한다"
    url_template, parameters, problem = _compose_url_template(source, draft_request["parameters"], draft_step["step_id"])
    if problem is not None:
        return None, problem
    composed = {
        "step_id": draft_step["step_id"],
        "order": draft_step["order"],
        "source_request_id": draft_step["source_request_id"],
        "account_id": account["account_id"],
        "role_id": account["role_id"],
        "session_ref": account["session_ref"],
        "request": {
            "method": source["method"],
            "url_template": url_template,
            "parameters": parameters,
            "body_ref": source["body_ref"],
        },
        "bindings": draft_step["bindings"],
        "state_change": estimate_state_change(source["method"]),
    }
    return composed, None


def _build_session_preconditions(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """steps에 쓰인 계정마다 session_valid(eq true) 사전조건을 만든다. 세션이 죽은 실행을 위반 판정에 쓰지 않게 한다."""
    preconditions: list[dict[str, Any]] = []
    seen_account_ids: list[str] = []
    for step in steps:
        account_id = step["account_id"]
        if account_id in seen_account_ids:
            continue
        seen_account_ids.append(account_id)
        preconditions.append({
            "check_id": f"{SESSION_PRECONDITION_PREFIX}{len(seen_account_ids)}",
            "kind": "session_valid",
            "subject_ref": account_id,
            "selector": None,
            "operator": "eq",
            "expected": True,
        })
    return preconditions


def _compose_scenario(
    matched: MatchedCandidate, draft_scenario: Any, index: CrawlIndex
) -> tuple[dict[str, Any] | None, ComposeFailure | None]:
    """초안 steps를 조립하고 기준 단계·재현 조건을 붙인다. 판정 조건은 초안이 아니라 프로그램이 만든다."""
    candidate = matched.candidate
    if not isinstance(draft_scenario, dict) or set(draft_scenario) != DRAFT_KEYS:
        # assertions·candidate_id·resource_ids·preconditions 같은 키를 LLM이 직접 쓰면 원본과 어긋날 수 있어 받지 않는다.
        return None, _draft_invalid(f"초안의 키는 {sorted(DRAFT_KEYS)}여야 한다")
    draft_steps = draft_scenario["steps"]
    if not isinstance(draft_steps, list) or not draft_steps:
        return None, _draft_invalid("steps가 비어 있거나 배열이 아니다")
    composed_steps: list[dict[str, Any]] = []
    for draft_step in draft_steps:
        step, problem = _compose_step(draft_step, matched, index)
        if problem is not None:
            return None, _draft_invalid(problem)
        composed_steps.append(step)
    # 기준 단계는 재현 단계를 복사해 만들므로 그 전에 단계 모양(타입)을 믿을 수 있어야 한다.
    schema_problem = find_steps_schema_problem(composed_steps)
    if schema_problem is not None:
        return None, _draft_invalid(schema_problem)
    reproduction = build_reproduction(matched, composed_steps, index)
    if isinstance(reproduction, ReproductionProblem):
        code = (
            GenerationErrorCode.ASSERTION_BASELINE_UNAVAILABLE
            if reproduction.is_baseline_unavailable
            else GenerationErrorCode.DRAFT_INVALID
        )
        return None, ComposeFailure(code, reproduction.message)
    scenario = {
        "scenario_id": f"{SCENARIO_ID_PREFIX}{candidate['candidate_id']}",
        "candidate_id": candidate["candidate_id"],
        "expected_basis": candidate["expected_basis"],
        # KG Resource instance node_id. verifier가 검증 결과를 KG 자원 노드에 잇는 키라 후보 값을 그대로 옮긴다.
        "resource_ids": list(candidate["resource_ids"]),
        "preconditions": _build_session_preconditions(reproduction.steps),
        "steps": reproduction.steps,
        "assertions": reproduction.assertions,
    }
    return scenario, None


def _draft_invalid(message: str) -> ComposeFailure:
    return ComposeFailure(GenerationErrorCode.DRAFT_INVALID, message)


def _finalize_scenario(
    matched: MatchedCandidate, draft_scenario: Any, index: CrawlIndex, target_url: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    """초안을 조립·검증한다. 돌려주는 것: (시나리오|None, 오류 항목, 초안 실패 문제 목록).

    초안 실패(DRAFT_INVALID)면 문제 목록이 비어 있지 않다(진단 기록용 전체 목록이며, 공개 오류 메시지는 요약본을 쓴다).
    기준 응답을 만들 수 없는 후보(ASSERTION_BASELINE_UNAVAILABLE)는 초안 탓이 아니라 문제 목록 없이 오류만 돌려준다.
    """
    candidate_id = matched.candidate["candidate_id"]
    scenario, failure = _compose_scenario(matched, draft_scenario, index)
    if failure is not None and failure.code is GenerationErrorCode.ASSERTION_BASELINE_UNAVAILABLE:
        logger.warning("후보 %s는 기준 응답을 만들 수 없어 시나리오를 만들지 않는다", candidate_id)
        return None, [_make_error_item(failure.code.value, failure.message, candidate_id, False)], []
    problems = [failure.message] if failure is not None else find_scenario_problems(scenario, ValidationContext(matched, index, target_url))
    if problems:
        logger.warning("후보 %s의 초안이 검증을 통과하지 못했다 (%d건)", candidate_id, len(problems))
        error = _make_error_item(GenerationErrorCode.DRAFT_INVALID.value, summarize_problems(problems), candidate_id, False)
        return None, [error], problems
    return scenario, [], []


def _upstream_partial_errors(artifacts: list[LoadedArtifact]) -> list[dict[str, Any]]:
    # 일부만 받은 입력으로 전체 범위가 정상이라고 단정하지 않는다 (명세 02).
    return [
        _make_error_item(
            InputErrorCode.UPSTREAM_PARTIAL.value,
            f"{artifact.artifact_type}이(가) partial이다. 누락 범위는 해당 산출물의 errors를 따른다",
            None,
            False,
        )
        for artifact in artifacts
        if artifact.status == "partial"
    ]


def generate_scenarios(
    candidates_artifact: LoadedArtifact, crawl_artifact: LoadedArtifact, drafter: ScenarioDrafter
) -> GenerationOutcome:
    """crawl_result 안에 ID 중복이 있으면 InputError가 올라간다(어느 쪽이 맞는지 알 수 없어서)."""
    crawl_data = crawl_artifact.document["data"]
    candidates = candidates_artifact.document["data"]["candidates"]
    index = build_crawl_index(crawl_data)
    match_result = match_candidates(candidates, index)

    errors = _upstream_partial_errors([candidates_artifact, crawl_artifact])
    errors += [rejected.to_error_item() for rejected in match_result.rejected]
    scenarios: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    input_token_counts: list[int | None] = []
    output_token_counts: list[int | None] = []
    llm_calls = 0
    model_info = getattr(drafter, "model_info", None)

    for matched in match_result.matched:
        candidate_id = matched.candidate["candidate_id"]
        blocker = find_baseline_blocker(matched)
        if blocker is not None:
            # 재현 조건을 세울 수 없는 후보는 LLM을 부르지 않는다. 가짜 조건으로 시나리오를 만들지 않는다.
            errors.append(_make_error_item(GenerationErrorCode.ASSERTION_BASELINE_UNAVAILABLE.value, blocker, candidate_id, False))
            continue
        llm_calls += 1
        try:
            draft = drafter.draft(build_draft_request(matched, crawl_data["target_url"]))
        except DrafterError as error:
            logger.warning("후보 %s의 초안 생성에 실패했다", candidate_id)
            errors.append(
                _make_error_item(GenerationErrorCode.DRAFTER_FAILED.value, str(error), candidate_id, error.is_retryable)
            )
            diagnostics.append(build_drafter_failed_record(candidate_id, str(error), model_info))
            continue
        input_token_counts.append(draft.input_tokens)
        output_token_counts.append(draft.output_tokens)

        scenario, scenario_errors, problems = _finalize_scenario(matched, draft.scenario, index, crawl_data["target_url"])
        errors += scenario_errors
        if scenario is not None:
            scenarios.append(scenario)
        elif problems:
            diagnostics.append(build_draft_invalid_record(candidate_id, draft.scenario, problems, model_info))

    return GenerationOutcome(
        scenarios=scenarios,
        errors=errors,
        candidate_count=len(candidates),
        llm_calls=llm_calls,
        input_tokens=_sum_known(input_token_counts),
        output_tokens=_sum_known(output_token_counts),
        diagnostics=diagnostics,
    )
