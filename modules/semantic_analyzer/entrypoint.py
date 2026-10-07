"""semantic_analyzer 공개 실행 창구.

공개 함수: run(operation, input_paths, output_dir, context) → 결과 요약 dict.
operation은 "analyze"만 지원한다. 입력 검증 → 처리 → 출력 검증 → 원자적 저장 → 완료/실패 통지를
이 모듈 안에서 끝낸다. 다른 모듈을 import하거나 실행하지 않는다.

CLI:
    python -m modules.semantic_analyzer.entrypoint analyze \
        --input <crawl_result.json> --output-dir <dir> [--run-id ...] [--iteration N] [--mode ...]
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from modules.semantic_analyzer.llm.adapter import LlmClient, LlmError
from modules.semantic_analyzer.llm.factory import build_llm_client
from modules.semantic_analyzer.service import analyze_data
from modules.semantic_analyzer.utils import envelope as env
from modules.semantic_analyzer.utils.io import read_json, write_json_atomic
from modules.semantic_analyzer.utils.validation import (
    check_referential_integrity,
    validate_crawl_result,
    validate_semantic_analysis,
)

logger = logging.getLogger(__name__)

OPERATION_ANALYZE = "analyze"
OUTPUT_FILENAME = "semantic_analysis.json"
_EMPTY_DATA = {
    "normalized_requests": [], "nodes": [], "relationships": [], "workflows": [], "model_info": None,
}


def run(operation: str, input_paths: dict | list | str, output_dir: str | Path,
        context: dict | None = None, *, client: LlmClient | None = None) -> dict:
    """분석을 한 번 수행하고 결과 요약을 돌려준다. 실패도 계약(failed/data=null)으로 공개한다."""
    if operation != OPERATION_ANALYZE:
        raise ValueError(f"지원하지 않는 operation: {operation!r} (지원: {OPERATION_ANALYZE})")
    context = context or {}
    client = client or build_llm_client()  # 설정(configs/default.toml)의 provider. 기본 fake
    output_path = Path(output_dir) / OUTPUT_FILENAME

    crawl_path = _select_input_path(input_paths)
    crawl, input_errors = _load_and_validate_input(crawl_path)
    if input_errors:
        return _publish(output_path, _failed_envelope(context, crawl, input_errors))

    try:
        data = analyze_data(crawl["data"], client)
    except LlmError as error:
        # LLM 추론 실패는 가짜 정상으로 숨기지 않고 failed로 공개한다(절대 규칙 9).
        return _publish(output_path, _failed_envelope(
            context, crawl, [_error("LLM_INFERENCE_FAILED", f"LLM 추론 실패: {error}")]))
    output_errors = _validate_output_data(data)
    if output_errors:
        return _publish(output_path, _failed_envelope(context, crawl, output_errors))

    status = env.STATUS_PARTIAL if crawl["status"] == "partial" else env.STATUS_COMPLETED
    errors = _upstream_partial_errors(crawl) if status == env.STATUS_PARTIAL else []
    result = env.build_envelope(
        artifact_id=_artifact_id(context, crawl),
        run_id=_run_id(context, crawl),
        iteration=_iteration(context, crawl),
        mode=_mode(context, crawl),
        status=status,
        created_at=context.get("now") or env.utc_now_rfc3339(),
        input_refs=[_input_ref(context, crawl, crawl_path)],
        errors=errors,
        runtime_metrics=env.null_runtime_metrics(),
        data=data,
    )
    return _publish(output_path, result)


# ── 입력 ──────────────────────────────────────────────────────────────────────
def _select_input_path(input_paths: dict | list | str) -> Path:
    if isinstance(input_paths, dict):
        value = input_paths.get("crawl_result") or next(iter(input_paths.values()))
        return Path(value)
    if isinstance(input_paths, (list, tuple)):
        return Path(input_paths[0])
    return Path(input_paths)


def _load_and_validate_input(crawl_path: Path) -> tuple[dict, list[dict]]:
    try:
        crawl, _ = read_json(crawl_path)
    except (OSError, json.JSONDecodeError) as error:
        return {}, [_error("INPUT_UNREADABLE", f"입력을 읽지 못함: {error}")]
    schema_errors = validate_crawl_result(crawl)
    if schema_errors:
        return crawl, [_error("CONTRACT_INVALID", msg) for msg in schema_errors[:20]]
    if crawl["status"] == "failed" or crawl["data"] is None:
        return crawl, [_error("INPUT_UPSTREAM_FAILED", "입력 crawl_result가 failed/data=null이다")]
    return crawl, []


# ── 출력 검증 ──────────────────────────────────────────────────────────────────
def _validate_output_data(data: dict) -> list[dict]:
    reference_errors = check_referential_integrity(data)
    if reference_errors:
        return [_error("OUTPUT_INVALID", msg) for msg in reference_errors[:20]]
    return []


def _publish(output_path: Path, result: dict) -> dict:
    schema_errors = validate_semantic_analysis(result)
    if schema_errors:
        # 자기 출력이 Schema를 못 지키면 버그다. completed로 공개하지 않는다.
        raise AssertionError("출력 Schema 위반:\n- " + "\n- ".join(schema_errors[:20]))
    sha256 = write_json_atomic(output_path, result)
    summary = _summary(result)
    logger.info("semantic_analyzer %s → %s (%s)", result["status"], output_path, summary)
    return {
        "status": result["status"],
        "output_path": str(output_path),
        "artifact_id": result["artifact_id"],
        "sha256": sha256,
        "errors": len(result["errors"]),
        "summary": summary,
    }


def _summary(result: dict) -> dict:
    data = result["data"] or _EMPTY_DATA
    return {
        "normalized_requests": len(data["normalized_requests"]),
        "nodes": len(data["nodes"]),
        "relationships": len(data["relationships"]),
        "workflows": len(data["workflows"]),
    }


# ── envelope 조립 헬퍼 ──────────────────────────────────────────────────────────
def _failed_envelope(context: dict, crawl: dict, errors: list[dict]) -> dict:
    return env.build_envelope(
        artifact_id=_artifact_id(context, crawl),
        run_id=_run_id(context, crawl),
        iteration=_iteration(context, crawl),
        mode=_mode(context, crawl),
        status=env.STATUS_FAILED,
        created_at=context.get("now") or env.utc_now_rfc3339(),
        input_refs=[],
        errors=errors,
        runtime_metrics=env.null_runtime_metrics(),
        data=None,
    )


def _upstream_partial_errors(crawl: dict) -> list[dict]:
    return [_error("INPUT_UPSTREAM_PARTIAL", "입력 crawl_result가 partial이다(유효 data만 처리)")]


def _run_id(context: dict, crawl: dict) -> str:
    return context.get("run_id") or crawl.get("run_id") or "unknown-run"


def _iteration(context: dict, crawl: dict) -> int:
    value = context.get("iteration")
    return value if value is not None else int(crawl.get("iteration", 0))


def _mode(context: dict, crawl: dict) -> str:
    return context.get("mode") or crawl.get("mode") or "diagnosis"


def _artifact_id(context: dict, crawl: dict) -> str:
    return (context.get("artifact_id")
            or f"{env.PRODUCER}-{_run_id(context, crawl)}-iter{_iteration(context, crawl)}")


def _input_ref(context: dict, crawl: dict, crawl_path: Path) -> dict:
    _, sha256 = read_json(crawl_path)
    run_root = context.get("run_root")
    path_value = _relative_path(run_root, crawl_path) if run_root else crawl_path.name
    return env.ArtifactRef(
        artifact_id=crawl.get("artifact_id", "unknown"),
        artifact_type=crawl.get("artifact_type", "crawl_result"),
        iteration=int(crawl.get("iteration", 0)),
        path=path_value,
        sha256=sha256,
    ).to_dict()


def _relative_path(run_root: str, crawl_path: Path) -> str:
    try:
        return str(crawl_path.resolve().relative_to(Path(run_root).resolve()))
    except ValueError:
        return crawl_path.name


def _error(code: str, message: str, item_ref: str | None = None) -> dict:
    return {"code": code, "message": message, "item_ref": item_ref, "retryable": False}


# ── CLI ────────────────────────────────────────────────────────────────────────
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="semantic_analyzer")
    parser.add_argument("operation", choices=[OPERATION_ANALYZE])
    parser.add_argument("--input", required=True, help="crawl_result.json 경로")
    parser.add_argument("--output-dir", required=True, help="semantic_analysis.json을 쓸 디렉토리")
    parser.add_argument("--run-id")
    parser.add_argument("--iteration", type=int)
    parser.add_argument("--mode", choices=["diagnosis", "development"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)
    context = {k: v for k, v in (
        ("run_id", args.run_id), ("iteration", args.iteration), ("mode", args.mode),
    ) if v is not None}
    result = run(args.operation, {"crawl_result": args.input}, args.output_dir, context)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in (env.STATUS_COMPLETED, env.STATUS_PARTIAL) else 1


if __name__ == "__main__":
    raise SystemExit(main())
