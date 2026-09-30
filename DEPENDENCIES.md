# analyzer 모듈 의존성 참고
#
# 이 프로젝트는 루트 단일 pyproject.toml + uv.lock 로 관리됩니다(README 규약).
# analyzer 모듈은 표준 라이브러리만 사용하므로 런타임 추가 의존성이 없습니다.
# (json, re, dataclasses, argparse 등 stdlib 만 사용)
#
# 테스트에는 pytest 가 필요합니다. 루트 pyproject.toml 의 dev 그룹에 이미 있다면
# 추가 작업이 필요 없습니다. 없다면 루트에서:
#
#     uv add --group dev pytest
#
# analyzer 는 crawler 의 출력(JSON)만 소비하고 다른 모듈에 런타임 의존하지 않습니다.
# 그래프(dict)는 kg/ 모듈이, 태스크는 이후 agent 가 소비합니다.
