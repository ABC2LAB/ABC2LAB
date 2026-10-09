# 평가·리포트 · reporter

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

<aside>
📊

- 구현 폴더: `modules/reporter/`.
- 공개 operation: `report`, `evaluate`.
- 담당 범위: 실제 진단의 근거·재현·Policy 상태를 로컬 리포트로 정리하고, 개발 모드에서는 자체 테스트 웹의 정답과 실제 산출물을 비교해 성능을 평가한다.
</aside>

**읽는 순서:** 독립 동작·수정 책임 → 입력 변화 기준 → 입력 → 구현할 일 → 출력 → 완료 기준 → 주의사항. 상세 JSON·중첩 레코드는 접힌 제목에서 확인한다.

## 현재 구현·연결 상태 — 2026-10-10

기준 커밋 `695b3c8`의 구현·공개 계약을 반영했다. PR #52·#53 병합 후 상태이며,
이번 저장소 문서 갱신이 노션 원본의 export 갱신을 의미하지는 않는다.

| 구분 | 현재 상태 |
| --- | --- |
| 독립 구현 | 공개 `report`·`evaluate`·CLI, 6개 진단 분류·개발 평가·원자 저장 구현 완료 |
| 리포트 화면 | 로컬 HTML 생성 구현 완료. PDF 출력은 미구현 |
| 공개 계약 | 실행 입력 7종·출력 2종 `0.2.0`, Ground Truth 입력은 기존 `0.1.0` 유지 |
| 소비자 호환 | Scenario.resource_ids, 검증 관계 source_account_id, KG Resource Type/Instance·원본 ID 대응 완료 |
| 독립 회귀 | Reporter `418 passed`; KG·Safety Policy·Reporter·Verifier 합동 `897 passed, 12 skipped` |
| 실제 전체 연결 | 미완료. 같은 run의 실제 후보·계획·판정·검증 결과와 평가 snapshot으로 연결 검증 필요 |

skip 12건은 이번에 활성화하지 않은 KG 실제 Neo4j 테스트다. Verifier 공개 결과는
Reporter 입력 Schema를 통과하지만 graph_updates 관계는 아직 0개다. Schema 수용과
실제 검증 관계 수신은 구분한다. 상세 구현 이력은
[모듈 README](../../modules/reporter/README.md)를 따른다.

## 독립 동작·수정 책임

**이 모듈 담당자의 전체 책임:** 명세에 맞는 입력을 받으면 입력 검증·변환·실제 처리·출력 변환·출력 검증·저장·오류 처리·설정·모듈별 의존성 선언·테스트·동작 확인을 자기 폴더 안에서 끝낸다. 실행·테스트는 팀 공통 Python과 루트 잠금 환경에서 수행한다. 외부 모듈의 내부 코드·공유 도구에 의존하지 않는다.

| 영역 | 변경·작성 책임 | 참조·사용 경계 |
| --- | --- | --- |
| 자기 구현·도구·타입·설정·의존성 선언·테스트 | `modules/reporter/**`는 reporter 담당 수정 | 파서·검증·저장·해시·경로·adapter도 자기 utils와 schemas에서 구현한다. 다른 모듈 코드를 import하지 않는다. |
| 입력 계약·입력 검증 | reporter 담당이 자기 `schemas/input/`과 검증을 관리 | 입력 원본은 읽기 전용. 생산자 출력 계약과 일치시키고 원본 오류는 생산자에게 요청한다. |
| 자기 출력 계약·출력 검증 | reporter 담당이 자기 `schemas/output/`·출력·근거·문서를 작성·검증 | 합의한 출력 Schema·필드 의미·ID·근거를 만족한 파일만 완료로 공개한다. |
| 입력·외부 참조 | 후보·계획·Policy·검증·수집·의미 분석은 각 생산자, 실제 KG snapshot은 knowledge_graph, 정답은 평가 자료 제공자가 작성한다. reporter가 각 입력을 검증한다. | 명시된 파일·근거·공개 창구만 사용한다. 다른 모듈 내부 구현·전역 가변 상태를 읽지 않는다. |
| 출력 변경·수신자 조율 | 변경 제안·출력 구현은 reporter / 입력 대응은 아래 직접 소비자 | 소비자 내부 알고리즘은 알 필요가 없다. 그 파일을 실제 읽는 담당자와만 버전·필드·의미·변경 시점을 합의한다. |
| 진단 분류·집계·평가·렌더링 | reporter가 자기 입력의 근거를 연결해 결과를 작성 | 잘 보이게 만들기 위해 upstream 결과·상태·계획·정답을 수정하지 않는다. |
| 개발 평가 snapshot/정답 | snapshot 질의는 access_analyzer 작성·KG 조회, 정답은 평가 자료 제공자 작성 | reporter는 읽기만 한다. 실제 진단에 Ground Truth를 요구하지 않는다. |

**모듈 내부 경계:** 입력 adapter → 내부 처리 → 출력 adapter → 자기 출력 검증 → 원자적 저장 → 완료 통지. 내부 고도화가 계약을 유지하면 다른 담당자에게 수정을 요구하지 않는다.

**입력·출력 계약과 수정 책임:** [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · **독립 실행·모듈 폴더 구조:** [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

## 공통 환경에서의 독립 개발

**개발 기준·관리자 초기 설정·환경 설치:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

- 같은 `.python-version`과 루트 `requirements.lock.txt`로 만든 저장소 루트 `.venv`에서 실행·테스트한다. 독립 검증은 자기 입력 fixture·공개 창구 대역으로 수행한다.
- `modules/reporter/requirements.txt`에는 실제 사용하는 직접 의존성과 지원 버전을 선언한다. 추가·버전 변경은 GitHub 관리자에게 제안하고 후보 잠금 환경에서 자기 모듈을 검증한다.
- 루트 네 환경 파일은 관리자만 수정한다. 자기 Python 버전이나 별도 lock을 만들어 팀 기준을 변경하지 않는다.
- 의존성 변경은 직접 JSON 소비자 외에도 같은 패키지·하위 의존성의 영향을 받는 모듈과 호환성을 확인한다. JSON 출력 변경은 기존 직접 소비자 목록을 따른다.
- 필요한 환경변수는 자기 `.env.example`·configs·README에 기록한다. 실제 비밀값과 실행 결과는 Git에 올리지 않는다.

## 입력 변화·고도화 완료 기준

- [ ]  기준 Python·공통 잠금 파일로 환경을 동기화하고 같은 환경에서 자기 실행·테스트 명령을 확인한다.
- [ ]  의존성 변경이 있으면 자기 requirements 제안과 관리자 잠금 파일 갱신·영향 검증이 같은 병합본에 반영된다.
- [ ]  입력 Schema·지원 버전·ID·참조·해시를 자기 모듈이 검증하고 잘못된 입력을 정상 결과로 숨기지 않는다.
- [ ]  입력 파싱부터 정상/부분/실패 처리, 출력 Schema·의미 검증과 실제 저장까지 담당자가 동작을 확인한다. 중앙 검증 도구에 책임을 넘기지 않는다.
- [ ]  팀 공통 잠금 환경과 자기 설정·tests/fixtures로 명세 입력을 받아 독립 실행할 수 있다. 다른 모듈의 실제 구현이 없어도 공개 입력 fixture로 검증한다.
- [ ]  자기 utils·타입·Schema는 자기 폴더 안에 둔다. 공유 구현 폴더와 다른 모듈의 내부 파일을 import하지 않는다.
- [ ]  후보 수·실행 여부·오류·차단/승인 대기가 달라도 근거와 미검증 사유를 보존한다.
- [ ]  누락 결과를 정상 0건·오탐으로 합치지 않고 지표 분모·미검증 수·분모 0의 null 규약을 유지한다.
- [ ]  허용된 빈 배열·null·partial, 잘못된 Schema/버전/ID/참조/해시를 구분한다. 입력 오류를 성공·정상 빈 결과로 숨기지 않는다.
- [ ]  자기 출력의 Schema·작성자·필수 필드·ID/근거 대응을 검증하고 자기 경로에 원자적으로 공개한다. 저장 실패를 완료로 알리지 않는다.
- [ ]  위 입력 변화와 의존성 실패를 자기 모듈 테스트에서 검증한다. 다른 모듈 실제 구현 대신 공개 입력 fixture·인터페이스 대역으로 독립 검증할 수 있다.
- [ ]  내부 최적화 이후에도 자기 입력·출력 계약 검증을 통과한다. 계약 변경 시 직접 소비자와 실제 출력 샘플의 수신 검증을 함께 확인한다.

## 입력

| 입력 파일·정보 | 작성·제공 주체 | 사용할 내용 |
| --- | --- | --- |
| vulnerability_candidates.json | `access_analyzer` | report: data.candidates 전체를 기준으로 처리되지 못한 후보도 보존한다. |
| test_scenarios.json / safety_decisions.json | `scenario_generator / safety_policy` | report: 재현 계획, 판정 ID·허용 여부·평가 근거·차단·승인 사유를 연결한다. |
| verification_results.json 및 근거 참조 | `verifier` | report: data.results·실행 상태·요청/응답 근거와 오류를 읽는다. 결과가 없는 후보는 입력 오류·누락 상태와 함께 추적한다. |
| crawl_result.json / semantic_analysis.json | `collector / semantic_analyzer` | evaluate: 페이지·API·Parameter 발견과 분석한 관계·역할·업무 흐름을 평가한다. |
| graph_query_result.json의 structure_snapshot | `knowledge_graph / 개발 평가` | evaluate: 실제 적재된 KG 구조와 조회 revision을 사용한다. |
| ground_truth.json | `test_fixture / 테스트 웹 담당` | evaluate 전용: dataset_id·dataset_version과 정답 entities·relationships·workflows·cases. 프로젝트 루트 기준으로 참조한다. |
| 실행 인자 / 입력 산출물의 모델·실행 측정값 | `사용자 실행 인자 / 명시된 산출물 참조` | 대상 URL, 실행 mode, 평가 매칭 규칙, model_info와 runtime_metrics. |

**입력 파일의 전체 필드:** [vulnerability_candidates.json 필드](m4-access_analyzer.md) · [test_scenarios.json 필드](m5-scenario_generator.md) · [safety_decisions.json 필드](m6-safety_policy.md) · [verification_results.json 필드](m7-verifier.md) · [crawl_result.json 필드](m1-collector.md) · [semantic_analysis.json 필드](m2-semantic_analyzer.md) · [ground_truth.json 필드](m8-reporter.md).

### 현재 입력·출력 계약과 ID 경계

| 계약 | 지원 버전 |
| --- | --- |
| report 입력: vulnerability_candidates·test_scenarios·safety_decisions·verification_results | `0.2.0` |
| evaluate 추가 실행 입력: crawl_result·semantic_analysis·graph_query_result | `0.2.0` |
| reporter 출력: diagnosis_report·evaluation_results | `0.2.0` |
| evaluate 전용 정적 입력: ground_truth | 기존 `0.1.0` |

- 필수 `Scenario.resource_ids`는 하나 이상의 비어 있지 않은 문자열이다. 원본
  후보의 불투명 Resource instance node_id를 순서·값 그대로 수용한다. 후보와의
  일치는 생산자가 보장하며 Reporter가 ID를 생성·정렬·중복 제거하지 않는다.
- 검증 관계는 m7의 `source_account_id`(실제 접근 계정의 원본 ID)와 `target_id`
  (기존 Resource instance node_id)를 받는다. 이전 source_id·동시 입력·누락은
  거절한다. 계정→User node_id 변환·DB 반영은 KG 책임이다.
- semantic 및 KG snapshot의 일반 관계는 계속 source_id·target_id를 사용한다.
  원본 계정·역할 ID와 노드 ID는 속성 인덱스로 구분하며 접두사로 추측하지 않는다.
- 개발 평가는 실제 KG snapshot의 Resource Type/Instance와 match_key를 사용한다.
  복합 식별값은 순서와 무관하게 비교하되 식별값의 원문 대소문자·공백을 보존한다.
- 같은 계획의 정확한 파일 해시·후보/시나리오/판정/검증 ID·근거·revision을 검증한다.
  미실행·누락·판단불가를 정상 빈 결과나 취약점 없음으로 바꾸지 않는다.
- 실행 계약 0.1.0·미지원 버전은 묵시 변환하지 않는다. 공통 계약의 11개 파일 0.2
  안내와 본 문서의 Ground Truth 0.1 표는 불일치하므로 정답 Schema·fixture는
  유지하고 버전 변경은 생산자·소비자·관리자 합의 대상으로 남긴다.

## 구현할 일

1. report: 후보 ID를 시나리오·Policy·검증 결과와 대응시키고 실제 요청·응답 근거를 연결한다.
2. 취약점 확인·위반 의심·미재현·판단불가·정책 차단·승인 대기를 분류하고 미검증 사유를 보존한다.
3. diagnosis_report.json과 사용자에게 보여줄 로컬 HTML 리포트를 생성한다. 현재 구현은 HTML이며 PDF 출력은 미구현이다.
4. evaluate: 개발 모드에서 정답 버전·KG snapshot·매칭 규칙·동일 평가 조건을 확인한다.
5. 구조·관계·역할·업무 흐름·후보/확정 진단 지표, 원시 분자·분모, 미검증 수와 모델·시간·자원 측정값을 evaluation_results.json으로 출력한다.

## 출력

| 작성 파일 | 생성 operation | 출력 변경 협의 대상 — 직접 소비자 |
| --- | --- | --- |
| `diagnosis_report.json` | `report` | 로컬 사용자 |
| `evaluation_results.json` | `evaluate` | 개발 평가 사용자 |

정상·부분 완료 파일의 `data` 필드는 아래와 같다. `status=failed`이면 `data=null`로 기록한다. 공통 메타데이터·상태 규칙은 [공통 파일 형식](02-common-contract.md)을 적용한다.

## 완료 기준

아래 표시는 모듈 독립 구현·소유 fixture 검증 기준이며 실제 동일 run 전체 연결
또는 Ground Truth 버전 합의 완료를 뜻하지 않는다.

- [x]  후보 ID를 계획·Policy·검증 결과와 연결하고 미검증 후보와 처리 오류도 리포트에 남긴다.
- [x]  확인·의심·미재현·판단불가·차단·승인 대기를 근거에 따라 구분한다.
- [x]  report는 Ground Truth 없이 동작하고 evaluate만 정답·실제 KG snapshot·모델 및 자원 측정값을 사용한다.
- [x]  평가 분모·미검증 수를 공개하고 분모 0의 비율은 null로 기록한다.
- [ ]  같은 run의 실제 후보·계획·판정·검증 결과 및 개발 평가 입력으로 전체 연결을 검증한다.

## 구현 주의사항

- 실제 진단은 Ground Truth 없이 수행한다. evaluation_results.json은 정답이 있는 개발 평가 모드에서만 생성한다.
- ground_truth.json은 테스트 웹 담당자가 작성한 입력이며 reporter의 출력이 아니다. 의미 분석·후보·시나리오 생성에 정답을 전달하지 않는다.
- 시나리오 생성·Policy 처리·검증 오류로 결과가 없는 후보도 보존한다. 없는 scenario_id·verification_id는 null로 기록하고 suspected 또는 indeterminate와 처리 오류를 설명한다.
- 검증 결과와 기대 정책의 근거가 충분할 때 confirmed로 보고한다. 실행되지 않은 후보를 취약점 없음이나 오탐으로 합치지 않는다.
- summary.candidate_count는 고유 후보 수이고 분류별 집계는 리포트 항목 수다. 동일 후보·시나리오 중복 항목은 제거하며, 한 후보의 여러 시나리오 때문에 두 집계의 합계가 다를 수 있다.
- GT ID와 분석 노드 ID의 문자열 일치를 평가 기준으로 삼지 않는다. Page path, Endpoint method+path_template, Parameter endpoint+name+location, Role name, Resource type+외부 식별값의 정규화 키로 매칭한다.
- 같은 테스트 웹·dataset_version·초기 상태·계정·매칭 규칙·Policy 조건으로 모델을 비교한다. model_version·prompt_version·생성 설정·호출량·시간·자원을 기록한다.
- 후보 발견율과 검증 확정 발견율을 분리한다. 전체 정답 양성 분모와 차단·승인 대기·판단불가 수를 함께 공개하고 미검증을 분모에서 빼서 발견율을 부풀리지 않는다.
- 비율의 분모가 0이면 value=null이다. 미측정을 0·1로 기록하지 않는다. 정상 대조 사례도 포함해 정밀도를 평가한다.

## 입력·출력 JSON 필드

### ground_truth.json — 개발 평가 입력 계약

- 작성 주체는 테스트 웹 담당자(`test_fixture`)이고, 개발 모드의 reporter만 읽는다.
- 위치: `datasets/<dataset_id>/ground_truth.json`. 실행별 공통 envelope를 사용하지 않는 정적 dataset 파일이다.
- 예상 Schema 경로: `modules/reporter/schemas/input/ground_truth.schema.json`.
- 예시: `datasets/shop_demo/ground_truth.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `schema_version` | `string: 0.1.0` | 필수 | 정답 파일 계약 버전. |
| `artifact_type` | `string: ground_truth` | 필수 | 정답 파일 종류. |
| `dataset_id` | `string` | 필수 | 자체 테스트 웹 데이터셋 ID. |
| `dataset_version` | `string` | 필수 | 웹 구조·취약점·초기 데이터 버전. |
| `producer` | `string: test_fixture` | 필수 | 정답 파일 작성 주체. 8개 진단 모듈 중 하나가 아니다. |
| `created_at` | `string (date-time)` | 필수 | 정답 데이터 작성 시각. |
| `data` | `GroundTruthData` | 필수 | 평가 전용 정답 정보. LLM 분석·후보·시나리오 입력으로 전달하지 않는다. |

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.entities` | `array<GroundTruthEntity>` | 필수 | 페이지·API·파라미터·역할·계정·자원 정답 목록. |
| `data.relationships` | `array<GroundTruthRelation>` | 필수 | Page–API, API–Parameter, User–Resource 등 정답 관계. |
| `data.workflows` | `array<GroundTruthFlow>` | 필수 | 주요 업무 흐름과 조건 정답. |
| `data.cases` | `array<GroundTruthCase>` | 필수 | 취약 사례와 정상 대조 사례 정답. |

**정답 입력의 중첩 필드**

### GroundTruthEntity

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `gt_id` | `string` | 필수 | 정답 데이터 내부 ID. 분석기가 생성한 노드 ID와 같을 필요는 없다. |
| `entity_type` | `enum: User, Role, Page, Action, Endpoint, Parameter, Resource` | 필수 | 정답 엔티티 종류. |
| `match_key` | `map<string, JsonValue>` | 필수 | 평가 매칭용 정규화 키. Page path, Endpoint method+path_template 등. |

### GroundTruthRelation

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `gt_relation_id` | `string` | 필수 | 정답 관계 ID. |
| `source_gt_id` | `string` | 필수 | 정답 시작 엔티티 ID. |
| `target_gt_id` | `string` | 필수 | 정답 끝 엔티티 ID. |
| `relation_type` | `string` | 필수 | 정답 관계 종류. |

### GroundTruthFlow

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `gt_workflow_id` | `string` | 필수 | 정답 업무 흐름 ID. |
| `name` | `string` | 필수 | 정답 업무 흐름 이름. |
| `ordered_actions` | `array<string>` | 필수 | 기대 업무 순서의 정규화 행위 이름. |
| `constraints` | `array<string>` | 필수 | 정상 상태·순서·횟수 조건. 실행 코드가 아니다. |

### GroundTruthCase

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `case_id` | `string` | 필수 | 정답 평가 사례 ID. |
| `category` | `enum: authorization, business_logic` | 필수 | 평가 사례 분류. |
| `vulnerability_type` | `string` | 필수 | 평가 유형. |
| `actor_alias` | `string` | 필수 | 테스트 실행 계정 별칭. |
| `resource_match_key` | `map<string, JsonValue>` | 필수 | 대상 자원을 찾을 정규화 키. |
| `is_vulnerable` | `boolean` | 필수 | 해당 평가 사례에 실제 취약점이 존재하는지 정답. |
| `expected_behavior` | `string` | 필수 | 정상 동작 기대 설명. |

### diagnosis_report.json

- 고정 값: `artifact_type=diagnosis_report`, `producer=reporter`, `schema_version=0.2.0`.
- 예상 Schema 경로: `modules/reporter/schemas/output/diagnosis_report.schema.json`.
- 예상 출력 fixture 경로: `modules/reporter/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/reporter/diagnosis_report.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.target_url` | `string` | 필수 | 진단 대상 URL. |
| `data.summary` | `ReportSummary` | 필수 | 결과 분류별 요약. |
| `data.findings` | `array<Finding>` | 필수 | 재현 시나리오·근거와 연결되는 리포트 항목. |
| `data.limitations` | `array<string>` | 필수 | 미검증 범위·근거 한계·세션 오류 등 보고할 제한 사항. |

### evaluation_results.json

- 고정 값: `artifact_type=evaluation_results`, `producer=reporter`, `schema_version=0.2.0`.
- 예상 Schema 경로: `modules/reporter/schemas/output/evaluation_results.schema.json`.
- 예상 출력 fixture 경로: `modules/reporter/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/reporter/evaluation_results.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.ground_truth_ref` | `GroundTruthRef` | 필수 | 평가에 사용한 테스트 웹 정답 참조. |
| `data.source_graph_revision` | `integer` | 필수 | 평가한 실제 적재 KG revision. |
| `data.matching_profile` | `string` | 필수 | URL·API·파라미터·역할·자원 정답 매칭 규칙 버전. |
| `data.metrics` | `array<Metric>` | 필수 | 구조·관계·역할·Flow·취약점 진단 지표와 원시 분자·분모. |
| `data.candidate_counts` | `CountTriple` | 필수 | 후보 생성 단계의 TP·FP·FN. |
| `data.confirmed_counts` | `CountTriple` | 필수 | 검증 확정 단계의 TP·FP·FN. 미검증 양성은 확정 TP가 아니다. |
| `data.unverified_counts` | `UnverifiedCounts` | 필수 | 차단·승인 대기·판단불가 별도 집계. |
| `data.models` | `map<string, ModelInfo>` | 필수 | 의미 분석·복잡한 후보 분석·시나리오 생성에 사용한 모델 정보. |
| `data.run_metrics` | `RuntimeMetrics` | 필수 | 동일 측정 기준의 시간·호출량·자원 사용량. |
| `data.notes` | `array<string>` | 필수 | 평가 조건·미측정·범위 한계 설명. |

## 중첩 레코드 필드

출력 배열·객체의 항목마다 아래 필수 필드를 적용한다. `properties`·`match_key` 등 명시된 JSON map은 확장 가능하고 일반 객체는 미정의 키를 거절한다.

### ReportSummary

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `candidate_count` | `integer` | 필수 | 리포트가 다루는 고유 검증 후보 수. |
| `confirmed_count` | `integer` | 필수 | 취약점이 확인된 리포트 항목 수. |
| `not_confirmed_count` | `integer` | 필수 | 유효 검증에서 위반이 확인되지 않은 리포트 항목 수. |
| `suspected_count` | `integer` | 필수 | 근거·기대 정책이 불충분한 위반 의심 리포트 항목 수. |
| `indeterminate_count` | `integer` | 필수 | 판단불가 리포트 항목 수. |
| `policy_blocked_count` | `integer` | 필수 | 정책 차단으로 실행하지 않은 리포트 항목 수. |
| `approval_pending_count` | `integer` | 필수 | 사용자 승인 대기로 실행하지 않은 리포트 항목 수. |

### Finding

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `finding_id` | `string` | 필수 | 리포트 항목 ID. |
| `candidate_id` | `string` | 필수 | 원본 후보 ID. |
| `scenario_id` | `string / null` | 필수 | 원본 시나리오 ID. 생성되지 못한 후보는 null. |
| `verification_id` | `string / null` | 필수 | 원본 검증 결과 ID. 검증 결과를 만들지 못한 후보는 null. |
| `category` | `enum: authorization, business_logic` | 필수 | 취약점 분류. |
| `vulnerability_type` | `string` | 필수 | 구체 유형. |
| `status` | `enum: confirmed, suspected, not_confirmed, indeterminate, policy_blocked, approval_pending` | 필수 | 실제 판정·미검증 상태. |
| `title` | `string` | 필수 | 리포트 항목 제목. |
| `description` | `string` | 필수 | 재현·판정·미검증 사유 설명. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 최종 리포트 근거 참조. |

### GroundTruthRef

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `dataset_id` | `string` | 필수 | 정답 데이터셋 ID. |
| `dataset_version` | `string` | 필수 | 정답 데이터셋 버전. |
| `path` | `string` | 필수 | 프로젝트 루트 기준 datasets//ground_truth.json 상대 경로. |
| `sha256` | `string` | 필수 | 정답 파일 바이트 SHA-256. |

### Metric

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `metric_id` | `string` | 필수 | 평가 지표 이름. 예: page_recall, confirmed_precision. |
| `value` | `number / null` | 필수 | 0~1 비율. 분모가 0이거나 측정하지 못하면 null. |
| `numerator` | `integer` | 필수 | 지표 분자 원시 개수. |
| `denominator` | `integer` | 필수 | 지표 분모 원시 개수. 미검증 항목을 숨기기 위해 임의 축소하지 않는다. |

### CountTriple

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `tp` | `integer` | 필수 | 정답 취약 사례와 일치한 양성 수. |
| `fp` | `integer` | 필수 | 정답 정상 사례에 대해 양성으로 분류한 수. |
| `fn` | `integer` | 필수 | 전체 평가 범위의 정답 취약 사례 중 양성으로 발견·확인하지 못한 수. |

### UnverifiedCounts

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `policy_blocked` | `integer` | 필수 | 정책 차단 수. |
| `approval_pending` | `integer` | 필수 | 승인 대기 수. |
| `indeterminate` | `integer` | 필수 | 판단불가 수. |

**재사용하는 계약 필드:** [ArtifactRef](02-common-contract.md), [ErrorItem](02-common-contract.md), [EvidenceRef](02-common-contract.md), [ModelInfo](02-common-contract.md), [RuntimeMetrics](02-common-contract.md).

## 변경 이력

| 날짜 | 문서 변경 | 기준·영향 |
| --- | --- | --- |
| 2026-10-10 | 현재 구현·연결 상태, 실행 입력 7종·출력 2종의 0.2.0, 자원 ID·계정 source·KG 평가 경계와 HTML 구현 범위를 반영 | `695b3c8`, PR #52·#53 반영. 기존 결과 분류·평가·출력 필드 유지, Ground Truth 0.1 유지 |

기존 책임 경계·미검증 후보 보존·평가 분모 규칙과 정답 필드 표는 유지하며 상세
과거 구현 이력은 모듈 README에 보존한다. 합동 회귀는 `897 passed, 12 skipped`다.
실제 동일 run 전체 연결, 비어 있지 않은 Verifier 검증 관계 수신, Ground Truth
버전 합의는 미완료다. 노션 원본 반영·export 기준일 갱신은 별도 관리 작업으로 남긴다.

---

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md) ·
