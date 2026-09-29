from analyzer.normalize import api_id, page_id, param_id, rel_id, role_id


def test_ids_are_deterministic_and_formatted():
    assert role_id("user") == "role:user"
    assert page_id("/products/{id}") == "page:/products/{id}"
    assert api_id("get", "/api/products/{id}") == "api:GET:/api/products/{id}"
    assert param_id("get", "/api/products/{id}", "query", "sort") == \
        "param:GET:/api/products/{id}:query:sort"
    # 관계 ID의 type은 대문자
    assert rel_id("page:/x", "calls", "api:GET:/x") == "rel:page:/x:CALLS:api:GET:/x"
