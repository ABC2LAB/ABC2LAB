# ABC2LAB_인터페이스_명세서_v0.1

<aside>
📘

**목표:** 인가·비즈니스 로직 취약점에 특화된 AI DAST 개발

**계약:** `schema_version=0.1.0` · 8개 모듈 · 11개 JSON 파일 = 실행 산출물 10개 + 개발 평가 입력 `ground_truth.json` 1개.

</aside>

**담당자가 보는 순서:** 개발 기준 → 관리자 초기 설정·팀원 설치 확인 → 담당 모듈 → 독립 동작·수정 책임 → 입력·할 일·출력 → 완료 체크·상세 JSON.

**공통으로 읽을 문서:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md) · [연결 예시 · 참고 자료](04-examples-refs.md)

예시는 가상 데이터이며 실제 진단·벤치마크 결과가 아니다. 실제 처리 코드는 각 모듈 폴더에서 구현한다.

## 팀의 개발 시작 기준

<aside>
🧭

**개발환경은 하나의 기준으로 맞추고, 모듈 구현·입출력 검증은 각 담당자가 끝까지 책임진다.**

모든 팀원은 같은 Python과 루트 `requirements.lock.txt`로 환경을 만든다. GitHub 관리자는 초기 설정·루트 환경 파일·병합 운영을 맡는다.

**개발 기준·관리자 초기 설정·clone 후 설치·변경/병합 규칙:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

</aside>

| 역할 | 먼저 할 일 | 개발 중 책임 |
| --- | --- | --- |
| GitHub 관리자 | 루트 네 설정 파일 + README + 모듈 폴더/초기 requirements 준비 → 새 clone 설치 확인 → main에 초기 기준 공개 | 공통 환경·잠금 파일·루트 연결·PR/검사 운영 |
| 각 모듈 담당자 | 같은 기준 커밋 clone → 지정 Python·루트 `.venv`·잠금 파일 설치 → 자기 모듈 명세 확인 | 자기 코드·입력 처리·출력 검증·저장·실패 처리·테스트 / 계약 변경 시 직접 소비자와 조율 |

**초기 기준값:** Ubuntu 24.04 LTS · x86_64 / Windows는 WSL2 · CPython 3.12.13. 관리자가 설치 검증 후 정확한 Python·uv·패키지 버전을 README와 잠금 파일로 공개한다.

**문서와 구현 상태:** 이 노션은 개발 기준을 정리한다. GitHub 최초 설정·설치 검증·모듈 구현의 완료 여부는 개발 기준 페이지의 체크리스트에서 별도로 확인한다.

## 최우선 설계 원칙

<aside>
🧩

**각 모듈 담당자는 자기 입력 처리부터 자기 출력 검증·저장·실제 동작 확인까지 전부 책임진다.**

명세에 맞는 다양한 입력을 독립적으로 처리하고, 다음 소비자와 합의한 출력 계약을 만족시킨다. 내부 구현·도구·타입·Schema·설정·모듈별 의존성 선언·테스트는 자기 폴더에 둔다. 실행·테스트 환경은 팀 공통 Python과 루트 잠금 파일을 따른다.

출력 계약을 바꿀 때는 그 파일을 직접 읽는 모듈 담당자와만 합의한다. 여러 모듈이 직접 읽으면 그 소비자들이 협의 대상이다.

</aside>

## 모듈만 개발하고 병합하는 방식

- 각 담당자는 `modules/<module_id>/**`에서 입력 검증 → 처리 → 출력 검증 → 원자적 저장 → 정상/부분/실패 처리 → 테스트·동작 확인을 완성한다.
- 파서·저장·해시·경로·검증·내부 타입은 모듈 안에서 구현한다. 각 모듈의 `schemas/input/`, `schemas/output/`, `utils/`, `configs/`, `tests/`, `requirements.txt`는 해당 담당자가 관리한다. 패키지 설치는 공통 루트 잠금 파일로 통일한다.
- 공유 구현 폴더와 중앙 검증기를 두지 않는다. 모듈마다 독립 실행 방법과 공개 호출·완료/실패 규약을 제공한다.
- 전체 실행에 필요한 `pipeline.py`는 순서·파일 경로·실행 인자 전달만 맡는 최소 호출 스크립트로 둔다. 내용 해석·검증·분석·실행 허용·오류 판정은 각 모듈에서 끝낸다.
- 출력 변경자는 변경 계약·샘플·버전을 제안하고, 직접 소비자는 자기 입력 Schema·adapter·테스트를 수정한다. 내부 고도화가 기존 출력을 유지하면 다른 담당자의 수정 없이 진행한다.
- 현재는 인터페이스와 예상 폴더 설계이며, 모듈 코드·검증 코드가 구현되었다는 뜻은 아니다.

## 수정 책임 한눈에 보기

| 대상 | 책임 주체 | 수정 경계 |
| --- | --- | --- |
| 입력 Schema·파싱·정상/부분/실패 처리 | 입력을 받는 모듈 담당 | 생산자의 합의된 출력 계약을 자기 입력에 반영하고 원본을 덮어쓰지 않음 |
| 출력 Schema·파일·근거·출력 검증 | 출력을 만드는 모듈 담당 | 자기 출력만 작성·검증 / 변경은 직접 소비자와 합의 |
| 내부 로직·utils·타입·설정·의존성 선언·테스트·README | 해당 모듈 담당 | 모든 구현은 자기 폴더 / 다른 모듈 내부 코드 import 금지 |
| 세션 공개 창구 / Neo4j | 세션: collector / DB: knowledge_graph | verifier·access_analyzer 등은 필요한 공개 연결만 사용 |
| 실행 순서·파일 경로 전달 | GitHub 관리자 / 최소 `pipeline.py` | 입출력의 업무 검증 책임은 각 모듈 담당에게 유지 |
| 테스트 웹·초기 상태·정답·데이터셋 버전 | 평가 자료 제공자 / 기존 팀원이 겸임 가능 | reporter가 자기 입력으로 검증하며 개발 평가에서만 사용 |
| 루트 `.python-version`·`.gitignore`·`requirements.txt`·`requirements.lock.txt` | GitHub 관리자만 작성·수정 | 공통 환경·전체 의존성 해결·잠금 파일 갱신 / 기능 검증은 각 담당자 |
| 의존성 추가·패키지 버전 변경 | 제안 모듈 담당자 + GitHub 관리자 + 영향받는 모듈 담당자 | 제안자는 자기 선언, 관리자는 전체 lock, 영향받는 담당자는 같은 환경에서 자기 모듈 검증 |

**파일별 작성자·직접 소비자·변경 절차:** [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md)

**모듈 내부 폴더·독립 실행·최소 호출 규약:** [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

각 담당자는 자기 모듈 페이지의 **독립 동작·수정 책임 → 입력 → 구현할 일 → 출력과 직접 소비자 → 완료 기준**을 따라 구현한다.

## 모듈별 입력 · 할 일 · 출력

| 담당 모듈 / 구현 폴더 | 받을 입력 | 구현할 일 | 만들 출력 |
| --- | --- | --- | --- |
| [웹 정보 수집기 · collector](m1-collector.md)
`modules/collector/` | 대상 URL·역할별 테스트 계정
실행 인자 | 기존 수집 결과를 계약에 맞게 내보내고, 계정·역할·요청·세션 참조를 연결한다. | `crawl_result.json` |
| [의미 분석기 · semantic_analyzer](m2-semantic_analyzer.md)
`modules/semantic_analyzer/` | `crawl_result.json`
실행 인자·모델 설정 | 요청을 정규화하고 LLM으로 자원·행위·역할 및 업무 흐름의 의미를 추론한다. | `semantic_analysis.json` |
| [지식 그래프 저장소 · knowledge_graph](m3-knowledge_graph.md)
`modules/knowledge_graph/` | `semantic_analysis.json`
`graph_query.json`
`verification_results.json` | Neo4j 적재·읽기 질의·검증 반영을 구현하고 graph_id·revision을 관리한다. | `graph_query_result.json`
KG 준비 상태·revision 제어 응답 |
| [접근 통제 분석기 · access_analyzer](m4-access_analyzer.md)
`modules/access_analyzer/` | KG 준비 상태·실행 인자·규칙 설정
`graph_query_result.json` | 질의 계획을 만들고 소유권·역할 접근·업무 조건을 분석해 검증 후보를 도출한다. | `graph_query.json`
`vulnerability_candidates.json` |
| [시나리오 생성기 · scenario_generator](m5-scenario_generator.md)
`modules/scenario_generator/` | `vulnerability_candidates.json`
`crawl_result.json`·근거 참조
실행 인자·모델 설정 | LLM으로 다단계 요청 순서·실행 계정·동적 값 바인딩·확인 조건을 포함한 계획을 만든다. | `test_scenarios.json` |
| [Safety Policy · safety_policy](m6-safety_policy.md)
`modules/safety_policy/` | `test_scenarios.json`
신뢰된 실행 인자·Policy 설정
승인 시 실제 사용자 기록 | 사전 정의 규칙으로 범위·계정·요청·상태 변경·데이터·서비스 영향을 평가한다. | `safety_decisions.json` |
| [재현·검증기 · verifier](m7-verifier.md)
`modules/verifier/` | `test_scenarios.json`
`safety_decisions.json`
`crawl_result.json`·세션 공개 창구 | 허용된 계획만 제한적으로 실행하고 요청·응답·상태 근거와 검증 결과를 남긴다. | `verification_results.json` |
| [평가·리포트 · reporter](m8-reporter.md)
`modules/reporter/` | 후보·시나리오·Policy·검증 결과
개발 평가: 수집·의미 분석·실제 KG snapshot·Ground Truth | 실제 진단의 로컬 리포트를 만들고 개발 모드에서 정답 대비 지표와 실행 비용을 평가한다. | `diagnosis_report.json`
`evaluation_results.json` — 개발 모드 |

## 연결할 때 같이 맞출 부분

- **수집기 ↔ 검증기:** `session_ref`는 불투명 참조값이다. 보호된 세션은 수집기 공개 창구에서 제공한다. [웹 정보 수집기 · collector](m1-collector.md) · [재현·검증기 · verifier](m7-verifier.md)
- **KG ↔ 접근 분석기:** `ingest → prepare_queries → query → analyze` 순서로 파일·준비 상태를 전달한다. KG와 접근 분석 담당자가 각각 자기 입력·출력을 검증한다. 질의 ID·키·graph_revision을 맞춘다. [지식 그래프 저장소 · knowledge_graph](m3-knowledge_graph.md) · [접근 통제 분석기 · access_analyzer](m4-access_analyzer.md)
- **시나리오 ↔ Policy ↔ 검증기:** 정확한 계획 파일 SHA-256에 판정을 묶고, `allow`만 실행한다. 실행 여부는 LLM이 아니라 Policy가 결정한다. [시나리오 생성기 · scenario_generator](m5-scenario_generator.md) · [Safety Policy · safety_policy](m6-safety_policy.md) · [재현·검증기 · verifier](m7-verifier.md)
- **검증기 → KG / 리포터:** `verification_results.json`은 검증기가 한 번 작성하고 두 모듈이 읽는다. 미실행·판단불가를 접근 성공으로 기록하지 않는다. [지식 그래프 저장소 · knowledge_graph](m3-knowledge_graph.md) · [평가·리포트 · reporter](m8-reporter.md)
- **개발 평가 입력:** `ground_truth.json`은 테스트 웹 담당자의 입력이다. reporter의 개발 평가만 읽으며 실제 진단과 의미·후보·시나리오 생성에 사용하지 않는다. [평가·리포트 · reporter](m8-reporter.md)

## 모든 담당자의 구현 완료 체크

- [ ]  관리자가 공개한 기준 Python·루트 잠금 파일로 환경을 준비하고 해당 환경에서 자기 모듈을 검증한다.
- [ ]  의존성 추가·버전 변경은 관리자에게 제안하고 전체 잠금 환경에서 검증한 변경을 병합한다.
- [ ]  자기 모듈만으로 명세 입력을 받아 독립 실행하고 정상·partial·failed 처리를 확인한다.
- [ ]  입력·출력 Schema, ID·참조·해시·필드 의미·실행 제한을 자기 모듈이 검증한다.
- [ ]  검증된 자기 출력만 원자적으로 공개하고 완료·실패를 실제 결과대로 통지한다.
- [ ]  자기 utils·schemas·configs·의존성·tests/fixtures를 관리하며 다른 모듈 구현이나 공유 도구를 import하지 않는다.
- [ ]  변경한 출력 계약은 직접 소비자와 합의하고 양쪽 Schema·adapter·샘플·동작 확인을 함께 맞춘다.
- [ ]  비밀값을 JSON·LLM 입력·공유 로그에 넣지 않고, 세션과 근거는 정해진 참조·공개 창구로 연결한다.

## 상세 명세 모음

각 모듈 페이지 상단에는 입력·구현할 일·출력·완료 기준이 있고, 하단의 접힌 제목에는 원래 필드 명세가 있다. 입력 파일 링크는 작성 모듈의 명세로, 공통 레코드 링크는 공통 계약으로 연결된다.

[웹 정보 수집기 · collector](m1-collector.md)

[의미 분석기 · semantic_analyzer](m2-semantic_analyzer.md)

[지식 그래프 저장소 · knowledge_graph](m3-knowledge_graph.md)

[접근 통제 분석기 · access_analyzer](m4-access_analyzer.md)

[시나리오 생성기 · scenario_generator](m5-scenario_generator.md)

[Safety Policy · safety_policy](m6-safety_policy.md)

[재현·검증기 · verifier](m7-verifier.md)

[평가·리포트 · reporter](m8-reporter.md)

[공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md)

[실행 제어 · runner와 폴더 구조](03-runner-layout.md)

[연결 예시 · 참고 자료](04-examples-refs.md)

[개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)