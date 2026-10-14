"""응답 내용 비교 도구: 보이는 텍스트 줄·coverage, 식별자 추출(collector와 같은 기준). 리터럴 입력으로 규칙 하나씩 본다."""

import pytest

from modules.verifier.utils.evidence import is_sensitive_key
from modules.verifier.utils.identifiers import extract_identifiers
from modules.verifier.utils.text_content import coverage, visible_lines


def test_visible_lines_keep_text_in_document_order() -> None:
    html = "<html><head><title>Order</title></head><body><h2>Order #1</h2><p>Total: 3,000</p></body></html>"
    assert visible_lines(html) == ["Order", "Order #1", "Total: 3,000"]


def test_hidden_element_text_is_dropped() -> None:
    # 스크립트 상수·스타일은 화면에 안 보이고 요청마다 바뀔 수 있다. 빼지 않으면 비교가 흔들린다.
    html = (
        "<body><script>var csrf = 'abc123';</script><style>.x{color:red}</style>"
        "<noscript>enable js</noscript><template><p>tpl</p></template><p>visible</p></body>"
    )
    assert visible_lines(html) == ["visible"]


def test_attribute_values_are_not_text() -> None:
    # hidden input의 토큰 값·링크 주소는 속성이라 텍스트 줄에 들어가지 않는다.
    html = '<form><input type="hidden" name="csrf" value="tok-1"><a href="/orders/7">detail</a></form>'
    assert visible_lines(html) == ["detail"]


def test_whitespace_is_collapsed_and_entities_decoded() -> None:
    html = "<p>  Tom   &amp;\n  Jerry  </p><p>   </p>"
    assert visible_lines(html) == ["Tom &", "Jerry"]


def test_plain_text_splits_on_lines() -> None:
    assert visible_lines("first line\n\n  second   line \n") == ["first line", "second line"]


def test_coverage_counts_baseline_lines_found_in_subject() -> None:
    # 분모는 기준 줄 수다(기준 → 실행 방향). 기준 4줄 중 3줄이 실행 응답에 있으면 0.75.
    baseline = ["nav", "Bob(user)", "Order #1", "Total"]
    subject = ["nav", "Alice(user)", "Order #1", "Total", "extra line"]
    assert coverage(baseline, subject) == 0.75


def test_coverage_counts_duplicates_as_multiset() -> None:
    # 같은 줄이 기준에 두 번 있으면 실행 응답에도 두 번 있어야 다 덮인다(집합으로 세면 1.0이 된다).
    assert coverage(["row", "row"], ["row"]) == 0.5


def test_coverage_without_baseline_lines_is_none() -> None:
    assert coverage([], ["anything"]) is None


def test_identifier_keys_follow_collector_rule() -> None:
    body = {"id": 1, "user_id": 2, "ownerId": 3, "ownerID": 4, "ID": 5, "valid": 6, "paid": 7, "name": 8}
    assert extract_identifiers(body, is_sensitive_key) == {"/id": 1, "/user_id": 2, "/ownerId": 3, "/ownerID": 4, "/ID": 5}


@pytest.mark.parametrize(
    "value, expected",
    [
        (7, {"/id": 7}),
        ("42", {"/id": "42"}),  # 원래 JSON 타입(문자열)을 그대로 둔다
        ("123e4567-e89b-12d3-a456-426614174000", {"/id": "123e4567-e89b-12d3-a456-426614174000"}),
        (True, {}),  # bool은 int 하위 클래스지만 식별자가 아니다
        (-1, {}),
        ("abc", {}),
        (1.5, {}),
    ],
)
def test_identifier_values_follow_collector_rule(value: object, expected: dict) -> None:
    assert extract_identifiers({"id": value}, is_sensitive_key) == expected


def test_sensitive_key_and_its_subtree_are_skipped() -> None:
    body = {"session_id": 9, "session": {"id": 5}, "order": {"id": 1}}
    assert extract_identifiers(body, is_sensitive_key) == {"/order/id": 1}


def test_pointers_cover_lists_and_escape_tokens() -> None:
    body = {"items": [{"id": 3}, {"id": 4}], "a/b": {"id": 5}}
    assert extract_identifiers(body, is_sensitive_key) == {"/items/0/id": 3, "/items/1/id": 4, "/a~1b/id": 5}


def test_non_container_document_has_no_identifiers() -> None:
    assert extract_identifiers("<html>id=3</html>", is_sensitive_key) == {}
