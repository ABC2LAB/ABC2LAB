"""응답 JSON 식별자 추출 규칙(core/identifiers.py)을 표로 확인한다."""

import pytest

from modules.collector.core import identifiers
from modules.collector.core.capture import SENSITIVE_KEY_PARTS, list_secret_variants
from modules.collector.core.identifiers import (
    Identifier,
    extract_identifiers,
    is_identifier_key,
    to_identifier_value,
)

UUID_VALUE = "0B6F2C1E-8D4A-4C3E-9F1A-2D5E7C8B9A01"
NUMERIC_PASSWORD = "20261006"


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def scan(document: object, secrets: tuple[str, ...] = ()) -> list[tuple[str, object]]:
    result = extract_identifiers(document, is_sensitive_key, list_secret_variants(secrets))
    return [(identifier.pointer, identifier.value) for identifier in result.identifiers]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("id", True),
        ("ID", True),
        ("Id", True),
        ("user_id", True),
        ("USER_ID", True),
        ("_id", True),
        ("userId", True),
        ("userID", True),
        ("UserID", True),
        ("valid", False),
        ("paid", False),
        ("userid", False),
        ("uid", False),
        ("USERID", False),
        ("identity", False),
        ("idx", False),
        ("item2Id", False),
    ],
)
def test_identifier_key_rule(key: str, expected: bool) -> None:
    assert is_identifier_key(key) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(7, 7, id="int"),
        pytest.param(0, 0, id="zero"),
        pytest.param("42", "42", id="digit_string_kept_as_string"),
        pytest.param("007", "007", id="leading_zeros"),
        pytest.param(UUID_VALUE, UUID_VALUE, id="uuid_uppercase"),
        pytest.param(True, None, id="bool"),
        pytest.param(7.0, None, id="float"),
        pytest.param(-1, None, id="negative"),
        pytest.param(" 42", None, id="leading_space"),
        pytest.param("4 2", None, id="inner_space"),
        pytest.param("4.2", None, id="decimal_string"),
        pytest.param("-1", None, id="negative_string"),
        pytest.param("", None, id="empty"),
        pytest.param("alice", None, id="word"),
        pytest.param(None, None, id="null"),
        pytest.param({"id": 1}, None, id="object"),
    ],
)
def test_identifier_value_rule(value: object, expected: object) -> None:
    converted = to_identifier_value(value)

    assert converted == expected
    assert type(converted) is type(expected)


def test_ids_in_nested_arrays_keep_document_order() -> None:
    document = {
        "items": [
            {"id": 1, "name": "first", "owner_id": "2"},
            {"id": 3, "tags": [{"tag_id": 4, "label": "x"}], "categoryId": UUID_VALUE},
        ],
        "total": 2,
    }

    assert scan(document) == [
        ("/items/0/id", 1),
        ("/items/0/owner_id", "2"),
        ("/items/1/id", 3),
        ("/items/1/tags/0/tag_id", 4),
        ("/items/1/categoryId", UUID_VALUE),
    ]


def test_top_level_array_and_scalar() -> None:
    assert scan([{"id": 5}]) == [("/0/id", 5)]
    assert scan(5) == []
    assert scan(None) == []


def test_pointer_tokens_escaped() -> None:
    document = {"a/b": {"id": 1}, "m~n": {"id": 2}, "~/": {"id": 3}}

    assert scan(document) == [("/a~1b/id", 1), ("/m~0n/id", 2), ("/~0~1/id", 3)]


def test_sensitive_keys_and_their_subtrees_skipped() -> None:
    document = {"session_id": 9, "token_id": 8, "csrf": {"id": 7}, "session": [{"id": 6}], "order_id": 5}

    assert scan(document) == [("/order_id", 5)]


def test_values_that_are_not_ids_skipped() -> None:
    document = {"id": True, "user_id": 7.0, "item_id": -1, "group_id": " 42", "team_id": "4.2", "org_id": "acme"}

    assert scan(document) == []


def test_account_password_never_kept() -> None:
    document = {"id": int(NUMERIC_PASSWORD), "user_id": NUMERIC_PASSWORD, NUMERIC_PASSWORD: {"id": 1}, "order_id": 3}

    assert scan(document, secrets=(NUMERIC_PASSWORD,)) == [("/order_id", 3)]


def test_count_limit_marks_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identifiers, "MAX_IDENTIFIERS", 3)
    exactly_full = {"items": [{"id": index} for index in range(3)]}
    over_full = {"items": [{"id": index} for index in range(4)]}

    full_result = extract_identifiers(exactly_full, is_sensitive_key, ())
    over_result = extract_identifiers(over_full, is_sensitive_key, ())

    assert (len(full_result.identifiers), full_result.is_truncated) == (3, False)
    assert over_result.identifiers == tuple(Identifier(f"/items/{index}/id", index) for index in range(3))
    assert over_result.is_truncated is True


def test_depth_limit_marks_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identifiers, "MAX_IDENTIFIER_DEPTH", 2)
    within = {"a": {"id": 1}}
    beyond = {"a": {"b": {"id": 2}}, "id": 3}
    empty_beyond = {"a": {"b": {}}}

    within_result = extract_identifiers(within, is_sensitive_key, ())
    beyond_result = extract_identifiers(beyond, is_sensitive_key, ())
    empty_result = extract_identifiers(empty_beyond, is_sensitive_key, ())

    assert (within_result.identifiers, within_result.is_truncated) == ((Identifier("/a/id", 1),), False)
    assert (beyond_result.identifiers, beyond_result.is_truncated) == ((Identifier("/id", 3),), True)
    # 못 본 안쪽이 비어 있으면 빠진 것이 없다.
    assert empty_result.is_truncated is False
