"""규칙 설정 로드. 커밋된 기본 파일·환경변수 경로·값 검증을 본다. 대상 앱 값은 레포 밖 설정에 두므로 여기선 자리표시자만 쓴다."""

import json
import shutil
from pathlib import Path

import pytest

from modules.access_analyzer import entrypoint as ep
from modules.access_analyzer.tests.helpers import (
    CRAWL_FIXTURE_DIR,
    CRAWL_INPUT_REL,
    RESULT_INPUT_REL,
    make_context,
    output_dir_for,
)
from modules.access_analyzer.utils import config as cfg

RESULT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "graph_query_result" / "two_owners.json"


def _write_toml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert cfg.load_rule_config(tmp_path / "nope.toml") == cfg.DEFAULT_RULE_CONFIG


def test_committed_file_matches_defaults() -> None:
    # 커밋된 파일은 앱 값 없이 기본값과 같아야 한다(대상 앱 값은 ACCESS_ANALYZER_CONFIG_PATH 복사본에).
    assert cfg.load_rule_config(cfg.CONFIG_PATH) == cfg.DEFAULT_RULE_CONFIG


def test_env_path_overrides_committed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_toml(tmp_path / "aa.toml", '[rules.vertical_access]\nrole_order = ["role:low", "role:high"]\ndenied_status_codes = [401, 403, 404]\nmax_candidates = 7\n')
    monkeypatch.setenv(cfg.CONFIG_PATH_ENV, str(path))
    loaded = cfg.load_rule_config()
    assert loaded.vertical_access.role_order == ("role:low", "role:high")
    assert loaded.vertical_access.denied_status_codes == frozenset({401, 403, 404})
    assert loaded.vertical_access.max_candidates == 7
    assert loaded.vertical_access.success_status_range == (200, 299)  # 안 적은 키는 기본값
    assert loaded.same_role_other_owner == cfg.DEFAULT_RULE_CONFIG.same_role_other_owner


def test_blank_env_falls_back_to_committed_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cfg.CONFIG_PATH_ENV, "   ")
    assert cfg.resolve_config_path() == cfg.CONFIG_PATH


@pytest.mark.parametrize(
    "body",
    [
        pytest.param('[rules.vertical_access]\nrole_order = "role:low"\n', id="role_order_not_list"),
        pytest.param('[rules.vertical_access]\nrole_order = ["role:a", "role:a"]\n', id="role_order_duplicate"),
        pytest.param('[rules.vertical_access]\nrole_order = ["role:a", ""]\n', id="role_order_empty_id"),
        pytest.param('[rules.vertical_access]\nsuccess_status_range = [299, 200]\n', id="range_reversed"),
        pytest.param('[rules.vertical_access]\nsuccess_status_range = [200]\n', id="range_one_value"),
        pytest.param('[rules.vertical_access]\ndenied_status_codes = [403, true]\n', id="denied_bool"),
        pytest.param('[rules.vertical_access]\ndenied_status_codes = [4030]\n', id="denied_out_of_http_range"),
        pytest.param('[rules.vertical_access]\nmax_candidates = 0\n', id="max_candidates_zero"),
        pytest.param('[rules.vertical_access]\nenabled = "yes"\n', id="enabled_string"),
        pytest.param('[rules.same_role_other_owner]\nmax_candidates = true\n', id="same_role_max_bool"),
    ],
)
def test_invalid_values_raise(tmp_path: Path, body: str) -> None:
    with pytest.raises(cfg.RuleConfigError):
        cfg.load_rule_config(_write_toml(tmp_path / "bad.toml", body))


def test_analyze_reports_invalid_config_as_failed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 설정이 틀리면 규칙 일부만 적용한 결과를 내지 않는다. 파일은 남기되 failed·data=null.
    bad = _write_toml(tmp_path / "bad.toml", '[rules.vertical_access]\nrole_order = ["role:a", "role:a"]\n')
    monkeypatch.setenv(cfg.CONFIG_PATH_ENV, str(bad))
    run_root = tmp_path / "run_demo_001"
    for relative, source in ((RESULT_INPUT_REL, RESULT_FIXTURE), (CRAWL_INPUT_REL, CRAWL_FIXTURE_DIR / "completed.json")):
        target = run_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    result = ep.run("analyze", [RESULT_INPUT_REL, CRAWL_INPUT_REL], output_dir_for(run_root), make_context(run_root))
    document = json.loads((output_dir_for(run_root) / "vulnerability_candidates.json").read_bytes())
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "CONFIG_INVALID"
    assert document["data"] is None
    assert [ref["artifact_type"] for ref in document["input_refs"]] == ["graph_query_result", "crawl_result"]
