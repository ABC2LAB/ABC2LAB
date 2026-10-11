"""Web 경계 검증 전용 CLI 대역. 운영 이미지·실제 pipeline 실행에는 포함하지 않는다."""

import argparse
import hashlib
import json
import os
import signal
import sys
import time
import tomllib
from pathlib import Path

STEPS = (
    ("collector.collect", "collector/crawl_result.json"),
    ("semantic_analyzer.analyze", "semantic_analyzer/semantic_analysis.json"),
    ("knowledge_graph.ingest", None),
    ("access_analyzer.prepare_queries", "access_analyzer/graph_query.json"),
    ("knowledge_graph.query", "knowledge_graph/graph_query_result.json"),
    ("access_analyzer.analyze", "access_analyzer/vulnerability_candidates.json"),
    ("scenario_generator.generate", "scenario_generator/test_scenarios.json"),
    ("safety_policy.evaluate", "safety_policy/safety_decisions.json"),
    ("verifier.verify", "verifier/verification_results.json"),
    ("knowledge_graph.apply_verification", None),
    ("reporter.report", "reporter/diagnosis_report.json"),
    ("reporter.evaluate", "reporter/evaluation_results.json"),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("mode", "target-url", "dataset-id", "matching-profile", "run-id", "runs-dir", "project-root"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    assert args.mode == "development"
    config_path = Path(os.environ["COLLECTOR_CONFIG_PATH"])
    configuration = config_path.read_bytes()
    assert tomllib.loads(configuration.decode())["target_url"] == args.target_url
    assert not Path(os.environ["COLLECTOR_SECRETS_PATH"]).read_bytes()
    for key, value in os.environ.items():
        if key.startswith("COLLECTOR_ACCOUNT_"):
            assert value.encode() not in configuration
            assert value not in sys.argv
    assert os.environ.get("COLLECTOR_ACCOUNT_LEAK_PASSWORD") is None
    run_root = Path(args.runs_dir) / args.run_id
    run_root.mkdir(mode=0o700)
    case = os.environ.get("LOCAL_WEB_TEST_RUNNER_CASE", "completed")
    fixtures = {}
    if os.environ.get("LOCAL_WEB_TEST_PUBLIC_ARTIFACTS") == "1":
        from public_artifacts import EvaluationFixtureSettings, make_artifacts, public_bytes
        fixtures = make_artifacts(args.run_id, args.target_url, case,
                                  evaluation_settings=EvaluationFixtureSettings(args.dataset_id, args.matching_profile))
    if case == "ignore_term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    steps = []
    for index, (name, suffix) in enumerate(STEPS):
        status = "failed" if index == 0 and case == "failed" else "skipped" if case == "failed" else "partial" if name in {"semantic_analyzer.analyze", "reporter.evaluate"} and case == "partial" else "completed"
        path = "artifacts/iteration-000/" + suffix if suffix and status != "skipped" else None
        if path:
            destination = run_root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            # JSON 파싱·진단 성공을 파일 존재로 추측하지 않는지 확인한다.
            artifact_type = destination.stem
            body = public_bytes(fixtures[artifact_type]) if artifact_type in fixtures else b"PUBLICATION_METADATA_ONLY"
            destination.write_bytes(body)
        steps.append({"step": name, "status": status, "artifact": path,
                      "sha256": hashlib.sha256(body).hexdigest() if path else None,
                      "error_count": 1 if status in {"failed", "partial"} else 0,
                      "error_codes": ["PASSWORD_SENTINEL"],
                      "detail": "LOGIN_SENTINEL", "control": {"report_path": "/private/SECRET"}})
    # 실제 프로세스·동시 실행·서버 종료를 검사하기 위한 명시적 테스트 gate.
    release = os.environ.get("LOCAL_WEB_TEST_RUNNER_RELEASE")
    if release:
        marker = os.environ.get("LOCAL_WEB_TEST_RUNNER_STARTED")
        if marker:
            Path(marker).touch()
        while not Path(release).exists():
            time.sleep(0.02)
    code = 2 if case == "failed" else 1 if case in {"partial", "session_failed"} else 0
    summary = {"run_id": args.run_id, "run_root": str(run_root), "mode": args.mode,
               "iteration": 0, "exit_code": code, "halted_by": "collector.collect" if case == "failed" else None,
               "session_window": "failed" if case == "session_failed" else "opened",
               "session_window_error": "PASSWORD_SENTINEL", "steps": steps}
    sys.stderr.write("PASSWORD_SENTINEL LOGIN_SENTINEL stderr must not be exposed\n")
    if case == "bad_json":
        sys.stdout.write("PASSWORD_SENTINEL invalid json")
    elif case == "oversized":
        sys.stdout.write("x" * (1024 * 1024 + 1))
    elif case != "missing_summary":
        sys.stdout.write(json.dumps(summary) + "\n")
    return 2 if case == "exit_mismatch" else code


if __name__ == "__main__":
    raise SystemExit(main())
