"""Public function and CLI for knowledge graph operations."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from modules.knowledge_graph.exceptions import (
    ContractValidationError,
    GraphAlreadyExistsError,
    GraphStorageVerificationError,
    InputArtifactFailedError,
    InputHashMismatchError,
    OutputArtifactExistsError,
    PathValidationError,
    RepositoryError,
    SourceArtifactConflictError,
)
from modules.knowledge_graph.ingest_adapter import parse_ingest_request
from modules.knowledge_graph.models import (
    ControlError,
    IngestControlResponse,
    QueryControlResponse,
)
from modules.knowledge_graph.neo4j_repository import Neo4jGraphRepository
from modules.knowledge_graph.query_adapter import parse_query_request
from modules.knowledge_graph.service import (
    PreparedQuery,
    build_query_dependency_failure,
    execute_ingest,
    execute_query,
    prepare_ingest_operation,
    prepare_query_operation,
    publish_query_artifact,
)
from modules.knowledge_graph.settings import Neo4jSettings


LOGGER = logging.getLogger(__name__)


def run(
    operation: str,
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    if operation == "ingest":
        return _run_ingest(input_paths, output_dir, context)
    if operation == "query":
        return _run_query(input_paths, output_dir, context)
    return {
        "operation": operation,
        "status": "failed",
        "errors": [
            ControlError(
                code="OPERATION_UNSUPPORTED",
                message="지원하지 않는 knowledge_graph operation",
                item_ref=None,
                retryable=False,
            ).to_mapping()
        ],
    }


def _run_ingest(
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        request = parse_ingest_request(input_paths, output_dir, context)
        prepared = prepare_ingest_operation(request)
    except (ContractValidationError, PathValidationError, OSError) as error:
        return _input_failure_response(error)
    except Exception:
        LOGGER.exception("예상하지 못한 ingest 입력 준비 실패")
        return _failed_response(
            "INGEST_FAILED",
            "예상하지 못한 ingest 입력 처리 오류가 발생함",
            retryable=True,
        )

    try:
        settings = Neo4jSettings.from_environment()
    except ContractValidationError as error:
        LOGGER.warning("Neo4j 설정 검증 실패: %s", error)
        return _failed_response(
            "CONFIG_INVALID",
            "Neo4j 연결 설정이 누락됐거나 올바르지 않음",
            retryable=False,
        )

    try:
        with Neo4jGraphRepository(settings) as repository:
            repository.verify_connectivity()
            return execute_ingest(prepared, repository).to_mapping()
    except RepositoryError as error:
        return _repository_failure_response(error)
    except Exception:
        LOGGER.exception("예상하지 못한 knowledge_graph ingest 실패")
        return _failed_response(
            "INGEST_FAILED",
            "예상하지 못한 ingest 오류가 발생함",
            retryable=True,
        )


def _run_query(
    input_paths: Mapping[str, Any],
    output_dir: str | Path,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        request = parse_query_request(input_paths, output_dir, context)
        prepared = prepare_query_operation(request)
    except (ContractValidationError, PathValidationError, OSError) as error:
        return _query_input_failure_response(error)
    except Exception:
        LOGGER.exception("예상하지 못한 query 입력 준비 실패")
        return _failed_query_control_response(
            "QUERY_FAILED",
            "예상하지 못한 query 입력 처리 오류가 발생함",
            retryable=True,
        )

    try:
        settings = Neo4jSettings.from_environment()
    except ContractValidationError:
        artifact = build_query_dependency_failure(
            prepared,
            "CONFIG_INVALID",
            "Neo4j 연결 설정이 누락됐거나 올바르지 않음",
            retryable=False,
        )
        return _publish_query(prepared, artifact)

    try:
        with Neo4jGraphRepository(settings) as repository:
            artifact = execute_query(prepared, repository)
    except RepositoryError as error:
        LOGGER.warning("Neo4j query 실패: %s", error)
        artifact = build_query_dependency_failure(
            prepared,
            "NEO4J_UNAVAILABLE",
            "Neo4j가 query 요청을 완료하지 못함",
            retryable=True,
        )
    except Exception:
        LOGGER.exception("예상하지 못한 knowledge_graph query 실패")
        artifact = build_query_dependency_failure(
            prepared,
            "QUERY_FAILED",
            "예상하지 못한 query 오류가 발생함",
            retryable=True,
        )
    return _publish_query(prepared, artifact)


def _input_failure_response(error: Exception) -> dict[str, Any]:
    if isinstance(error, InputArtifactFailedError):
        return _failed_response(
            "INPUT_STATUS_FAILED",
            "semantic_analysis가 사용할 수 없는 failed 상태임",
            retryable=False,
        )
    if isinstance(error, InputHashMismatchError):
        return _failed_response(
            "INPUT_HASH_MISMATCH",
            "semantic_analysis 파일 해시가 전달값과 다름",
            retryable=False,
        )
    if isinstance(error, (PathValidationError, OSError)):
        LOGGER.warning("ingest 입력 경로 검증 실패: %s", error)
        return _failed_response(
            "PATH_INVALID",
            "semantic_analysis 또는 실행 경로에 접근할 수 없음",
            retryable=False,
        )
    LOGGER.warning("ingest 입력 계약 검증 실패: %s", error)
    return _failed_response(
        "CONTRACT_INVALID",
        "semantic_analysis 또는 실행 인자가 계약을 위반함",
        retryable=False,
    )


def _repository_failure_response(error: RepositoryError) -> dict[str, Any]:
    if isinstance(error, SourceArtifactConflictError):
        return _failed_response(
            "ARTIFACT_CONFLICT",
            "동일 semantic artifact_id가 다른 파일 해시로 사용됨",
            retryable=False,
        )
    if isinstance(error, GraphAlreadyExistsError):
        return _failed_response(
            "GRAPH_CONFLICT",
            "생성한 graph_id가 현재 run 범위에서 충돌함",
            retryable=True,
        )
    if isinstance(error, GraphStorageVerificationError):
        return _failed_response(
            "STORAGE_VERIFICATION_FAILED",
            "Neo4j 적재 결과가 semantic_analysis 입력과 일치하지 않음",
            retryable=False,
        )
    LOGGER.warning("Neo4j ingest 실패: %s", error)
    return _failed_response(
        "NEO4J_UNAVAILABLE",
        "Neo4j가 ingest 요청을 완료하지 못함",
        retryable=True,
    )


def _query_input_failure_response(error: Exception) -> dict[str, Any]:
    if isinstance(error, InputArtifactFailedError):
        return _failed_query_control_response(
            "INPUT_STATUS_FAILED",
            "graph_query가 사용할 수 없는 failed 상태임",
            retryable=False,
        )
    if isinstance(error, InputHashMismatchError):
        return _failed_query_control_response(
            "INPUT_HASH_MISMATCH",
            "graph_query 파일 해시가 전달값과 다름",
            retryable=False,
        )
    if isinstance(error, (PathValidationError, OSError)):
        LOGGER.warning("query 입력 경로 검증 실패: %s", error)
        return _failed_query_control_response(
            "PATH_INVALID",
            "graph_query 또는 실행 경로에 접근할 수 없음",
            retryable=False,
        )
    LOGGER.warning("query 입력 계약 검증 실패: %s", error)
    return _failed_query_control_response(
        "CONTRACT_INVALID",
        "graph_query 또는 실행 인자가 계약을 위반함",
        retryable=False,
    )


def _publish_query(
    prepared: PreparedQuery,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    try:
        return publish_query_artifact(prepared, artifact).to_mapping()
    except OutputArtifactExistsError:
        return _failed_query_control_response(
            "OUTPUT_EXISTS",
            "불변 graph_query_result 출력 경로가 이미 존재함",
            retryable=False,
            graph_id=prepared.graph_id,
        )
    except ContractValidationError as error:
        LOGGER.warning("query 출력 계약 검증 실패: %s", error)
        return _failed_query_control_response(
            "OUTPUT_INVALID",
            "생성한 graph_query_result가 출력 계약을 위반함",
            retryable=False,
            graph_id=prepared.graph_id,
        )
    except OSError as error:
        LOGGER.warning("query 출력 저장 실패: %s", error)
        return _failed_query_control_response(
            "OUTPUT_WRITE_FAILED",
            "graph_query_result를 원자적으로 저장하지 못함",
            retryable=True,
            graph_id=prepared.graph_id,
        )


def _failed_query_control_response(
    code: str,
    message: str,
    retryable: bool,
    graph_id: str | None = None,
) -> dict[str, Any]:
    return QueryControlResponse(
        status="failed",
        artifact_id=None,
        output_path=None,
        sha256=None,
        graph_id=graph_id,
        graph_revision=None,
        errors=(
            ControlError(
                code=code,
                message=message,
                item_ref=None,
                retryable=retryable,
            ),
        ),
    ).to_mapping()


def _failed_response(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return IngestControlResponse(
        status="failed",
        graph_id=None,
        graph_revision=None,
        is_ready=False,
        errors=(
            ControlError(
                code=code,
                message=message,
                item_ref=None,
                retryable=retryable,
            ),
        ),
    ).to_mapping()


def _create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledge_graph")
    parser.add_argument("operation", choices=("ingest", "query"))
    parser.add_argument("--input-path", required=True)
    parser.add_argument("--input-sha256", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--iteration", required=True, type=int)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("diagnosis", "development"),
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _create_argument_parser().parse_args(arguments)
    input_key = "semantic_analysis" if parsed.operation == "ingest" else "graph_query"
    response = run(
        operation=parsed.operation,
        input_paths={
            input_key: {
                "path": parsed.input_path,
                "sha256": parsed.input_sha256,
            }
        },
        output_dir=parsed.output_dir,
        context={
            "run_id": parsed.run_id,
            "iteration": parsed.iteration,
            "mode": parsed.mode,
            "run_root": parsed.run_root,
        },
    )
    json.dump(response, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 1 if response["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
