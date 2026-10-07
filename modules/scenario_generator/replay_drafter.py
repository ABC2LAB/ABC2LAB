"""파일에 미리 적어 둔 초안을 그대로 돌려주는 drafter. LLM이 연결되기 전의 개발·테스트용이다.

model_info는 LLM이 아니라는 사실을 그대로 드러낸다 (진짜 모델인 것처럼 기록하지 않는다).
"""

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from modules.scenario_generator.scenario_drafter import Draft, DrafterError
from modules.scenario_generator.utils.json_io import read_json_strict

REPLAY_MODEL_INFO: dict[str, Any] = {
    "model_id": "replay_file",
    "model_version": "none",
    "prompt_version": "none",
    "temperature": None,
    "seed": None,
}


class ReplayScenarioDrafter:
    def __init__(self, drafts_by_candidate_id: Mapping[str, Any], model_info: dict[str, Any] | None = None) -> None:
        self._drafts_by_candidate_id = drafts_by_candidate_id
        self.model_info = dict(REPLAY_MODEL_INFO) if model_info is None else model_info

    @classmethod
    def from_file(cls, path: Path) -> "ReplayScenarioDrafter":
        drafts = read_json_strict(path)
        if not isinstance(drafts, dict):
            raise ValueError("초안 파일의 최상위는 candidate_id를 키로 하는 객체여야 한다")
        return cls(drafts)

    def draft(self, request: dict[str, Any]) -> Draft:
        candidate_id = request["candidate"]["candidate_id"]
        if candidate_id not in self._drafts_by_candidate_id:
            raise DrafterError(f"후보 {candidate_id}의 미리 적어 둔 초안이 없다")
        return Draft(scenario=copy.deepcopy(self._drafts_by_candidate_id[candidate_id]))
