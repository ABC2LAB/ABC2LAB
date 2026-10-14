"""Optional, dataset-scoped identity settings for development evaluation only."""

from __future__ import annotations

import os
from pathlib import Path

from modules.reporter.exceptions import ContractValidationError
from modules.reporter.matching import normalize_text
from modules.reporter.models import ActorBinding, EvaluationSettings, GroundTruth
from modules.reporter.utils.hashing import calculate_sha256
from modules.reporter.utils.paths import require_existing_file
from modules.reporter.utils.validation import (
    load_json,
    require_unique,
    validate_schema,
    verify_file_sha256,
)

EVALUATION_CONFIG_PATH_ENV = "REPORTER_EVALUATION_CONFIG_PATH"
EVALUATION_CONFIG_SCHEMA = Path(__file__).parent / "schemas/config/evaluation.schema.json"


def load_evaluation_settings(
    project_root: Path | None,
    ground_truth: GroundTruth,
) -> EvaluationSettings:
    relative_path = os.environ.get(EVALUATION_CONFIG_PATH_ENV)
    if relative_path is None:
        return EvaluationSettings()
    if project_root is None:
        raise ContractValidationError("평가 설정의 project_root가 없음")
    path = require_existing_file(project_root, relative_path)
    sha256 = calculate_sha256(path)
    value = load_json(path)
    validate_schema(value, EVALUATION_CONFIG_SCHEMA)
    verify_file_sha256(path, sha256)
    if (
        value["dataset_id"] != ground_truth.dataset_id
        or value["dataset_version"] != ground_truth.dataset_version
    ):
        raise ContractValidationError("평가 설정의 dataset_id/dataset_version이 GT와 다름")
    bindings = tuple(ActorBinding(**item) for item in value["actor_bindings"])
    require_unique(
        (normalize_text(item.actor_alias) for item in bindings), "평가 actor alias",
    )
    require_unique((item.account_id for item in bindings), "평가 account_id")
    return EvaluationSettings(
        actor_bindings=bindings, relative_path=relative_path, sha256=sha256,
    )
