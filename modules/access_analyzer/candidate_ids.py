"""candidate_id 조립. 뒤 모듈(시나리오·결과·리포트)의 연결 키라 결정적이어야 하고, 다른 조합이 같은 ID로 뭉개지면 안 된다.

구성 요소(계정·자원·엔드포인트 ID)는 불투명 문자열이라 ':'·'/'는 물론 구분자 '|'도 들어 있을 수 있다. 구분자와
이스케이프 문자만 퍼센트 인코딩해 조합 → ID가 단사가 되게 한다. 구분자가 없는 보통 ID는 그대로라 기존 ID와 같다.
"""

RULE_SEPARATOR = ":"
PART_SEPARATOR = "|"
ESCAPE = "%"
# 이스케이프 문자를 먼저 바꿔야 "%7C"를 원문에 가진 값과 '|'를 가진 값이 구분된다.
_ESCAPES = ((ESCAPE, "%25"), (PART_SEPARATOR, "%7C"))


def build_candidate_id(rule_id: str, *parts: str) -> str:
    """`<rule_id>:<part>|<part>|…`. parts 순서는 규칙이 정한다(실행 계정, 기준 계정, 대상)."""
    return f"{rule_id}{RULE_SEPARATOR}{PART_SEPARATOR.join(_escape(part) for part in parts)}"


def _escape(part: str) -> str:
    for raw, encoded in _ESCAPES:
        part = part.replace(raw, encoded)
    return part
