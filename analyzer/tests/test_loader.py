"""loader.load_crawl_result 입력 검증 테스트.

정상 샘플(crawl_sample.json)을 조금씩 망가뜨려서
  · 치명적 오류 → CrawlValidationError 발생
  · 사소 문제  → 예외 없이 warnings 로만 반환
되는지 확인한다.
"""
import copy
import json
import pathlib

import pytest

from analyzer.loader import CrawlValidationError, load_crawl_result

DATA = pathlib.Path(__file__).parent / "data"


def _sample() -> dict:
    return json.loads((DATA / "crawl_sample.json").read_text(encoding="utf-8"))


def _load(d: dict):
    return load_crawl_result(json.dumps(d))


# ─────────────────────────── 정상 경로 ───────────────────────────
def test_valid_sample_no_error():
    crawl, warnings = _load(_sample())
    assert crawl.run_id == "20260928-051203-a1b2"
    assert warnings == []


# ─────────────────────────── [A] 구조(Pydantic) ───────────────────────────
def test_unknown_field_is_critical():
    d = _sample()
    d["roles"][0]["pages"][0]["bogus"] = 1  # extra="forbid"
    with pytest.raises(CrawlValidationError) as e:
        _load(d)
    assert any("구조" in m for m in e.value.errors)


def test_missing_required_field_is_critical():
    d = _sample()
    del d["roles"][0]["pages"][0]["endpoint"]
    with pytest.raises(CrawlValidationError):
        _load(d)


# ─────────────────────────── [B] 치명적 ───────────────────────────
def test_bad_run_id_is_critical():
    d = _sample()
    d["run_id"] = "2026-9-28"
    with pytest.raises(CrawlValidationError) as e:
        _load(d)
    assert any("run_id" in m for m in e.value.errors)


def test_duplicate_page_id_is_critical():
    d = _sample()
    dup = copy.deepcopy(d["roles"][0]["pages"][0])  # page:1 두 번
    d["roles"][0]["pages"].append(dup)
    with pytest.raises(CrawlValidationError) as e:
        _load(d)
    assert any("page ID 중복" in m for m in e.value.errors)


def test_duplicate_request_id_is_critical():
    d = _sample()
    dup = copy.deepcopy(d["roles"][0]["requests"][0])  # request:1 두 번
    d["roles"][0]["requests"].append(dup)
    with pytest.raises(CrawlValidationError) as e:
        _load(d)
    assert any("request ID 중복" in m for m in e.value.errors)


def test_dangling_source_page_id_is_critical():
    d = _sample()
    d["roles"][0]["requests"][0]["source_page_id"] = "page:999"
    with pytest.raises(CrawlValidationError) as e:
        _load(d)
    assert any("source_page_id" in m for m in e.value.errors)


def test_bad_page_id_format_is_critical():
    d = _sample()
    d["roles"][0]["pages"][0]["id"] = "page:0"  # N은 1부터
    with pytest.raises(CrawlValidationError) as e:
        _load(d)
    assert any("page ID 형식" in m for m in e.value.errors)


def test_bad_outcome_is_critical():
    d = _sample()
    d["roles"][0]["pages"][0]["links"].append({
        "action_id": "link:x", "url": None, "endpoint": None,
        "text": None, "is_state_changing": False, "outcome": "teleported",
    })
    with pytest.raises(CrawlValidationError) as e:
        _load(d)
    assert any("outcome" in m for m in e.value.errors)


def test_duplicate_role_is_critical():
    d = _sample()
    dup = copy.deepcopy(d["roles"][0])
    # page/request ID 중복은 피하고 역할만 중복시킨다
    dup["pages"] = []
    dup["requests"] = []
    d["roles"].append(dup)
    with pytest.raises(CrawlValidationError) as e:
        _load(d)
    assert any("역할 중복" in m for m in e.value.errors)


# ─────────────────────────── [B] 사소(경고·진행) ───────────────────────────
def test_missing_title_is_minor():
    d = _sample()
    d["roles"][0]["pages"][0]["title"] = None
    crawl, warnings = _load(d)  # 예외 없음
    assert any("title 없음" in w for w in warnings)


def test_role_label_mismatch_is_minor():
    d = _sample()
    d["roles"][0]["pages"][0]["role"] = "admin"  # 소속은 guest
    crawl, warnings = _load(d)
    assert any("소속 역할" in w for w in warnings)


def test_bad_timestamp_is_minor():
    d = _sample()
    d["roles"][0]["requests"][0]["captured_at"] = "yesterday"
    crawl, warnings = _load(d)
    assert any("captured_at" in w for w in warnings)
