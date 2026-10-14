"""응답 텍스트를 비교용 줄로 정규화한다. 텍스트 baseline_match의 coverage 기록에 쓴다.

stdlib html.parser만 쓴다(새 의존성 없음). 화면에 보이는 텍스트만 남긴다: script·style·noscript·template 안 내용은 버리고,
태그 속성값(CSRF hidden 값·링크 주소 등)은 처음부터 텍스트로 읽지 않는다. 줄마다 공백을 접고 빈 줄은 버린다.
HTML이 아닌 텍스트도 같은 함수로 줄을 나눈다(태그가 없으면 원문 줄 그대로).
"""

from collections import Counter
from html.parser import HTMLParser

# 안의 문자열이 화면에 보이지 않는 요소. 스크립트 상수·토큰처럼 요청마다 바뀌는 값이 비교를 흔들지 않게 뺀다.
HIDDEN_TEXT_TAGS = frozenset({"script", "style", "noscript", "template"})


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in HIDDEN_TEXT_TAGS:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in HIDDEN_TEXT_TAGS and self._hidden_depth > 0:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0:
            self.chunks.append(data)


def visible_lines(text: str) -> list[str]:
    """보이는 텍스트를 줄 목록으로 돌려준다(문서 순서, 중복 유지)."""
    parser = _VisibleTextParser()
    parser.feed(text)
    parser.close()
    lines: list[str] = []
    for chunk in parser.chunks:
        for raw_line in chunk.splitlines():
            line = " ".join(raw_line.split())
            if line:
                lines.append(line)
    return lines


def coverage(baseline_lines: list[str], subject_lines: list[str]) -> float | None:
    """기준 줄(중복 포함) 중 실행 응답에도 나타난 비율. 분모는 기준 줄 수다. 기준 줄이 없으면 None."""
    if not baseline_lines:
        return None
    shared = Counter(baseline_lines) & Counter(subject_lines)
    return sum(shared.values()) / len(baseline_lines)
