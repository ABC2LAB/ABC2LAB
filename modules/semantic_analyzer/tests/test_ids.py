from modules.semantic_analyzer.utils import ids


def test_ids_are_deterministic():
    assert ids.endpoint_node_id("GET", "/orders/{id}") == ids.endpoint_node_id("GET", "/orders/{id}")
    assert ids.user_node_id("acc_alice") == "user:acc_alice"
    assert ids.role_node_id("role_user") == "role:role_user"


def test_relationship_id_encodes_triple():
    edge_id = ids.relationship_id("user:acc_alice", "HAS_ROLE", "role:role_user")
    assert edge_id == "rel:user:acc_alice|HAS_ROLE|role:role_user"


def test_parameter_id_includes_location_and_name():
    endpoint = ids.endpoint_node_id("POST", "/cart/add")
    assert ids.parameter_node_id(endpoint, "body", "product_id").endswith(":body:product_id")
