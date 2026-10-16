"""canonical_action 규칙의 입출력 벡터.

이 표가 GT ordered_actions 생성 규칙과 동일한 '최종 규칙'이다(윤재상 공유용).
토큰이 의미적으로 완벽할 필요는 없고, GT·analyzer가 같은 (method, path_template) → 토큰을 내는 게 목적이다.
"""
import pytest

from modules.semantic_analyzer.utils.normalize import canonical_action

# (method, path_template) → 기대 토큰. path_template은 normalize_path_template 결과({id} 치환)를 가정한다.
_VECTORS = [
    # 인증 특례
    ("POST", "/login", "login"),
    ("POST", "/signup", "signup"),
    ("POST", "/logout", "logout"),
    # GET: 끝이 {id}면 read, 아니면 view
    ("GET", "/", "view_root"),
    ("GET", "/products", "view_product"),
    ("GET", "/products/{id}", "read_product"),
    ("GET", "/cart", "view_cart"),
    ("GET", "/orders", "view_order"),
    ("GET", "/orders/{id}", "read_order"),
    ("GET", "/mypage", "view_mypage"),
    ("GET", "/checkout", "view_checkout"),
    ("GET", "/admin", "view_admin"),
    ("GET", "/admin/users", "view_admin_user"),
    ("GET", "/admin/api/users", "view_admin_user"),  # api 접두는 뺀다
    ("GET", "/admin/products", "view_admin_product"),
    ("GET", "/admin/orders", "view_admin_order"),
    ("GET", "/admin/products/new", "view_admin_product_new"),
    ("GET", "/admin/products/{id}/edit", "view_admin_product_edit"),
    ("GET", "/api/users/{id}", "read_user"),
    # POST/PUT/PATCH: 끝이 {id}면 update, 아니면 create
    ("POST", "/orders", "create_order"),
    ("POST", "/admin/products", "create_admin_product"),
    ("POST", "/admin/products/{id}", "update_admin_product"),
    ("PUT", "/admin/products/{id}", "update_admin_product"),
    # 끝 세그먼트 동사 override: add→update, delete·remove→delete (이름에서 뺀다)
    ("POST", "/cart/add", "update_cart"),
    ("POST", "/admin/products/{id}/delete", "delete_admin_product"),
    # DELETE
    ("DELETE", "/admin/products/{id}", "delete_admin_product"),
]


@pytest.mark.parametrize(("method", "path_template", "expected"), _VECTORS)
def test_canonical_action_vectors(method, path_template, expected):
    assert canonical_action(method, path_template) == expected


def test_method_is_case_insensitive():
    assert canonical_action("get", "/orders/{id}") == "read_order"


def test_unknown_method_falls_back_to_access():
    assert canonical_action("OPTIONS", "/orders") == "access_order"
