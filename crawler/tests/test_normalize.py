import pytest

from crawler.normalize import NormalizedPath, normalize_path

SAMPLE_UUID = "550e8400-e29b-41d4-a716-446655440000"
NOT_A_UUID = "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"


@pytest.mark.parametrize(
    ("url_or_path", "expected"),
    [
        pytest.param(
            "/orders/3",
            NormalizedPath("/orders/{id}", ("3",), ()),
            id="numeric_id",
        ),
        pytest.param(
            "/users/5/orders/7",
            NormalizedPath("/users/{id}/orders/{id}", ("5", "7"), ()),
            id="multiple_numeric_ids",
        ),
        pytest.param(
            f"/files/{SAMPLE_UUID}",
            NormalizedPath("/files/{id}", (SAMPLE_UUID,), ()),
            id="uuid_lowercase",
        ),
        pytest.param(
            f"/files/{SAMPLE_UUID.upper()}",
            NormalizedPath("/files/{id}", (SAMPLE_UUID.upper(),), ()),
            id="uuid_uppercase_keeps_original_value",
        ),
        pytest.param(
            "/orders/{id}",
            NormalizedPath("/orders/{id}", (), ()),
            id="already_placeholder",
        ),
        pytest.param(
            "/orders/{order_id}",
            NormalizedPath("/orders/{order_id}", (), ()),
            id="already_named_placeholder",
        ),
        pytest.param(
            "/search?q=shoe&page=2",
            NormalizedPath("/search", (), ("page", "q")),
            id="query_string_names_sorted_values_dropped",
        ),
        pytest.param(
            "/items?tag=a&tag=b",
            NormalizedPath("/items", (), ("tag",)),
            id="duplicate_query_names_deduplicated",
        ),
        pytest.param(
            "/orders/3?tab=items",
            NormalizedPath("/orders/{id}", ("3",), ("tab",)),
            id="query_string_with_path_id",
        ),
        pytest.param(
            "/orders/",
            NormalizedPath("/orders", (), ()),
            id="trailing_slash",
        ),
        pytest.param(
            "/orders/3/",
            NormalizedPath("/orders/{id}", ("3",), ()),
            id="trailing_slash_with_id",
        ),
        pytest.param(
            "/orders//3",
            NormalizedPath("/orders/{id}", ("3",), ()),
            id="consecutive_slashes_collapsed",
        ),
        pytest.param(
            "/",
            NormalizedPath("/", (), ()),
            id="root",
        ),
        pytest.param(
            "/?page=1",
            NormalizedPath("/", (), ("page",)),
            id="root_with_query",
        ),
        pytest.param(
            "",
            NormalizedPath("/", (), ()),
            id="empty_string_is_root",
        ),
        pytest.param(
            "http://localhost:8000/orders/3?x=1",
            NormalizedPath("/orders/{id}", ("3",), ("x",)),
            id="absolute_url",
        ),
        pytest.param(
            "/api/v1/users",
            NormalizedPath("/api/v1/users", (), ()),
            id="partial_digits_segment_unchanged",
        ),
        pytest.param(
            "/docs/report-2024.pdf",
            NormalizedPath("/docs/report-2024.pdf", (), ()),
            id="digits_inside_filename_unchanged",
        ),
        pytest.param(
            f"/x/{NOT_A_UUID}",
            NormalizedPath(f"/x/{NOT_A_UUID}", (), ()),
            id="uuid_shaped_non_hex_unchanged",
        ),
    ],
)
def test_normalize_path(url_or_path: str, expected: NormalizedPath) -> None:
    assert normalize_path(url_or_path) == expected


def test_same_endpoint_different_ids_share_template() -> None:
    first = normalize_path("/orders/3")
    second = normalize_path("/orders/7/")

    assert first.template == second.template
    assert first.path_values != second.path_values
