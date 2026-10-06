from modules.semantic_analyzer.utils.normalize import (
    json_value_type,
    normalize_method,
    normalize_path_template,
)


def test_numeric_segment_becomes_placeholder():
    assert normalize_path_template("http://localhost:8001/orders/1") == "/orders/{id}"


def test_uuid_segment_becomes_placeholder():
    url = "http://localhost/api/users/3fa85f64-5717-4562-b3fc-2c963f66afa6"
    assert normalize_path_template(url) == "/api/users/{id}"


def test_trailing_and_double_slash_collapse():
    assert normalize_path_template("/orders//3/") == "/orders/{id}"


def test_non_id_segments_kept():
    assert normalize_path_template("/admin/api/users") == "/admin/api/users"


def test_method_uppercased():
    assert normalize_method(" get ") == "GET"


def test_json_value_type_handles_bool_before_int():
    assert json_value_type(True) == "boolean"
    assert json_value_type(3) == "number"
    assert json_value_type("x") == "string"
    assert json_value_type(None) == "null"
    assert json_value_type([1]) == "array"
    assert json_value_type({"a": 1}) == "object"
