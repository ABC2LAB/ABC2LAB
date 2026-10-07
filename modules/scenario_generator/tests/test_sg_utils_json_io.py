import pytest

from modules.scenario_generator.utils.json_io import (
    InvalidJsonError,
    dump_json_bytes,
    parse_json_strict,
)


def test_valid_json_is_parsed() -> None:
    assert parse_json_strict(b'{"a": [1, null, "x"]}') == {"a": [1, None, "x"]}


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a": 1, "a": 2}',
        b'{"outer": {"a": 1, "a": 2}}',
        b'[{"a": 1, "a": 2}]',
    ],
)
def test_duplicate_keys_are_rejected_at_any_depth(raw: bytes) -> None:
    with pytest.raises(InvalidJsonError):
        parse_json_strict(raw)


@pytest.mark.parametrize("raw", [b"NaN", b"Infinity", b"-Infinity", b'{"a": NaN}'])
def test_nan_and_infinity_are_rejected(raw: bytes) -> None:
    with pytest.raises(InvalidJsonError):
        parse_json_strict(raw)


@pytest.mark.parametrize("raw", [b"{", b"", b"not json", b'{"a": }'])
def test_malformed_json_is_rejected(raw: bytes) -> None:
    with pytest.raises(InvalidJsonError):
        parse_json_strict(raw)


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(InvalidJsonError):
        parse_json_strict(b'{"a": "\xff"}')


def test_dump_roundtrips_and_keeps_korean_readable() -> None:
    document = {"hypothesis": "다른 계정의 자원", "n": 1, "none": None}
    raw = dump_json_bytes(document)
    assert "다른 계정의 자원".encode("utf-8") in raw
    assert raw.endswith(b"\n")
    assert parse_json_strict(raw) == document


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_dump_rejects_nan_and_infinity(bad_value: float) -> None:
    with pytest.raises(InvalidJsonError):
        dump_json_bytes({"value": bad_value})
