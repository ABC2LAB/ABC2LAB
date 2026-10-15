"""candidate_id 조립: 결정적이고 단사여야 한다. 구성 요소에 ':'·'/'·'|'·'%'가 있어도 다른 조합이 같은 ID가 되지 않는다."""

from modules.access_analyzer.candidate_ids import build_candidate_id


def test_plain_ids_keep_the_documented_shape() -> None:
    # 기존 Rule A ID 모양 그대로(fixture·소비자가 보는 값). ':'·'/'·'{}'는 구분자가 아니라 바꾸지 않는다.
    assert build_candidate_id("rule_x", "account:alice", "account:bob", "endpoint:GET:/orders/{id}") == (
        "rule_x:account:alice|account:bob|endpoint:GET:/orders/{id}"
    )


def test_separator_inside_a_part_does_not_collide() -> None:
    # ("a|b", "c") 와 ("a", "b|c")는 다른 조합이다. 이스케이프 없이 붙이면 둘 다 "a|b|c"가 된다.
    assert build_candidate_id("r", "a|b", "c", "e") != build_candidate_id("r", "a", "b|c", "e")


def test_escape_character_inside_a_part_does_not_collide() -> None:
    # 원문에 "%7C"가 있는 값과 '|'를 이스케이프한 값도 구분돼야 단사다.
    assert build_candidate_id("r", "a%7Cb", "c", "e") != build_candidate_id("r", "a|b", "c", "e")


def test_same_parts_give_same_id() -> None:
    assert build_candidate_id("r", "a", "b", "c") == build_candidate_id("r", "a", "b", "c")
