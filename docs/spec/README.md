# 명세 (docs/spec/)

ABC2LAB 인터페이스 명세의 현재본입니다. 개발 중에는 이 폴더가 기준입니다.
노션 `ABC2LAB_인터페이스_명세서_v0.1`은 GitHub 관리자가 이 폴더를 기준으로 주기적으로 반영하는 사본입니다. 둘이 다르면 이 폴더를 따릅니다(2026-10-10 팀 합의).

## 계약 버전

- 실행 산출물 10개: `schema_version` 0.2.0
- 개발 평가 입력 `ground_truth.json`: 0.1.0

문서 이름의 판 번호(v0.x)는 팀 전체가 같이 전환할 때만 올리고, 파일 하나의 계약 변경은 그 파일의 `schema_version`만 올립니다. 지금 이름의 v0.1은 2026-10-05 확정 판이고, 모든 모듈 명세 갱신 뒤 v0.2로 올립니다. v0.1 원문은 커밋 `358f880`(2026-10-06 노션 export)에 있습니다.

## 누가 고치나

| 파일 | 고치는 사람 |
|---|---|
| `m1`~`m8` 모듈 명세 | 그 모듈 담당자 |
| `README.md`, `00`~`04` 공통 문서 | GitHub 관리자 |

## 모듈 명세를 고칠 때 (모듈 담당자)

1. 코드를 바꾸는 PR에서 자기 `m*.md`를 같이 고칩니다. 코드와 명세가 다른 상태로 머지하지 않습니다.
2. 파일 맨 아래 "변경 이력"에 날짜 · 내용 · 근거(PR·커밋)를 한 줄 남깁니다.
3. 출력 계약(필드·타입·enum·의미·버전)이 바뀌면 생산자와 모든 직접 소비자가 먼저 합의하고 PR 제목에 "스키마 변경"을 붙입니다([02 공통 계약](02-common-contract.md) "고도화와 계약 변경의 경계").
4. 머지되면 디스코드에 공지합니다. 계약·동작이 바뀐 경우만 하고 오타·문장 다듬기는 생략합니다.
   형식: `[명세] 파일 · 바뀐 점 한 줄 · PR 링크 · 영향받는 모듈`
5. 다른 담당자의 모듈 명세는 직접 고치지 않습니다. 담당자에게 말하거나 그 담당자 리뷰를 받는 PR로 제안합니다.
6. 모듈 명세 위쪽 "현재 구현·연결 상태 — 날짜" 표(독립 구현 / 공개 계약 / 실제 전체 연결 행 필수)를 상태가 바뀔 때 갱신합니다.

## 노션 반영 (GitHub 관리자)

- 방향은 레포 → 노션 한쪽뿐입니다. 노션은 직접 고치지 않습니다(다음 반영 때 덮어써집니다). 노션 댓글도 반영 때 사라질 수 있으니 논의는 PR·디스코드에서 합니다.
- 마지막 반영 날짜와 기준 커밋은 노션 메인 페이지 상단 안내에만 적습니다. 레포에는 적지 않습니다.
- 주기: 발표 전(10/15, 12/18)과 그 사이 주 1회.
- 절차:
  1. 노션 상단 안내에서 기준 커밋을 확인합니다.
  2. `git fetch origin && git diff --stat <기준 커밋>..origin/dev -- docs/spec/`로 바뀐 파일을 뽑습니다.
  3. 바뀐 파일의 노션 페이지만 본문을 교체합니다. 페이지 사이 링크는 노션 페이지 멘션으로 바꿉니다. 대응은 아래 표를 따릅니다.
  4. 상단 안내의 날짜·기준 커밋을 이번 `origin/dev` 커밋으로 고칩니다.
  5. 디스코드에 "노션 반영 완료(기준 커밋 · 바뀐 페이지)"를 공지합니다.
- 페이지가 추가·삭제되면 아래 표도 같이 고칩니다.

## 파일과 노션 페이지

| 파일 | 노션 페이지 | URL |
|---|---|---|
| `00-index.md` | ABC2LAB_인터페이스_명세서_v0.1 | https://app.notion.com/p/59862d24e2b68392a7b701df0bd15bbb |
| `01-dev-standard.md` | 개발 기준 · 환경 일원화와 GitHub 초기 설정 | https://app.notion.com/p/82c62d24e2b683e4a21e01d4e7c41318 |
| `02-common-contract.md` | 공통 계약 · 파일 형식과 전달 규칙 | https://app.notion.com/p/ecf62d24e2b683d7a566013eed4d7d6b |
| `03-runner-layout.md` | 실행 제어 · runner와 폴더 구조 | https://app.notion.com/p/a7d62d24e2b68218988981a208c29863 |
| `04-examples-refs.md` | 연결 예시 · 참고 자료 | https://app.notion.com/p/59162d24e2b683e99b2a81dfa486961e |
| `m1-collector.md` | 웹 정보 수집기 · collector | https://app.notion.com/p/0c662d24e2b6822c83d9819276cc2624 |
| `m2-semantic_analyzer.md` | 의미 분석기 · semantic_analyzer | https://app.notion.com/p/b8362d24e2b68328b85c813f2cb65116 |
| `m3-knowledge_graph.md` | 지식 그래프 저장소 · knowledge_graph | https://app.notion.com/p/c9862d24e2b682a8884381dce73bdb20 |
| `m4-access_analyzer.md` | 접근 통제 분석기 · access_analyzer | https://app.notion.com/p/27b62d24e2b683419a9701569e9057cb |
| `m5-scenario_generator.md` | 시나리오 생성기 · scenario_generator | https://app.notion.com/p/fbf62d24e2b683bbbbd6814799e6804f |
| `m6-safety_policy.md` | Safety Policy · safety_policy | https://app.notion.com/p/9b862d24e2b683c0ac0e012f09df80a0 |
| `m7-verifier.md` | 재현·검증기 · verifier | https://app.notion.com/p/3c962d24e2b6822a81fe8180c4c16833 |
| `m8-reporter.md` | 평가·리포트 · reporter | https://app.notion.com/p/d7d62d24e2b6821a8f4481c91d28bda0 |
