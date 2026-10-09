"""시나리오 생성 본체: 후보 대조 → 초안(LLM) → 초안 검증 → 시나리오 목록.

후보 하나가 실패해도 나머지는 계속 처리하고, 실패는 candidate_id와 함께 오류로 남긴다
(실패를 가짜 정상 계획으로 대체하지 않는다).
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from modules.scenario_generator.candidate_matcher import build_crawl_index, match_candidates
from modules.scenario_generator.input_adapter import InputErrorCode, LoadedArtifact
from modules.scenario_generator.scenario_drafter import (
    DRAFT_KEYS,
    DrafterError,
    ScenarioDrafter,
    build_draft_request,
)
from modules.scenario_generator.scenario_validator import (
    ValidationContext,
    find_scenario_problems,
    summarize_problems,
)

logger = logging.getLogger(__name__)

# 후보 하나당 시나리오 하나(1:1)로 보고, 후보 ID에서 결정적으로 만든다. LLM이 ID를 정하지 않게 한다.
SCENARIO_ID_PREFIX = "scenario_"


class GenerationErrorCode(StrEnum):
    DRAFTER_FAILED = "DRAFTER_FAILED"
    DRAFT_INVALID = "DRAFT_INVALID"


@dataclass(frozen=True)
class GenerationOutcome:
    scenarios: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    candidate_count: int
    llm_calls: int
    input_tokens: int | None
    output_tokens: int | None


def _make_error_item(code: str, message: str, item_ref: str | None, is_retryable: bool) -> dict[str, Any]:
    return {"code": code, "message": message, "item_ref": item_ref, "retryable": is_retryable}


def _sum_known(values: list[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _compose_scenario(candidate: dict[str, Any], draft_scenario: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(draft_scenario, dict) or set(draft_scenario) != DRAFT_KEYS:
        # candidate_id·expected_basis·resource_ids 같은 키를 LLM이 직접 쓰면 원본과 어긋날 수 있어 받지 않는다.
        return None, f"초안의 키는 {sorted(DRAFT_KEYS)}여야 한다"
    scenario = {
        "scenario_id": f"{SCENARIO_ID_PREFIX}{candidate['candidate_id']}",
        "candidate_id": candidate["candidate_id"],
        "expected_basis": candidate["expected_basis"],
        # KG Resource instance node_id. verifier가 검증 결과를 KG 자원 노드에 잇는 키라 후보 값을 그대로 옮긴다.
        "resource_ids": list(candidate["resource_ids"]),
        **draft_scenario,
    }
    return scenario, None


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
    input_token_counts: list[int | None] = []
    output_token_counts: list[int | None] = []
    llm_calls = 0

    for matched in match_result.matched:
        candidate = matched.candidate
        candidate_id = candidate["candidate_id"]
        llm_calls += 1
        try:
            draft = drafter.draft(build_draft_request(matched, crawl_data["target_url"]))
        except DrafterError as error:
            logger.warning("후보 %s의 초안 생성에 실패했다", candidate_id)
            errors.append(
                _make_error_item(GenerationErrorCode.DRAFTER_FAILED.value, str(error), candidate_id, error.is_retryable)
            )
            continue
        input_token_counts.append(draft.input_tokens)
        output_token_counts.append(draft.output_tokens)

        scenario, problem = _compose_scenario(candidate, draft.scenario)
        problems = [problem] if scenario is None else find_scenario_problems(
            scenario, ValidationContext(matched, index, crawl_data["target_url"])
        )
        if problems:
            logger.warning("후보 %s의 초안이 검증을 통과하지 못했다 (%d건)", candidate_id, len(problems))
            errors.append(
                _make_error_item(GenerationErrorCode.DRAFT_INVALID.value, summarize_problems(problems), candidate_id, False)
            )
            continue
        scenarios.append(scenario)

    return GenerationOutcome(
        scenarios=scenarios,
        errors=errors,
        candidate_count=len(candidates),
        llm_calls=llm_calls,
        input_tokens=_sum_known(input_token_counts),
        output_tokens=_sum_known(output_token_counts),
    )
