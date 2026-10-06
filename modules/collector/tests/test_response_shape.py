import json

import pytest

from modules.collector.core.response_shape import MAX_SHAPE_DEPTH, MAX_SHAPE_KEYS, TRUNCATED_KEY, describe_json_shape

SAMPLE_UUID = "550e8400-e29b-41d4-a716-446655440000"
SAMPLE_EMAIL = "alice@example.com"
HEX_TOKEN = "5f4dcc3b5aa765d61d8327deb882cf99"
BASE64_TOKEN = "aZ3kP9qL2mX7vB4n"
BASE64URL_TOKEN = "Xk9_2hQpLm-3vRtY8wZs"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("x", "str", id="str"),
        pytest.param(3, "int", id="int"),
        pytest.param(1.5, "float", id="float"),
        pytest.param(True, "bool", id="bool_is_not_int"),
        pytest.param(None, "null", id="null"),
    ],
)
def test_scalar_type_names(value: object, expected: str) -> None:
    assert describe_json_shape(value) == expected


def test_nested_object_example() -> None:
    value = {"id": 1, "email": SAMPLE_EMAIL, "items": [{"id": 2}, {"id": 3}]}

    assert describe_json_shape(value) == {"id": "int", "email": "str", "items": [{"id": "int"}]}


def test_array_elements_merged_into_one_shape() -> None:
    value = [{"id": 1, "name": "a"}, {"id": 2, "price": 9.5}]

    assert describe_json_shape(value) == [{"id": "int", "name": "str", "price": "float"}]


def test_different_scalar_types_joined_sorted() -> None:
    value = [{"code": 1}, {"code": "A"}, {"code": None}]

    assert describe_json_shape(value) == [{"code": "int|null|str"}]


def test_structure_kept_when_mixed_with_null() -> None:
    value = [{"owner": {"id": 1}}, {"owner": None}]

    assert describe_json_shape(value) == [{"owner": {"id": "int"}}]


def test_structure_mixed_with_scalar_becomes_type_names() -> None:
    value = [{"owner": {"id": 1}}, {"owner": 7}, {"owner": [1]}]

    assert describe_json_shape(value) == [{"owner": "dict|int|list"}]


def test_empty_containers_and_scalar_arrays() -> None:
    assert describe_json_shape({"tags": [], "meta": {}, "ids": [1, 2]}) == {"tags": [], "meta": {}, "ids": ["int"]}
    assert describe_json_shape([]) == []
    assert describe_json_shape([[], [1]]) == [["int"]]


@pytest.mark.parametrize(
    ("key", "placeholder"),
    [
        pytest.param("42", "{id}", id="numeric"),
        pytest.param(SAMPLE_UUID, "{id}", id="uuid"),
        pytest.param(SAMPLE_EMAIL, "{email}", id="email"),
        pytest.param("2026-09-28", "{date}", id="date_dash"),
        pytest.param("2026/09/28", "{date}", id="date_slash"),
        pytest.param("2026.09.28", "{date}", id="date_dot"),
        pytest.param("2026-09", "{date}", id="year_month"),
        pytest.param("2026-09-28T10:30:00Z", "{date}", id="iso_datetime"),
        pytest.param(HEX_TOKEN, "{token}", id="hex"),
        pytest.param(HEX_TOKEN.upper(), "{token}", id="hex_upper"),
        pytest.param(BASE64_TOKEN, "{token}", id="base64"),
        pytest.param(BASE64URL_TOKEN, "{token}", id="base64url"),
        pytest.param(f"{BASE64_TOKEN}==", "{token}", id="base64_padding"),
    ],
)
def test_value_like_keys_replaced(key: str, placeholder: str) -> None:
    assert describe_json_shape({key: 1}) == {placeholder: "int"}


@pytest.mark.parametrize(
    "key",
    [
        "created_at",
        "userName",
        "billingAddressLine2",
        "address_line_1_extra",
        "item_count",
        "v2",
        # 16자 미만 무작위 모양은 그대로 둔다
        BASE64_TOKEN[:-1],
        # 숫자 없는 긴 camelCase
        "createdByUserName",
    ],
)
def test_ordinary_keys_kept(key: str) -> None:
    assert describe_json_shape({key: 1}) == {key: "int"}


def test_replaced_keys_merged() -> None:
    value = {"1": {"name": "a"}, "2": {"price": 3}, "3": {"name": None}}

    assert describe_json_shape(value) == {"{id}": {"name": "null|str", "price": "int"}}


def test_depth_limit_cuts_to_type_name() -> None:
    value: object = 1
    for _ in range(MAX_SHAPE_DEPTH):
        value = {"child": value}
    deeper = {"child": [value]}

    shape = describe_json_shape(deeper)
    for _ in range(MAX_SHAPE_DEPTH):
        assert isinstance(shape, (dict, list))
        shape = shape["child"] if isinstance(shape, dict) else shape[0]
    assert shape == "dict"


def test_key_count_limit_marks_truncation() -> None:
    value = {f"field_{index}": index for index in range(MAX_SHAPE_KEYS + 5)}

    shape = describe_json_shape(value)

    assert isinstance(shape, dict)
    assert len(shape) == MAX_SHAPE_KEYS + 1
    assert shape[TRUNCATED_KEY] == "truncated"
    assert "field_0" in shape and f"field_{MAX_SHAPE_KEYS}" not in shape


def test_key_count_limit_applies_after_merging() -> None:
    value = [{f"a_{index}": 1} for index in range(MAX_SHAPE_KEYS + 5)]

    shape = describe_json_shape(value)

    assert isinstance(shape, list)
    assert len(shape[0]) == MAX_SHAPE_KEYS + 1
    assert shape[0][TRUNCATED_KEY] == "truncated"


def test_values_never_appear_in_shape() -> None:
    secret_values = ("bob-private-note", "314159", "2.71828", SAMPLE_EMAIL, HEX_TOKEN)
    value = {
        "note": "bob-private-note",
        "pin": 314159,
        "ratio": 2.71828,
        "contacts": [{"email": SAMPLE_EMAIL}, {SAMPLE_EMAIL: True}],
        "session": {HEX_TOKEN: HEX_TOKEN},
    }

    text = json.dumps(describe_json_shape(value))

    for secret in secret_values:
        assert secret not in text
