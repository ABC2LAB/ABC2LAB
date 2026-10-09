"""공개 실행 창구: `run(operation, input_paths, output_dir, context)`와 CLI.

처리 순서(명세 02): 입력 adapter → service → 출력 adapter → 임시 파일 → flush·close → rename → 완료 응답.
입력·설정 문제는 `failed` 파일로 남기고, 파일 자체를 쓰지 못하면 예외를 던진다(없는 파일을 완료라고 알리지 않는다).

초안 생성기(drafter): `drafter=` 인자로 주면 그걸 쓰고, 안 주면 설정 파일로 만든다
(SCENARIO_GENERATOR_CONFIG_PATH > modules/scenario_generator/configs/scenario_generator.toml, utils/config.py).
설정이 없거나 틀리면 파일을 쓰기 전에 ValueError. provider=none이면 drafter 없이 DRAFTER_NOT_CONFIGURED failed 파일.
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modules.scenario_generator.input_adapter import (
    SUPPORTED_INPUT_TYPES,
    InputError,
    InputSource,
    LoadedArtifact,
    load_input_artifact,
)
from modules.scenario_generator.output_adapter import (
    STATUS_FAILED,
    OutputContractError,
    OutputParts,
    RunIdentity,
    build_output_document,
    decide_status,
)
from modules.scenario_generator.ollama_drafter import OllamaDrafterConfig, OllamaScenarioDrafter
from modules.scenario_generator.replay_drafter import ReplayScenarioDrafter
from modules.scenario_generator.scenario_drafter import DrafterError, ScenarioDrafter
from modules.scenario_generator.service import GenerationOutcome, generate_scenarios
from modules.scenario_generator.utils.atomic_io import write_json_atomically
from modules.scenario_generator.utils.config import (
    CONFIG_PATH_ENV,
    DRAFTS_PATH_KEY,
    MODEL_ID_KEY,
    PROVIDER_NONE,
    PROVIDER_OLLAMA,
    PROVIDER_REPLAY,
    DrafterSettings,
    load_drafter_settings,
    parse_drafter_settings,
    resolve_config_path,
)

logger = logging.getLogger(__name__)

OPERATION_GENERATE = "generate"
OUTPUT_FILENAME = "test_scenarios.json"
ALLOWED_MODES = ("diagnosis", "development")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXIT_FILE_WRITTEN = 0
EXIT_NO_FILE_WRITTEN = 2
DRAFTER_NOT_CONFIGURED_CODE = "DRAFTER_NOT_CONFIGURED"
MILLISECONDS_PER_SECOND = 1000
CLI_SOURCE = "CLI"
# CLI ollama 옵션(argparse dest) → 설정 키. drafter 옵션을 하나라도 주면 CLI 모드(설정 파일을 안 읽는다).
CLI_OLLAMA_KEY_BY_DEST = {
    "model_id": "model_id",
    "base_url": "base_url",
    "temperature": "temperature",
    "seed": "seed",
    "timeout": "timeout_seconds",
}


class OutputWriteError(RuntimeError):
    """출력 파일을 만들 수 없다(경로·저장 실패). 이때는 완료 응답을 만들지 않는다."""


@dataclass(frozen=True)
class ExecutionContext:
    run_root: Path
    run_id: str
    iteration: int
    mode: str
    expected_sha256_by_type: dict[str, str]


def _parse_context(context: Mapping[str, Any]) -> ExecutionContext:
    # context에는 JSON으로 표현되는 실행 값만 둔다(명세 03). 객체를 조용히 무시하지 않고 거절한다.
    if "drafter" in context:
        raise ValueError("drafter는 context가 아니라 drafter= 인자로 넘긴다")
    raw_run_root = context.get("run_root")
    if raw_run_root is None or not Path(raw_run_root).is_dir():
        raise ValueError("context.run_root가 존재하는 폴더여야 한다")
    run_root = Path(raw_run_root)
    run_id = context.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("context.run_id가 필요하다")
    iteration = context.get("iteration")
    if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 0:
        raise ValueError("context.iteration은 0 이상의 정수여야 한다")
    mode = context.get("mode")
    if mode not in ALLOWED_MODES:
        raise ValueError(f"context.mode는 {ALLOWED_MODES} 중 하나여야 한다")
    expected_sha256_by_type = dict(context.get("expected_sha256") or {})
    for artifact_type, digest in expected_sha256_by_type.items():
        if artifact_type not in SUPPORTED_INPUT_TYPES or not SHA256_PATTERN.fullmatch(str(digest)):
            raise ValueError("context.expected_sha256은 {입력 종류: 소문자 hex 64자} 형태여야 한다")
    return ExecutionContext(run_root, run_id, iteration, mode, expected_sha256_by_type)


def _parse_input_paths(input_paths: Mapping[str, str | Path]) -> dict[str, Path]:
    if set(input_paths) != set(SUPPORTED_INPUT_TYPES):
        raise ValueError(f"input_paths의 키는 {sorted(SUPPORTED_INPUT_TYPES)}여야 한다")
    return {artifact_type: Path(path) for artifact_type, path in input_paths.items()}


def _resolve_output_path(output_dir: Path, run_root: Path) -> Path:
    resolved_dir = output_dir.resolve()
    if not resolved_dir.is_relative_to(run_root.resolve()):
        raise OutputWriteError("output_dir이 run 루트 밖에 있다")
    return resolved_dir / OUTPUT_FILENAME


def _load_inputs(
    input_paths: Mapping[str, Path], execution: ExecutionContext
) -> tuple[dict[str, LoadedArtifact], list[InputError]]:
    loaded: dict[str, LoadedArtifact] = {}
    errors: list[InputError] = []
    for artifact_type in SUPPORTED_INPUT_TYPES:
        source = InputSource(
            artifact_type, input_paths[artifact_type], execution.expected_sha256_by_type.get(artifact_type)
        )
        try:
            loaded[artifact_type] = load_input_artifact(source, execution.run_root, execution.run_id)
        except InputError as error:
            logger.error("입력 %s을(를) 쓸 수 없다: %s", artifact_type, error.code.value)
            errors.append(error)
    return loaded, errors


def _make_runtime_metrics(started: float, outcome: GenerationOutcome | None) -> dict[str, Any]:
    return {
        "duration_ms": int((time.monotonic() - started) * MILLISECONDS_PER_SECOND),
        "llm_calls": 0 if outcome is None else outcome.llm_calls,
        "input_tokens": None if outcome is None else outcome.input_tokens,
        "output_tokens": None if outcome is None else outcome.output_tokens,
        "peak_memory_mb": None,
    }


def _make_failed_parts(
    errors: list[dict[str, Any]], loaded: Mapping[str, LoadedArtifact], started: float
) -> OutputParts:
    return OutputParts(
        status=STATUS_FAILED,
        errors=errors,
        input_refs=[artifact.artifact_ref for artifact in loaded.values()],
        scenarios=None,
        model_info=None,
        runtime_metrics=_make_runtime_metrics(started, None),
    )


def _produce_parts(
    input_paths: Mapping[str, Path], execution: ExecutionContext, drafter: ScenarioDrafter | None, started: float
) -> OutputParts:
    loaded, input_errors = _load_inputs(input_paths, execution)
    if input_errors:
        return _make_failed_parts([error.to_error_item() for error in input_errors], loaded, started)
    if drafter is None:
        error_item = {
            "code": DRAFTER_NOT_CONFIGURED_CODE,
            "message": "시나리오 초안 생성기(LLM)가 설정되지 않았다",
            "item_ref": None,
            "retryable": False,
        }
        return _make_failed_parts([error_item], loaded, started)

    try:
        outcome = generate_scenarios(
            loaded["vulnerability_candidates"], loaded["crawl_result"], drafter
        )
    except InputError as error:
        return _make_failed_parts([error.to_error_item()], loaded, started)

    status = decide_status(len(outcome.scenarios), outcome.candidate_count, len(outcome.errors))
    return OutputParts(
        status=status,
        errors=outcome.errors,
        input_refs=[loaded[artifact_type].artifact_ref for artifact_type in SUPPORTED_INPUT_TYPES],
        scenarios=None if status == STATUS_FAILED else outcome.scenarios,
        model_info=drafter.model_info,
        runtime_metrics=_make_runtime_metrics(started, outcome),
    )


def _make_created_at() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(
    operation: str,
    input_paths: Mapping[str, str | Path],
    output_dir: str | Path,
    context: Mapping[str, Any],
    *,
    drafter: ScenarioDrafter | None = None,
) -> dict[str, Any]:
    """test_scenarios.json을 만들어 저장하고 완료 응답을 돌려준다.

    context: run_root, run_id, iteration, mode (필수) / expected_sha256 (선택). JSON으로 표현되는 값만 받는다.
    drafter: 초안 생성기(LLM 객체). context가 아니라 이 인자로 받는다. 주면 설정 파일을 읽지 않는다.
        안 주면 설정 파일로 만들고, 설정이 없거나 틀리면 파일을 쓰기 전에 ValueError.
    응답: status, artifact_id, output_path, sha256, scenario_count, error_count.
    """
    started = time.monotonic()
    if operation != OPERATION_GENERATE:
        raise ValueError(f"지원하지 않는 operation: {operation}")
    execution = _parse_context(context)
    if drafter is None:
        drafter = _build_configured_drafter()
    parsed_input_paths = _parse_input_paths(input_paths)
    output_path = _resolve_output_path(Path(output_dir), execution.run_root)

    identity = RunIdentity(
        run_id=execution.run_id,
        iteration=execution.iteration,
        mode=execution.mode,
        artifact_id=f"test_scenarios_{execution.run_id}_iteration{execution.iteration:03d}",
    )
    parts = _produce_parts(parsed_input_paths, execution, drafter, started)
    document = build_output_document(identity, _make_created_at(), parts)

    try:
        digest = write_json_atomically(output_path, document)
    except OSError as error:
        raise OutputWriteError(f"출력 파일을 저장하지 못했다: {output_path.name}") from error

    logger.info("test_scenarios.json 저장 완료: status=%s, errors=%d", parts.status, len(parts.errors))
    return {
        "status": parts.status,
        "artifact_id": identity.artifact_id,
        "output_path": str(output_path),
        "sha256": digest,
        "scenario_count": 0 if parts.scenarios is None else len(parts.scenarios),
        "error_count": len(parts.errors),
    }


def _parse_expected_sha256_args(values: Sequence[str]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for value in values:
        artifact_type, separator, digest = value.partition("=")
        if not separator:
            raise ValueError(f"--expected-sha256은 <입력종류>=<해시> 형태여야 한다: {artifact_type}")
        expected[artifact_type] = digest
    return expected


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scenario_generator", description="test_scenarios.json을 만든다")
    parser.add_argument("operation", choices=[OPERATION_GENERATE])
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--iteration", required=True, type=int)
    parser.add_argument("--mode", required=True, choices=ALLOWED_MODES)
    parser.add_argument("--candidates", required=True, type=Path, help="vulnerability_candidates.json 경로")
    parser.add_argument("--crawl-result", required=True, type=Path, help="crawl_result.json 경로")
    parser.add_argument("--output-dir", required=True, type=Path)
    # drafter 옵션은 기본값을 모두 None으로 둔다: "줬는지"로 CLI 모드를 가린다. ollama 기본값은 OllamaDrafterConfig.
    parser.add_argument("--config", type=Path,
                        help=f"drafter 설정 TOML (없으면 {CONFIG_PATH_ENV}, 그것도 없으면 기본 경로). drafter 옵션과 같이 못 씀")
    parser.add_argument("--drafts", type=Path, help="미리 적어 둔 초안 파일(replay용). --llm-provider 없이 주면 replay")
    parser.add_argument("--llm-provider", choices=(PROVIDER_REPLAY, PROVIDER_OLLAMA),
                        help="초안 생성기. 이것도 다른 drafter 옵션도 안 주면 설정 파일을 쓴다")
    parser.add_argument("--model-id", help="--llm-provider ollama일 때 필수. 실제 설치한 모델 태그")
    parser.add_argument("--base-url", help="Ollama 주소")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--timeout", type=float, help="Ollama 호출 타임아웃(초)")
    parser.add_argument("--expected-sha256", action="append", default=[], metavar="TYPE=HEX")
    return parser


def build_drafter(settings: DrafterSettings) -> ScenarioDrafter | None:
    """검증된 설정으로 drafter를 만든다. none이면 None(→ DRAFTER_NOT_CONFIGURED failed 파일)."""
    if settings.provider == PROVIDER_NONE:
        return None
    if settings.provider == PROVIDER_REPLAY:
        return ReplayScenarioDrafter.from_file(settings.drafts_path)
    return OllamaScenarioDrafter(OllamaDrafterConfig(**settings.ollama_options))


def _build_configured_drafter() -> ScenarioDrafter | None:
    """설정 모드: 설정 파일로 drafter를 만든다. 만드는 중 오류(초안 파일 내용 깨짐 등)도 파일을 쓰기 전에 ValueError."""
    config_path = resolve_config_path()
    settings = load_drafter_settings(config_path)
    try:
        return build_drafter(settings)
    except (DrafterError, ValueError, OSError) as error:
        # 메시지에는 파일·키 이름만 넣는다. 원인은 예외 체인으로 남긴다.
        key_name = DRAFTS_PATH_KEY if settings.provider == PROVIDER_REPLAY else MODEL_ID_KEY
        raise ValueError(
            f"{config_path.name} [llm]: {key_name} 설정으로 drafter를 만들지 못함"
        ) from error


def _cli_drafter_settings(args: argparse.Namespace) -> DrafterSettings | None:
    """drafter 옵션을 하나라도 줬으면 그 설정(CLI 모드), 하나도 안 줬으면 None(설정 파일 모드).

    CLI에서 준 옵션이 조용히 무시되는 조합은 ValueError(종료 코드 2, 파일 없음).
    """
    ollama_values = {
        key: getattr(args, dest) for dest, key in CLI_OLLAMA_KEY_BY_DEST.items() if getattr(args, dest) is not None
    }
    if args.llm_provider is None and args.drafts is None and not ollama_values:
        return None
    if args.config is not None:
        raise ValueError("--config와 drafter 옵션을 같이 줄 수 없다(--config가 무시된다)")
    provider = args.llm_provider
    if provider is None:
        if ollama_values:
            raise ValueError("ollama 옵션(--model-id 등)은 --llm-provider ollama와 같이 준다")
        provider = PROVIDER_REPLAY  # --drafts만 준 경우(기존 호환)
    if provider == PROVIDER_REPLAY:
        if ollama_values:
            raise ValueError("--llm-provider replay에는 ollama 옵션(--model-id 등)을 줄 수 없다")
        if args.drafts is None:
            raise ValueError("--llm-provider replay에는 --drafts가 필요하다")
        return parse_drafter_settings({"provider": provider, "drafts_path": str(args.drafts)}, CLI_SOURCE)
    if args.drafts is not None:
        raise ValueError("--llm-provider ollama에는 --drafts를 줄 수 없다")
    if args.model_id is None:
        raise ValueError("--llm-provider ollama에는 --model-id가 필요하다")
    return parse_drafter_settings({"provider": provider, **ollama_values}, CLI_SOURCE)


def main(argv: Sequence[str] | None = None) -> int:
    """종료 코드: 파일을 썼으면 0(결과 상태는 stdout JSON의 status), 쓰지 못했으면 2."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    args = _build_argument_parser().parse_args(argv)
    try:
        cli_settings = _cli_drafter_settings(args)
        # run()은 context에 실행 값만 받으므로 설정 파일 위치는 이 프로세스의 환경변수로 넘긴다(collector와 같은 방식).
        if args.config is not None:
            os.environ[CONFIG_PATH_ENV] = str(args.config)
        drafter = None if cli_settings is None else build_drafter(cli_settings)
        response = run(
            args.operation,
            {"vulnerability_candidates": args.candidates, "crawl_result": args.crawl_result},
            args.output_dir,
            {
                "run_root": args.run_root,
                "run_id": args.run_id,
                "iteration": args.iteration,
                "mode": args.mode,
                "expected_sha256": _parse_expected_sha256_args(args.expected_sha256),
            },
            drafter=drafter,
        )
    except (ValueError, DrafterError, OutputWriteError, OutputContractError, OSError) as error:
        logger.error("실행하지 못했다: %s", error)
        return EXIT_NO_FILE_WRITTEN
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    return EXIT_FILE_WRITTEN


if __name__ == "__main__":
    sys.exit(main())
