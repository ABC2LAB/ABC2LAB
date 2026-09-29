from analyzer.normalize import api_id, param_id, resource_id


def test_canonical_ids_match_spec():
    assert api_id("get", "/api/products/{id}") == "api:GET:/api/products/{id}"
    aid = api_id("GET", "/api/products/{id}")
    assert param_id(aid, "query", "sort") == "param:api:GET:/api/products/{id}:query:sort"
    assert resource_id("Product") == "resource:product"
    assert resource_id("Order Item") == "resource:order-item"
