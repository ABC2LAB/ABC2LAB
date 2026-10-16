# reporter

진단 결과를 정리해 `diagnosis_report.json`과 로컬 HTML을 생성하고, development 모드에서 Ground Truth 기반 평가 결과를 생성하는 모듈이다.

## 현재 상태 — 2026-10-16

기준 커밋 `695e36b`(SA PR #97 병합) + 이번 작업 트리 기준이다.
기존 `default-v1`과 CLI 기본값은 유지하고, Workflow 이름을 제외해 순서 부분수열로
비교하는 선택적 평가 프로필 `default-v2`와 단계 구성·순서 보존 부분 재현율을 구현했다.
두 부분 점수는 GT마다 선택한 같은 관찰 workflow에서 계산한다. 실행 계약 `0.3.0`·
GT `0.1.0`·report 분류와 비Workflow 지표의 계산은 유지한다. SA PR 병합 외에 다른
진단 모듈을 추가 수정하지 않았고, 원본 GT·snapshot·Schema·Runner·Web 설정도
변경하지 않았다. 새 진단·운영 Docker 배포는 아직 수행하지 않았다.

| 구분 | 현재 상태 |
|---|---|
| 독립 구현 | 공개 `report`·`evaluate`·CLI, 결과 분류·개발 평가·로컬 HTML 생성 구현 완료 |
| 공개 계약 | 실행 입력 7종·출력 2종 `0.3.0`만 지원; Ground Truth는 기존 `0.1.0` 유지 |
| 입력 호환 | Collector 발견 엔드포인트·응답 식별자 필드 수용; KG·Safety 출력과 입력 사본 일치 확인 |
| 평가 | Endpoint 노드·Endpoint→USE→Parameter(추론 포함)와 normalized_requests를 정규화 키로 비교; 중복 분자 증가 없음 |
| 관계 평가 | 실제 snapshot에서 Page.url→비교용 path, USE의 Endpoint 문맥을 사용; 실제 node_id·관계 종류·방향 일치 필수. notes에 탈락 건수 기록 |
| Workflow 평가 | default-v1은 이름+전체 액션열 일치 유지; default-v2는 이름 제외·단일 관찰 workflow의 순서 부분수열 비교. 단계 order·반복 횟수 유지, 다른 workflow 연결 금지 |
| Workflow 부분 점수 | default-v2만 workflow_step_recall(구성)·workflow_ordered_step_recall(LCS)을 추가. GT마다 LCS→구성 일치 수→원문 workflow_id 순으로 같은 관찰 하나를 선택하고 GT 단계 합계로 집계 |
| 계정 매핑 | 선택적 Reporter 전용 설정으로 논리 actor→원본 account_id 연결; Candidate·User·관계에 동일하게 적용 |
| Precision | 양성 GT 최대 일대일 매칭 후 FP=prediction 수−TP; 음성·미매칭·중복 prediction 포함 |
| 독립 회귀 | Reporter `696 passed`(기존 560건+Workflow 136건), SA+담당 3개 `1357 passed, 12 skipped`(실제 Neo4j opt-in), Web 평가/Runner `122 passed` |
| 실제 전체 연결 | 앞선 run의 report·evaluate 연결 이력 유지. 기존 두 run을 저장 없이 비교: 전체 v1/v2 모두 0/5, v2의 두 부분 점수 모두 0/29. 새 SA·공통 어휘 GT의 새 진단은 별도 검증 |

`web-49ebec8c2f328963ecc3a7f46ebcf022`를 읽기 전용 재평가했다. 관계 3/52→32/52,
명시적 계정 매핑 적용 시 User 0/1→1/1·후보 TP 0→1이다. 이는 같은 입력의 평가 누락
수정이지 탐지 엔진 성능 향상이 아니다. 실제 검증 관계는 해당 run에서 0개다.
운영 Docker 재빌드·실제 .env 변경·새 진단은 이번 작업에서 수행하지 않았다.
현재 리포트 렌더링은 HTML이며 PDF 출력은 구현하지 않았다. 현재 명세는
[m8-reporter](../../docs/spec/m8-reporter.md)를 참고한다.

## 역할

reporter는 두 가지 공개 operation을 제공한다.

| operation | 역할 |
|---|---|
| `report` | 후보·시나리오·Safety 판정·검증 결과를 종합해 진단 리포트 생성 |
| `evaluate` | 수집·의미 분석·KG·진단 결과를 Ground Truth와 비교해 성능 평가 |

공개 함수는 다음 인터페이스를 사용한다.

```python
from modules.reporter.entrypoint import run

response = run(operation, input_paths, output_dir, context)
```

---

## 입력·출력

### report

입력:

```text
vulnerability_candidates.json
test_scenarios.json
safety_decisions.json
verification_results.json
```

출력:

```text
diagnosis_report.json
diagnosis_report-iteration-<NNN>.html
```

### evaluate

`report` 입력에 다음 파일을 추가로 사용한다.

```text
crawl_result.json
semantic_analysis.json
graph_query_result.json
ground_truth.json
```

출력:

```text
evaluation_results.json
```

Ground Truth는 development 평가에서만 사용하며 다른 진단 모듈로 전달하지 않는다.

---

## 계약

- 진단 실행 입력 7종: `0.3.0` (`crawl_result`, `semantic_analysis`, `graph_query_result`, `vulnerability_candidates`, `test_scenarios`, `safety_decisions`, `verification_results`)
- reporter 출력 2종: `0.3.0` (`diagnosis_report`, `evaluation_results`)
- development 평가 전용 `ground_truth` 입력: 기존 `0.1.0` 유지
- 입력 Schema: `schemas/input/`
- 출력 Schema: `schemas/output/`
- 선택적 평가 설정 Schema: `schemas/config/evaluation.schema.json` (Reporter 내부 설정, 산출물 계약 아님)
- 실행 산출물: `runs/<run_id>/artifacts/iteration-<NNN>/reporter/`
- HTML 리포트: `runs/<run_id>/reports/`
- Ground Truth: `datasets/<dataset_id>/ground_truth.json`

모든 입력은 JSON Schema, 파일 SHA-256, `run_id`, iteration, revision 및 참조 관계를 검증한 뒤 사용한다.

입력 Schema는 producer의 공개 계약을 reporter 내부에 복제해 관리하며, 다른 모듈 구현 코드를 직접 import하지 않는다.

생산자 계약이 변경된 경우 reporter는 해당 생산자의 정상 출력을 소비할 수 있도록 직접 사용하는 입력 Schema만 동기화한다.

`verification_results.data.graph_updates.relationships`의 검증 관계는
`source_account_id`에 실제 접근한 원본 계정 ID를 받고, `target_id`에 기존
Resource instance node_id를 받는다. `0.2.0`에서 합의한 필드 의미를 `0.3.0`에도
유지한다. 이전 `source_id`, 두 source 필드의 동시 입력, source 필드 누락은 거절한다.

검증 관계의 `source_account_id`를 User node_id로 해석해 DB에 반영하는 책임은 KG에 있다.
reporter는 이 검증 관계를 변환·저장하거나 node_id를 접두사로 추측하지 않는다.
semantic 및 KG snapshot의 일반 관계는 계속 `source_id`·`target_id`를 사용한다.
개발 평가의 원본 계정·역할 ID 참조 검증은 아래 `GraphReferenceIndex`로 계속 수행한다.

진단 실행 입력의 `0.1.0`·`0.2.0` 및 미지원 버전은 거절하며 기존 산출물을
묵시적으로 변환하지 않는다. 과거 단계별 이력은 아래 변경 이력에 보존한다.

현재 `test_scenarios` 입력은 합의된 `0.3.0` 계약과 필수
`data.scenarios[].resource_ids`를 수용한다. 이 필드는 최소 1개의 비어 있지 않은
문자열 배열이며, 원본 후보의 KG Resource instance node_id를 값·순서 그대로 담는다.
ID의 형식을 해석·재계산하거나 정렬·중복 제거하지 않는다. 후보와의 값 일치는
생산자가 보장하므로 소비자에서 다시 대조하는 로직은 추가하지 않는다.

2026-10-14 기준 Collector·KG·Safety Policy 출력 Schema와 Reporter 입력 사본은
최상위 `$id`·제목·설명을 제외하면 일치한다. semantic_analyzer·access_analyzer·
scenario_generator·verifier 출력은 현재 `0.2.0`이므로 담당자의 동시 전환이 필요하다.
Verifier는 아직 검증 관계를 빈 배열로 생성한다. 새 버전의 실제 전체 실행은 미검증이다.
공통 명세·Runner·local_web의 버전 수용도 각 담당자가 전환한다.

Ground Truth Schema·fixture·dataset 계약은 `0.1.0`으로 유지한다. 정답은 evaluate만 읽는다.

### Collector 0.3 입력

`data.discovered_endpoints`, 각 response의 `identifiers`·`identifiers_truncated`를
필수 필드로 수용한다. 발견 엔드포인트는 `basis=inferred`이며 실행된 요청이 아니다.
식별자 값의 숫자·문자열 JSON 타입을 보존하며, 미관찰 `null`과 관찰한 빈 배열을
구분한다. Reporter가 발견 레코드로 요청·접근 관계·취약점 후보를 만들지는 않는다.

---

## Knowledge Graph 입력

development 평가에서 사용하는 `graph_query_result.json`은 현재 Knowledge Graph의 `schema_version=0.3.0` 계약을 따른다.

### AccessRow provenance

`role_resource_access`의 `AccessRow`는 원본 요청 provenance를 포함한다.

```json
{
  "request_ids": [
    "request_read_order"
  ]
}
```

`request_ids`는 다음 조건을 만족해야 한다.

```text
최소 1개
중복 불가
빈 문자열 불가
```

### Resource Type / Instance

KG는 Resource 종류와 실제 Resource 인스턴스를 구분한다. 0.2에서 도입한 모델을 0.3에서도 유지한다.

Resource는 다음 필드를 사용한다.

```text
resource_key
resource_scope
match_key
```

`resource_scope`는 다음 두 값을 사용한다.

```text
type
instance
```

Resource Type 예시:

```json
{
  "resource_key": "order",
  "resource_scope": "type",
  "match_key": null
}
```

Resource Instance 예시:

```json
{
  "resource_key": "order",
  "resource_scope": "instance",
  "match_key": {
    "resource_key": "order",
    "identifiers": [
      {
        "key": "external_id",
        "value": "order-b"
      }
    ]
  }
}
```

`resource_ownership`은 Resource Instance를 대상으로 하며 `resource_scope="instance"`와 유효한 `match_key`를 요구한다.

`role_resource_access`의 Resource 관련 필드는 다음과 같다.

```text
resource_id
resource_key
resource_scope
match_key
```

Resource가 연결되지 않은 AccessRow는 네 필드가 모두 `null`일 수 있다.

```json
{
  "resource_id": null,
  "resource_key": null,
  "resource_scope": null,
  "match_key": null
}
```

reporter의 `evaluate`는 현재 KG 결과 중 `structure_snapshot`을 실제 구조 평가에 사용한다.

다른 query 결과 역시 producer의 `graph_query_result 0.3.0` 계약에 따라 정상적으로 검증할 수 있어야 한다.

---

## 원본 ID와 KG 노드 ID 검증

`evaluate`는 원본 계정·역할 ID와 KG 노드 ID를 구분한다. 예를 들어 `account_id=account_user_a`와 `node_id=user:account_user_a`는 서로 다른 식별자다.

입력의 `nodes`를 읽어 다음 reporter 내부 인덱스를 만든다. `GraphReferenceIndex`는 내부 검증 모델이며 공개 JSON에 추가되는 필드가 아니다.

| 내부 변수 | 연결 기준 | 사용하는 참조 |
|---|---|---|
| `user_node_id_by_account_id` | User의 `properties.account_id` → `node_id` | `normalized_requests.account_id` |
| `role_node_id_by_role_id` | Role의 `properties.role_id` → `node_id` | `normalized_requests.role_id`, `workflow.role_ids` |
| `node_type_by_id` | `node_id` → 노드 종류 | `endpoint_id`, `resource_ids`의 대상 종류 검사 |

관계의 `source_id`, `target_id`는 실제 `node_id`의 존재 여부를 검사한다. Workflow의 단계 순서·의존 단계, semantic의 원본 요청 참조 및 collector와의 계정·역할 일치 검사도 유지한다.

이 검증은 semantic 입력과 KG `structure_snapshot`에 공통으로 적용한다. 입력의 원본 ID나 노드 ID를 변경하지 않고, `user:`·`role:` 같은 접두사를 계산하거나 생산자의 ID 생성 함수를 import하지 않는다. 따라서 공개 속성이 유효하면 다른 형식의 노드 ID도 처리할 수 있다.

User의 `account_id`와 Role의 `role_id` 속성은 비어 있지 않은 문자열이어야 하며, 같은 종류의 노드 간 중복 원본 ID는 거절한다. 원본 ID 속성이 없으면 `node_id`에서 추측해 복구하지 않는다. 노드 ID가 원본 ID와 우연히 같더라도 올바른 공개 속성이 있으면 허용한다.

---

## 진단 결과 상태

리포트 finding은 다음 상태를 사용한다.

| 상태 | 의미 |
|---|---|
| `confirmed` | 취약점이 실제 실행으로 확인됨 |
| `suspected` | 취약 정황은 있으나 확정 근거가 부족함 |
| `not_confirmed` | 검증에서 취약점이 재현되지 않음 |
| `indeterminate` | 근거 부족으로 판단할 수 없음 |
| `policy_blocked` | Safety Policy가 실행을 차단함 |
| `approval_pending` | 사용자 승인을 기다리는 상태 |

미실행·차단·판단불가 상태를 취약점 없음으로 처리하지 않는다.

---

## 평가

기본 matching profile은:

```text
default-v1
```

이다.

기본값을 바꾸지 않고 `--matching-profile default-v2`로 새 Workflow 비교를 선택할 수
있다. 공개 `run()`에서는 `context.matching_profile`에 `default-v2`를 지정한다.
산출물의 `data.matching_profile`과 notes에 실제 적용한 규칙을 기록한다.

Ground Truth와 분석 결과는 내부 ID 문자열을 직접 비교하지 않고 의미 정보를 기준으로 연결한다.

주요 비교 정보:

- Page path
- Endpoint method + path template
- Parameter 위치·이름
- Role 이름
- Resource 종류와 원문 식별키·값

### Workflow 비교 — default-v1 / default-v2

| 항목 | default-v1(기존 기본값) | default-v2(선택) |
|---|---|---|
| 표시 이름 | 정규화한 name 일치 필요 | 비교하지 않음 |
| 액션열 | 전체 일치 | GT ordered_actions가 단일 관찰 workflow의 순서 부분수열이면 일치 |
| 관찰 순서 | steps의 order로 정렬 | 동일; 중간·앞뒤의 다른 단계는 허용 |
| 반복 액션 | 반복 포함 전체 일치 | GT에 필요한 횟수만큼 실제 관찰돼야 함 |
| GT 중복 키 | 정규화한 이름+액션열 | 정규화한 액션열; 이름/constraints만 다르면 중복으로 거절 |
| 부분 점수 | 추가하지 않음 | 단계 구성·순서 보존 재현율을 별도로 출력 |

설명용 합성 토큰으로 GT `A → B`는 v2에서 관찰
`A → X → B`와 일치한다. 역순·필수 단계 누락은 전체 흐름으로
일치하지 않는다. 여러 계정/관찰 workflow를 이어 붙이지 않으며, 같은 GT가 여러
관찰에 포함돼도 분자는 한 번만 증가한다. 하나의 긴 관찰은 서로 다른 여러 GT 흐름을
포함할 수 있다. 빈 GT 액션열 거절과 분모 0의 `null` 규약은 유지한다.

두 프로필 모두 기존 공백·대소문자 정규화 후 액션 토큰을 비교하며 동의어를 만들거나
요청 method/path에서 토큰을 재생성하지 않는다. 결정적 액션 규칙과 GT 토큰 재생성은
semantic_analyzer/GT 담당자의 작업이다. 자유 서술 constraints·역할별 업무 조건을
검증하는 지표가 아니므로 v2 recall은 **업무 액션 순서의 관찰 포함률**이다.
업무 완주·실제 실행 성공·정책 준수·취약점 탐지율을 뜻하지 않는다.

#### 부분 점수 — default-v2만

전체 흐름의 포함 여부와 일부 단계의 관찰 정도를 구분한다. 기존 `Metric` 배열에
아래 지표를 담으며 새 필드·Schema·버전을 추가하지 않는다. v1에는 부분 지표와
부분 점수 notes를 넣지 않는다.

| metric_id | 분자 | 분모 |
|---|---|---|
| workflow_recall | GT 액션열 전체가 순서대로 포함된 GT workflow 수 | 전체 GT workflow 수 |
| workflow_step_recall | GT별 선택 흐름과의 순서 무관 단계 일치 수 합계 | 전체 GT 단계 수 합계 |
| workflow_ordered_step_recall | 같은 선택 흐름과의 최장 공통 부분수열(LCS) 길이 합계 | 전체 GT 단계 수 합계 |

GT마다 관찰 workflow 하나를 독립적으로 선택한다(A안).

1. 순서 보존 일치 수인 LCS 길이가 큰 흐름을 우선한다.
2. 동점이면 순서 무관 단계 일치 수가 큰 흐름을 선택한다.
3. 다시 동점이면 원문 `workflow_id` 오름차순으로 선택한다. ID는 정규화하지 않는다.
4. 두 분자는 반드시 선택한 같은 흐름에서 구한다. 모든 후보가 0점이면 대응 없음이다.

구성 일치 수는 액션별 반복 횟수의 교집합이다. GT에 `read`가 두 번 있어도 관찰이
한 번이면 한 단계만 일치한다. LCS도 관찰 단계 하나를 중복 소비하지 않는다.
여러 workflow/계정의 단계를 연결하거나 지표별로 서로 다른 최댓값을 취하지 않는다.
같은 관찰을 서로 다른 GT가 선택하는 것은 허용하며 업무흐름 분리 품질과 구분한다.

아래는 합성 토큰의 계산 예이며 실제 앱의 액션 규칙이 아니다.

| GT | 관찰 후보 | 선택한 흐름 | 단계 구성 | 순서 보존 | 전체 흐름 |
|---|---|---|---|---|---|
| A B C D | A B C | A B C | 3/4 | 3/4 | 0/1 |
| A B C D | A C B D | A C B D | 4/4 | 3/4 | 0/1 |
| A B C D | D C B A; A B C | A B C | 3/4 | 3/4 | 0/1 |

GT별 비율을 평균하지 않고 분자와 분모를 각각 합산한다(micro 집계). 예를 들어
4단계 GT에서 3단계, 1단계 GT에서 1단계를 찾으면 `4/5`이며 비율 평균 `7/8`이 아니다.

- GT workflow가 없어 단계 분모가 0이면 두 부분 비율은 `null`이다.
- GT 단계는 있지만 유효한 snapshot의 workflows/steps가 비었거나 겹침이 없으면 0이다.
- snapshot이 없거나 실패했다면 평가 전체를 `failed / data=null`로 반환한다. 0점으로 숨기지 않는다.
- 유효한 부분 입력은 기존 `partial`·오류를 보존한다. SA data가 없어도 유효한 KG snapshot이
  있으면 Workflow는 평가하고, SA에 의존하는 Endpoint·Parameter는 미측정으로 구분한다.

notes에 공식·선택 기준·빈 입력과 재사용 한계를 남긴다. 부분 지표는 **관찰 단계 커버리지**이며,
업무 단위 분리·필수 선행/분기·의존관계 정확도나 LLM의 업무흐름 생성 품질을 평가하지 않는다.

이미 완료된 평가 파일은 덮어쓰지 않는다. 과거 v1 값과 v2 값은 같은 평가 조건으로
취급하지 않으며, 동일 GT/관찰 입력에 두 프로필을 적용해 비교한다.
Web/Runner의 기본값은 `default-v1`이다. Web에서 v2를 사용하려면 새 Reporter
코드를 이미지에 반영하고 `LOCAL_WEB_MATCHING_PROFILE=default-v2`를 명시해야 한다.
이번에는 Web·실제 환경변수·Docker 이미지를 변경하지 않았다.

#### 동일 입력 비교 기록 — 2026-10-16

| 기존 진단 | 전체 v1 | 전체 v2 | 단계 구성 v2 | 순서 보존 v2 |
|---|---|---|---|---|
| web-49ebec8c2f328963ecc3a7f46ebcf022 | 0/5 (0%) | 0/5 (0%) | 0/29 (0%) | 0/29 (0%) |
| web-4069974f795330d58678ef506de57004 | 0/5 (0%) | 0/5 (0%) | 0/29 (0%) | 0/29 (0%) |

각 run은 GT workflow 5개·29단계, 관찰 workflow 4개·35단계다. 원본을 수정하지 않고
공개 artifacts의 임시 복사본을 기존 입력 adapter로 검증한 뒤 출력 builder를 메모리에서
실행했다. 두 run×두 프로필의 출력 Schema·출력/입력 재계산 일치를 각각 4건 검증했다.
전체 DP 행렬 LCS·리스트 제거 방식의 독립 참조 계산으로 GT 흐름 10개의 선택과 점수를 대조했다.
기존 evaluation의 input_refs 7개·GroundTruthRef 해시를 대조하고 run별 입력/저장 평가
9개 파일의 비교 전후 해시 불변을 확인했다. 평가 파일을 재발행하거나 원문 응답·private·
세션을 읽지 않았다. 두 프로필 사이의 비Workflow 지표 12개·후보/확정/미검증 집계·
모델·원본 실행 측정값·GT 참조·graph revision은 동일했다. 두 run 모두 평가 binding은 0개다.

이 두 입력의 GT 고유 액션 26개와 관찰 고유 액션 14개 사이에 같은 토큰이 0개다.
SA와 KG snapshot의 workflow별 정규화 액션열도 동일해, 현재 원본 어휘로는 부분 점수도
올라가지 않았다. 이는 관찰 단계 자체가 없다는 뜻이 아니며 해당 HTTP 요청의 미수집을
증명하지 않는다. SA PR #97의 결정적 토큰은 과거 산출물에 소급 적용하지 않았다.
다음 성능 비교는 담당자가 같은 규칙의 GT와 새 SA/KG 산출물을 발행한 뒤 수행한다.
Reporter는 과거 토큰 보정·동의어 치환·누락 단계 추론으로 점수를 올리지 않는다.
합성 회귀의 이름 불일치/추가 단계 사례는 각각 전체 0/1→1/1이며 실제 대상의 개선과 구분한다.

### Resource 정규화 — 0.2에서 도입, 0.3에서도 유지

Ground Truth를 KG 내부 Resource 표현에 직접 종속시키지 않는다.

reporter는 평가 시점에만 KG 0.2 Resource를 비교용 view로 정규화한다.

예를 들어 기존 Ground Truth가 다음과 같은 Resource를 사용할 수 있다.

```json
{
  "resource_type": "order",
  "external_id": "order-b"
}
```

KG 0.2의 실제 Resource 표현은 다음과 같을 수 있다.

```json
{
  "resource_key": "order",
  "resource_scope": "instance",
  "match_key": {
    "resource_key": "order",
    "identifiers": [
      {
        "key": "external_id",
        "value": "order-b"
      }
    ]
  }
}
```

reporter는 평가 시 다음 의미가 서로 비교될 수 있도록 정규화한다.

```text
resource_key  → resource_type 호환
resource_key  → name 호환

match_key.identifiers[]
              → identifier key/value 비교
```

따라서 기존 Ground Truth의 표현을 유지하면서 현재 KG Resource를 평가할 수 있다.

실제 KG artifact 자체를 변환하거나 수정하지 않는다.

### Resource 식별값 원문 보존

Resource의 정규화는 **비교용 표현을 맞추는 것**이며 문자열을 소문자로 바꾸거나 공백을 제거하는 것이 아니다.

- `resource_key`와 identifier의 `key`, `value`는 대소문자·앞뒤 공백·연속 공백을 그대로 비교한다.
- 예를 들어 `Ab`와 `ab`, `item-1`과 ` item-1 `은 서로 다른 자원 식별값이다.
- identifier 이름이 `method`, `path`, `url`이어도 HTTP Method·경로용 정규화를 적용하지 않는다.
- 복합키는 모든 지정된 식별값을 비교한다. KG 형태의 `match_key.identifiers`는 나열 순서만 무시하며 키·값은 변경하지 않는다.
- Ground Truth의 Resource entity와 case 중복 검사에도 같은 원문 보존 기준을 적용한다. 대소문자·공백이 다른 자원을 중복으로 거절하지 않고, 같은 복합키는 나열 순서가 달라도 중복으로 거절한다.

구조·관계·후보/case 매칭에서 Resource 여부를 명시적으로 전달한다. 기존 Ground Truth의 `resource_type`, `name` 호환 표현은 유지한다.

Role 이름·일반 엔티티 텍스트·HTTP Method·Page/Endpoint 경로·Workflow의 기존 정규화와 `default-v1` matching profile은 유지한다. 실행 Schema 버전은 `0.3.0`이다.

### Endpoint·Parameter 발견율의 범위

`endpoint_recall`은 semantic_analysis의 Endpoint 노드와 normalized_requests를
함께 비교한다. 추론 노드도 method·path_template이 GT에 매칭되면 포함한다.
같은 엔드포인트의 노드·요청이 반복돼도 정답 항목 하나당 한 번만 집계한다.
필요한 매칭 키가 없거나 다르면 매칭하지 않는다. 의미 분석 실패 시에는 미측정이다.

이 지표는 발견율이며 실제 요청·ACCESS·검증 성공·confirmed를 뜻하지 않는다.

`parameter_recall`은 기존 normalized_requests와 함께 semantic_analysis의
`Endpoint → USE → Parameter`를 비교한다. Endpoint의 method·path_template과
Parameter의 name·location으로 비교용 키를 구성하며 inferred 노드도 포함한다.
node_id를 해석하거나 값을 읽어 엔드포인트를 추측하지 않는다.

USE가 없거나 방향·노드 종류가 맞지 않으면 해당 노드를 평가 관찰에 넣지 않는다.
매칭 필드 누락·빈 값·비문자열·불일치는 매칭하지 않는다. 기존 정규화 요청의
평가 방식은 유지하고 노드·요청 중복으로 분자를 늘리지 않는다. 의미 분석 실패는
미측정, GT 분모 0은 null이다. 민감 Parameter도 값 없이 이름·위치만 비교한다.

입력·KG snapshot은 변경하지 않는다. 관계·후보·확정 집계·GT 분모 규칙과
default-v1, 공개 출력 필드·버전은 유지한다. 발견율 변화 자체를 탐지 모델의 성능
향상이나 실제 검증 성공으로 판단하지 않는다.

### 실제 snapshot의 관계 평가

`relationship_recall`은 `graph_query_result.structure_snapshot`만 사용한다.
개별 Page/Endpoint/Parameter 발견율에 쓰는 crawl/semantic 데이터를 가져와 KG 관계를
만들지 않는다. 구현은 `relationship_matching.py`의 비교 전용 view에 한정된다.

- Page에 path가 없으면 url을 비교용 path로 제공하고 기존 경로 정규화를 적용한다.
  기존 path·url 조건은 유지하며 URL이나 node_id를 다시 생성하지 않는다.
- Parameter는 실제 `Endpoint → USE → Parameter`의 method·path_template·name·location을
  비교한다. 기존 속성도 보존하고 endpoint 객체·endpoint_method/endpoint_path_template·
  method/path_template 형태의 비교 키를 지원한다. 필수 값 누락은 추론하지 않는다.
- 같은 Parameter 노드를 여러 Endpoint가 사용해도, 다른 Endpoint에서 얻은 문맥으로
  USE 관계를 정답 처리하지 않는다. 실제 source_id·target_id·relation_type·방향이 맞아야 한다.
- 중복 edge는 GT 관계 하나의 분자를 늘리지 않는다. User는 기존 actor binding을 재사용하고,
  Role·Resource 비교 및 자원 이름 원문 비교는 유지한다.
- notes에는 일치 / 출발 노드만 미매칭 / 도착 노드만 미매칭 / 양쪽 미매칭 /
  양끝은 일치하지만 관계 미매칭 건수를 남긴다. 다섯 건수의 합은 GT 관계 분모다.
  미매칭은 snapshot과 GT 비교 결과이며 실제 취약/정상 판정이 아니다.

### 평가 대상

주요 평가 대상:

```text
그래프 구조
관계
workflow
후보 recall / precision
확정 recall / precision
```

Resource 정규화는 다음 평가 경로에 공통으로 적용된다.

```text
구조 평가
관계 평가
취약점 후보 ↔ Ground Truth case 평가
```

분모가 0이거나 측정할 수 없는 지표는 `value=null`로 기록한다.

### 평가 계정 매핑 — GT와 실행 환경 분리

GT의 논리 actor(`actor_alias`, User `match_key.alias`)를 실행 계정 이름으로
덮어쓰지 않는다. 선택적 `REPORTER_EVALUATION_CONFIG_PATH` 환경변수로 별도
JSON 설정을 지정한다. 경로는 evaluate의 `project_root` 기준 상대 경로이며
절대 경로·`..`·루트 밖 symlink·없는 파일은 거절한다. 예시는
`configs/shop_demo.evaluation.example.json`이고 자동 적용하지 않는다.
실제 테스트 계정용 `configs/shop_demo.evaluation.json`도 제공한다. 이 파일은
`shop_demo`·dataset version `1.0.0`에만 사용하며 alice→`account:user_a`,
bob→`account:user_b`, admin→`account:admin_a`, guest→`account:guest`를 연결한다.
설정 파일이 존재하는 것만으로 로드하지 않으며 환경변수로 명시해야 한다.

```json
{
  "schema_version": "0.1.0",
  "dataset_id": "example_dataset",
  "dataset_version": "1",
  "actor_bindings": [
    {"actor_alias": "logical_actor", "account_id": "execution_account_id"}
  ]
}
```

- 설정의 dataset_id·dataset_version은 GT와 정확히 같아야 한다. 설정 0.1.0은
  별도의 내부 설정 버전이며 GT/실행 산출물 버전을 변경하지 않는다.
- actor alias는 기존 텍스트 정규화 후 중복을 거절하고 account_id는 원문으로
  비교한다. 같은 account_id의 중복 매핑도 거절한다.
- 명시된 binding은 candidate.actor_account_id 및 KG User properties.account_id와
  정확히 비교한다. User를 source/target으로 삼는 관계 평가도 같은 기준을 사용한다.
- 명시된 binding 불일치·account_id 누락은 미매칭이다. alias가 같다는 이유로
  binding을 무시하지 않는다. 매핑이 없는 actor와 설정 없는 기존 실행은 alias 비교를 유지한다.
- User의 나머지 match_key 조건은 유지한다. Role·Page·Action·Endpoint·Parameter·
  Resource의 기존 비교 방식은 바꾸지 않는다. 미관찰 계정을 임의 생성하지 않는다.
- 비밀번호·세션·쿠키·토큰 필드는 설정 Schema에서 거절한다. GT·LLM·진단 모듈에
  매핑을 전달하지 않는다. report는 이 설정을 읽지 않는다.
- 준비 시 설정 Schema·SHA-256을 검증하고 저장 직전 같은 파일 해시를 재확인한다.
  notes에는 설정 경로·해시·binding 수만 남기며 기존 input_refs·GT 참조·원자 저장은 유지한다.
  설정이 없으면 notes에 기존 alias 기준임을 명시한다.

기존 run()/CLI 인자는 그대로다. evaluate 실행 시 환경변수만 선택적으로 지정한다.

```bash
REPORTER_EVALUATION_CONFIG_PATH=modules/reporter/configs/shop_demo.evaluation.json \
  .venv/bin/python -m modules.reporter.entrypoint evaluate \
  --run-root runs/<run_id> --run-id <run_id> --iteration 0 \
  --mode development --target-url http://target.internal \
  --project-root . --dataset-id shop_demo --matching-profile default-v1
```

예시의 dataset 버전·계정 ID는 실제 평가 환경과 대조해야 한다. GT 원본에
실행별 account_id를 추가하거나 GT 계약 버전을 올리지 않는다.

local_web Compose도 같은 환경변수를 전달한다. Docker에서는 `/app` 기준 상대 경로를
사용한다. Web은 설정 내용을 읽지 않고 자식 Runner에 상속하며, 선택적 빈 값은 자식
환경에서 제거한다. 비어 있지 않은 잘못된 값은 Reporter가 기존대로 거절한다.
실제 적용 절차와 완료 산출물 불변 원칙은 [local_web README](../../local_web/README.md)를 따른다.

계정 매핑은 development 평가의 동일 계정 식별만 보완한다. 후보 생성·Verifier
실행 결과·Reporter의 confirmed 기준을 바꾸거나 GT에 맞춰 취약점을 확정하지 않는다.
Web은 Verifier 조건 통과와 Reporter 확정을 구분하고, 확정 외 항목의 description을
원문 그대로 표시한다. `inferred` 성공이 `suspected`에 남는 등 확정되지 않은 사유는
기존 report가 생성하며 Web이 다시 판단하지 않는다.

### Candidate·confirmed TP/FP/FN

이 evaluate는 **GT가 대상의 완전한 정답 집합인 benchmark**를 가정한다.
GT가 불완전한 실제 웹의 탐지 결과 전체에 이 Precision 의미를 그대로 적용하지 않는다.

```text
TP = 양성 GT와 최대 일대일 매칭된 prediction 수
FP = 전체 prediction 수 − TP
FN = 양성 GT 수 − TP
Precision = TP / 전체 prediction 수
```

양성 GT부터 최대 일대일 매칭한 뒤 남은 prediction의 명시적 음성 매칭을
별도 진단 정보로 계산한다. 하나의 양성 GT를 중복 TP로 소비하지 않으며,
음성 GT 매칭·어느 GT에도 일대일 배정되지 않은 prediction·중복 양성 예측은 FP다.
prediction 0개면 Precision은 null, prediction이 있지만 TP가 0이면 0.0이다.

candidate는 고유 후보 전체, confirmed는 report가 이미 confirmed로 분류한
고유 candidate_id만 평가한다. 여러 confirmed 시나리오가 같은 후보를 중복 집계하지 않는다.
정책 차단·승인 대기·판단불가 분류는 바꾸지 않으며, 이 FP는 benchmark 매칭 집계이지
미실행 후보의 실제 취약/정상 판정이 아니다. 미검증 양성 GT는 FN 분모에서 제외하지 않는다.

notes는 양성·명시적 음성·일대일 미매칭 수와 기존 미매칭 설명을 유지한다.
Resource 이름 동의어(user/id, product/products, order/orders)는 보정하지 않는다.

---

## 로컬 HTML

HTML 리포트는 `diagnosis_report.json`을 기반으로 생성되는 파생 출력이다.

- 외부 JavaScript 없음
- 외부 CSS 없음
- 네트워크 요청 없음
- 동적 문자열 HTML escape
- 폐쇄망에서 로컬 파일로 실행 가능
- 브라우저 인쇄를 통한 PDF 저장 가능

---

## 실패 처리

주요 오류는 다음과 같이 구분한다.

```text
CONTRACT_INVALID
PATH_INVALID
INPUT_HASH_MISMATCH
OUTPUT_EXISTS
OUTPUT_INVALID
STORAGE_FAILED
```

완료된 출력 파일은 덮어쓰지 않는다.

입력이 `partial`이면 누락·실패 범위를 보존하며, 판단할 수 없는 경우 정상적인 빈 결과로 바꾸지 않는다.

---

## CLI

진단 리포트:

```bash
python -m modules.reporter.entrypoint report \
  --run-root runs/<run_id> \
  --run-id <run_id> \
  --iteration 0 \
  --mode diagnosis \
  --target-url http://target.internal
```

개발 평가:

```bash
python -m modules.reporter.entrypoint evaluate \
  --run-root runs/<run_id> \
  --run-id <run_id> \
  --iteration 0 \
  --mode development \
  --target-url http://target.internal \
  --project-root . \
  --dataset-id <dataset_id> \
  --matching-profile default-v1
```

---

## 테스트

저장소 루트에서 실행한다.

전체 reporter 테스트:

```bash
.venv/bin/python -m pytest modules/reporter/ -q
```

현재 결과:

```text
696 passed
```

계약 테스트:

```bash
.venv/bin/python -m pytest modules/reporter/tests/test_contracts.py modules/reporter/tests/test_contract_migration.py modules/reporter/tests/test_crawl_0_3_contract.py -q
```

현재 결과:

```text
132 passed
```

계정 source 전환 계약·공개 실행 테스트:

```bash
.venv/bin/python -m pytest modules/reporter/tests/test_verification_account_contract.py -q
```

현재 결과:

```text
34 passed
```

평가 테스트:

```bash
.venv/bin/python -m pytest modules/reporter/tests/test_evaluation_service.py -q
```

현재 결과:

```text
56 passed
```

원본 ID / 노드 ID 참조 회귀 테스트:

```bash
.venv/bin/python -m pytest modules/reporter/tests/test_graph_references.py -q
```

현재 결과:

```text
74 passed
```

Resource 식별값 회귀 테스트:

```bash
.venv/bin/python -m pytest modules/reporter/tests/test_resource_matching.py -q
```

현재 결과:

```text
66 passed
```

추가 실행 의존성 없이 팀 공통 Python 환경과 루트 의존성을 사용한다.

Workflow 프로필·부분 점수·공개 실행 회귀(2026-10-16):

```bash
.venv/bin/python -m pytest modules/reporter/tests/test_workflow*.py -q --tb=short
# 136 passed: 전체 포함률 40 + 순수 계산 43 + 부분 평가 16 + 통합 회귀 37
```

프로필별 값·반복/분리/빈 입력·GT 중복·partial/failed·snapshot 부재, 공개 CLI,
실행 입력 7개/GT 해시·평가값 변조·완료 파일 불변·저장 실패 정리·diagnosis 분리를 검증한다.
이 테스트 건수와 위 두 실제 진단의 0점 결과는 서로 다른 검증이다.

연관 모듈·Web 회귀(2026-10-16, 실제 서비스 연결 시험과 구분):

```bash
KG_RUN_NEO4J_INTEGRATION=0 .venv/bin/python -m pytest modules/semantic_analyzer modules/knowledge_graph modules/safety_policy modules/reporter -q --tb=short
# 1357 passed, 12 skipped (실제 Neo4j opt-in 미실행)

.venv/bin/python -m pytest local_web/tests/test_evaluation.py local_web/tests/test_runner.py -q --tb=short
# 122 passed
```

---

## 변경 이력

### 2026-10-15 — shop_demo 평가 프로필의 명시적 운영 적용

실제 계정용 `configs/shop_demo.evaluation.json`을 추가하고 local_web 운영 설정에서
명시적으로 선택한다. example·legacy alias fallback·기존 로더의 dataset/version·
Schema·SHA-256 검증을 유지한다. GT·평가 알고리즘·report 확정 기준·공개 Schema·
버전은 변경하지 않는다. Web의 조건 통과/확정 구분과 사유 표시는 Presentation 변경이며
기존 완료 산출물을 재발행하지 않는다.

검증은 `.venv/bin/python -m pytest modules/reporter -q --tb=short`에서 560 passed,
`LOCAL_WEB_BROWSER_TESTS=1 .venv/bin/python -m pytest modules/reporter local_web/tests -q --tb=short`에서
894 passed·1 skipped(마운트 전용 환경 미실행)다. 실제 Docker의 활성 설정으로
기존 진단을 읽기 전용 재평가해 User 0/1→1/1·후보 TP 1→2·확정 0 유지를 확인했다.
공개 산출물 10개 해시는 불변이며 새 진단·LLM 호출은 실행하지 않았다.

아래 기록의 테스트 수치·연결 상태·다음 작업은 작성 당시 기준이다.
현재 계약과 연결 상태는 위 계약 절을 따른다.

### 2026-10-09 — 6단계: 담당 모듈 최종 회귀·생산자 산출물 수신 확인

기준 커밋 `1b123bd`에서 1~5단계 수정의 최종 회귀와 공개 입력 계약을 확인했다. 추가 구현 오류가 발견되지 않아 이번 단계에서는 README만 갱신했다. 다른 모듈·공용 명세·Schema·fixture 원본·의존성은 변경하지 않았다.

#### reporter 입력별 확인 결과

| 입력 | 생산자 / reporter 버전 | 확인 결과 |
|---|---|---|
| `crawl_result` | `0.2.0` / `0.2.0` | collector 공개 출력 fixture의 입력 Schema 검증 통과 |
| `semantic_analysis` | `0.2.0` / `0.2.0` | 생산자 CLI가 실제 생성한 결과의 입력 Schema·원본 ID·노드/Workflow 참조 검증 통과 |
| `graph_query_result` | `0.2.0` / `0.2.0` | KG 공개 출력 fixture의 입력 Schema 검증 통과 |
| `vulnerability_candidates` | `0.2.0` / `0.2.0` | access_analyzer 공개 출력 fixture의 입력 Schema 검증 통과 |
| `test_scenarios` | `0.1.0` / `0.2.0` | scenario_generator 공개 출력 fixture는 버전 불일치로 거절 |
| `safety_decisions` | `0.2.0` / `0.2.0` | safety_policy 공개 출력 fixture의 입력 Schema 검증 통과 |
| `verification_results` | `0.2.0` / `0.2.0` | verifier 공개 출력 fixture의 입력 Schema 검증 통과 |
| `ground_truth` | `0.1.0` / `0.1.0` | `datasets/shop_demo/ground_truth.json`의 입력 Schema 검증 통과 |

semantic_analyzer에는 문서상 공개 경로의 출력 fixture가 없어서 공개 CLI를 임시 디렉터리에서 실행했다. 생산자 소유 수집 fixture와 기본 `fake` LLM으로 요청 6개·노드 27개·관계 33개·Workflow 3개를 생성했고, 생산자·KG·reporter의 세 Schema와 KG/reporter 입력 adapter 검증을 통과했다. 원본 수집 파일 해시를 보존하고 임시 산출물은 검증 후 정리했다. 실제 Local LLM 품질이나 전체 파이프라인 실행 검증은 아니다.

정답 dataset은 entity 65개·관계 52개·Workflow 5개·case 10개이며 Schema 오류는 0건이다. 이는 정답 입력 형식 검증이고, 해당 dataset의 진단 정확도를 측정한 것은 아니다.

#### 계약 대조 범위

담당 3개 모듈의 직접 생산자·소비자 경계 13개를 대조했다. 최상위 제목·설명·`$id`를 제외하고 사본이 같은 경계는 7개다. 3개는 버전 차이이며, 나머지 3개는 KG 관련 정의 이름·`$ref` 등의 구조 표현 차이가 있다. Schema 파일의 전체 일치, 샘플 수신 성공, 실행 내 교차 참조 검증은 서로 다른 검증 결과로 구분한다.

기존 공개 fixture가 있는 경계 11개 중 Schema 검증 8개는 통과했고 3개는 아래 버전 문제로 거절됐다. semantic 2개 경계는 위 실제 CLI 생성 결과로 별도 확인했다. 서로 다른 모듈의 예시 파일을 동일 실행의 산출물인 것처럼 조합하거나 원본 ID·해시를 바꿔 연결하지 않았다.

#### 회귀 결과

```text
knowledge_graph 기본 테스트
160 passed, 4 skipped

safety_policy
145 passed

reporter
356 passed

담당 3개 모듈 기본 테스트
661 passed, 4 skipped

담당 3개 모듈, 실제 Neo4j 통합 테스트 활성화
665 passed

modules/ 전체 테스트
1456 passed, 4 skipped
```

기본 합동 검증 명령은 `.venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter -q -rs`, 전체 모듈 명령은 `.venv/bin/python -m pytest modules -q -rs --tb=short`다. 전체 모듈의 최초 실패는 Chromium·로컬 서버의 샌드박스 실행 제한이었으며, 실행 권한을 확보한 환경에서 재실행해 통과했다.

실제 Neo4j 4개 테스트는 기존 `neo4j:5.26.31-community` 이미지의 일회성 DB에서 실행하고 해당 컨테이너·임시 볼륨만 정리했다. 기존 DB와 볼륨은 사용하지 않았다. 기본 실행의 skip과 실제 DB 실행 성공을 구분한다.

`report`·`evaluate`·CLI의 정상/partial/failed 출력, 미검증 후보 보존, 원본 account/role ID와 KG node_id 구분, Resource 원문·복합키·GT 중복 검사, 진단 상태 분류, 출력 Schema·참조 해시·HTML·원자 저장을 기존 테스트로 회귀 확인했다. AST 검사에서 reporter Python 파일 31개의 다른 모듈 import 0건, Schema 10개의 외부 `$ref` 0건을 확인했다.

공통 환경은 Python `3.12.13`이며 `uv pip check --python .venv/bin/python`은 설치 패키지 21개의 호환성을 확인했다. 이는 현재 설치 환경의 검사이며 lock 파일을 재생성하거나 패키지를 설치·변경한 것은 아니다.

#### 완료 범위와 후속 연계 항목

이번 수정의 1~6단계는 완료했다. 추가 구현 단계는 남기지 않으며, 전체 파이프라인 연결 전에는 다음을 별도로 확인한다.

- scenario_generator 담당: `test_scenarios` 출력 0.2 전환과 새 산출물 발행. 구버전을 reporter/safety_policy가 자동 변환하지 않는다.
- verifier 담당: `safety_decisions` 입력 사본의 0.2 전환. 현재 Safety의 0.2 판정은 해당 0.1 사본에서 거절된다.
- 정답 담당자·관리자: 공통 명세의 11개 파일 0.2 안내와 `m8-reporter.md`의 Ground Truth 0.1 표 정리. 현재 정답 Schema와 dataset은 기존 0.1로 유지한다.
- 연결 검증: 위 항목 반영 후 동일 실행의 수집·후보·계획·판정·검증·KG revision·리포트로 실제 연결을 확인한다. 독립 테스트 통과나 서로 다른 예시 파일의 Schema 통과로 이를 대체하지 않는다.

노션 원본은 현재 연결에서 404로 반환되어 저장소 명세 스냅샷과 공개 Schema를 사용했다. 원본과 스냅샷의 최신 일치 여부는 확인하지 못했으며 `docs/spec/`을 수정하지 않았다. `IMPLEMENTATION.md`는 생성하지 않고 이 README에 이력을 누적한다.

### 2026-10-09 — 5단계: Resource 식별값 원문 보존

기준 커밋 `41717e7`에서 reporter의 일반 텍스트 정규화가 Resource 식별값에도 적용되어 서로 다른 자원을 같은 대상으로 매칭하거나 Ground Truth 중복으로 거절하는 문제를 수정했다.

변경 내용:

- `matching.py`에 Resource 전용 비교·중복 signature를 추가했다. 식별키·문자열의 대소문자와 공백을 보존하고, JSON 값의 타입도 구분한다.
- KG 0.2 Resource의 비교용 alias와 identifier 펼침을 유지했다. `match_key.identifiers`의 순서는 비교용 signature에서만 정렬하며 실제 입력을 변경하지 않는다.
- `evaluation_service.py`의 구조 평가·관계 평가·후보/case 매칭·GT entity 중복 검사에 Resource 비교 기준을 명시적으로 적용했다. GT case 중복 검사도 동일한 기준을 사용한다.
- `test_resource_matching.py`에 원문 차이, 복합키 순서, 동일 자원 중복 거절, 일반 엔티티 정규화 유지, 공개 `evaluate` 출력·해시 검증을 추가했다.

다른 모듈, Schema, 버전, fixture 원본, 진단 상태 분류, HTML, CLI, 의존성은 변경하지 않았다. 평가 계산식은 유지하되 Resource의 실제 매칭 건수는 원문 일치 여부에 따라 달라진다. `IMPLEMENTATION.md`는 만들지 않고 README에 이력을 누적한다.

검증 결과:

```text
test_resource_matching.py
66 passed

reporter 전체
356 passed

knowledge_graph + access_analyzer + safety_policy + reporter
741 passed, 4 skipped
```

공개 `evaluate` 테스트는 reporter 소유 fixture의 임시 복사본만 사용한다. 대소문자·공백만 다른 GT Resource가 별개 평가 대상으로 유지되는지 실제 결과 건수로 확인하고, 출력 Schema·GT 참조 해시·입력 파일 불변성을 검증했다. 이는 독립·회귀 검증이며 전체 파이프라인 연결 완료를 뜻하지 않는다.

다음은 6단계: 담당 모듈과 생산자 공개 산출물의 최종 회귀 검증 및 남은 계약 불일치 점검이다.

건너뛴 4개는 `KG_RUN_NEO4J_INTEGRATION=1` 설정이 필요한 실제 Neo4j 통합 테스트다. 이번 reporter 수정 검증에서는 해당 테스트를 실행하지 않았다.

### 2026-10-09 — 4단계: 원본 계정·역할 ID 참조 검증 수정

기준 커밋 `a30f32b`에서 reporter가 원본 `account_id`, `role_id`를 KG `node_id`와 동일하게 검사해 정상 semantic 출력과 Workflow를 거절하는 문제를 수정했다.

변경 내용:

- `models.py`에 검증용 `GraphReferenceIndex`를 추가하고, `input_validation.py`에서 노드 ID·원본 계정 ID·원본 역할 ID 인덱스를 분리했다.
- `normalized_requests.account_id`, `role_id`와 `workflow.role_ids`는 노드의 공개 원본 ID 속성으로 연결한다. semantic 입력과 KG snapshot의 공통 검증에 적용했다.
- Endpoint·Resource는 실제 노드 ID와 대상 종류를 각각 검사하고, 관계는 실제 노드 ID로 검사한다. 같은 ID가 여러 참조 필드에 나타나도 다른 필드의 검사를 덮어쓰지 않는다.
- 원본 ID 속성의 누락·잘못된 타입·빈 값·중복 매핑을 거절한다. 노드 ID 접두사나 문자열 일치로 원본 ID를 추측하지 않는다.
- reporter 소유 semantic/KG fixture를 User·Role 노드의 공개 원본 ID 속성과 별도 노드 ID를 갖는 구조로 갱신했다. 요청·Workflow·AccessRow의 원본 ID는 그대로 유지했다.
- reporter 소유 verification fixture의 관계 `source_id`도 실제 User 노드 ID로 맞추고, 이를 참조하는 리포트 fixture 6개의 SHA-256을 재계산했다.

입력·출력 Schema와 버전은 변경하지 않았다. 다른 모듈, `matching.py`, 진단 분류·평가 계산·HTML 렌더링·CLI도 변경하지 않았다. `IMPLEMENTATION.md`는 만들지 않고 README에 이력을 유지한다.

검증 결과:

```text
test_graph_references.py
74 passed

reporter 전체
290 passed

knowledge_graph + access_analyzer + safety_policy + reporter
675 passed, 4 skipped
```

회귀 테스트는 생산자 형태의 노드 ID·임의 노드 ID·원본 ID와 동일한 노드 ID를 모두 검증한다. 원본 ID/노드 ID 혼용, 잘못된 노드 종류, 누락·중복 원본 ID, 없는 관계 대상, Workflow 순서·의존 단계 오류를 거절하고 collector와의 계정 일치 검사를 유지한다.

공개 `evaluate` 실행에서 원본 요청과 Workflow가 변경되지 않고 실제 입력 해시를 참조하는 출력이 생성되는지 확인했다. 원본 ID 매핑이 잘못되면 `CONTRACT_INVALID` 제어 응답을 반환하고 출력 파일을 만들지 않는 것도 검증했다. 이는 reporter 독립 테스트이며 전체 파이프라인 연결 완료를 뜻하지 않는다.

다음은 5단계: Resource 식별값 비교에서 대소문자·공백 원문을 보존하는 수정이다. 이번 단계에서 Resource 매칭 방식은 변경하지 않았다.

### 2026-10-09 — 3단계: reporter 입출력 계약 0.2 동기화

기준 커밋 `986e9d7`과 팀의 0.2 전환 방향을 기준으로 reporter 내부의 계약 사본·출력 버전·독립 테스트를 갱신했다. 다른 모듈이나 `docs/spec/`은 수정하지 않았다.

변경한 입력 Schema:

```text
crawl_result
semantic_analysis
vulnerability_candidates
test_scenarios
safety_decisions
verification_results
```

각 Schema는 생산자의 공개 출력 Schema를 reporter 내부 사본으로 동기화했다. `test_scenarios`는 현재 생산자 Schema 구조를 유지하되 합의된 목표 버전인 `0.2.0`을 적용했다. 다른 모듈의 Schema를 직접 참조하거나 구현 코드를 import하지 않는다.

주요 계약 제약:

- `semantic_analysis`의 Resource는 `resource_key`, `resource_scope`, `match_key`를 필수로 갖는다. Type은 `match_key=null`, Instance는 식별값이 있는 `match_key`를 요구한다.
- 각 취약점 후보의 `resource_ids`, `source_request_ids`는 최소 1개를 요구한다. 후보 배열 자체가 비어 있는 정상 결과는 계속 허용한다.
- `verification_results.graph_updates`는 Resource 생성, 미검증 관계, `basis`가 verified가 아닌 갱신, 근거 없는 갱신을 거절한다. 관계는 `VERIFIED_ACCESS`, `VERIFIED_DENIAL`만 허용한다.
- collector의 민감 파라미터·마스킹 헤더 제약, 근거 SHA-256 형식 및 상태별 데이터·오류 제약을 생산자 공개 Schema와 맞췄다.

`diagnosis_report`, `evaluation_results`는 출력 Schema와 출력 adapter 모두 `0.2.0`을 사용한다. 기존 진단 상태 분류, 평가 계산, HTML 렌더링, CLI 인터페이스는 변경하지 않았다.

reporter 소유 fixture의 실행 산출물 버전과 Resource Instance 속성을 갱신하고, 변경된 시나리오 바이트를 기준으로 safety/verifier의 `scenarios_sha256`을 재계산했다. 리포트 fixture의 `input_refs`와 평가용 정답 참조도 실제 fixture 파일 해시로 갱신했다. 실제 실행 산출물은 수정하지 않았다.

변경하지 않은 계약은 기존 `graph_query_result` 0.2 Schema·fixture와 `ground_truth` 0.1 Schema·fixture다.

검증 결과:

```text
reporter 전체
216 passed

test_contracts.py + test_contract_migration.py
93 passed

test_evaluation_service.py
21 passed

knowledge_graph + access_analyzer + safety_policy + reporter
601 passed, 4 skipped
```

회귀 테스트는 지원/미지원 버전, Resource 범위와 match_key, 후보 참조 배열, 검증 graph_updates 제약, 민감값 마스킹, fixture 해시, 공개 run/CLI 출력 버전·참조 무결성을 확인한다. completed/partial/failed 리포트와 실패한 평가 출력도 0.2 Schema를 검증한다.

생산자 Schema 6종은 입력용 title과 `test_scenarios` 목표 버전을 제외하면 사본과 일치한다. collector/access_analyzer/safety_policy/verifier의 공개 출력 fixture 4종은 reporter 입력 Schema를 통과했다. semantic_analyzer에는 해당 경로의 공개 출력 fixture가 없어 Schema 비교만 수행했다. scenario_generator의 기존 0.1 fixture는 버전 불일치로 거절됨을 확인했다. 이 확인은 Schema 검증이며 모듈 간 전체 실행 검증은 아니다.

다음 단계에 남긴 reporter 내부 작업:

```text
4단계: account_id/role_id와 KG node_id의 참조 검증 구분
5단계: Resource 식별값 비교에서 대소문자·공백 원문 보존
6단계: 담당 모듈과 생산자 산출물의 최종 회귀 검증
```

이번 단계에서는 `input_validation.py`, `matching.py`를 변경하지 않았다. 0.2 입력 Schema를 통과하더라도 원본 계정·역할 ID와 KG node_id의 구분에 관한 후속 수정은 필요하다.

### 2026-10-08 — KG Resource Type/Instance 0.2 계약 동기화

knowledge_graph의 `graph_query_result.json`이 Resource Type/Instance 모델을 포함하는 `schema_version=0.2.0`으로 변경됨에 따라 reporter의 development 평가 입력 계약을 동기화했다.

변경 내용:

```text
graph_query_result
0.1.0 → 0.2.0
```

Resource 구조:

```text
resource_key
resource_scope: type | instance
match_key
```

`role_resource_access`의 AccessRow에 다음 Resource 메타데이터를 반영했다.

```text
resource_id
resource_key
resource_scope
match_key
request_ids
```

Resource가 없는 AccessRow는 다음 형태를 허용한다.

```text
resource_id=null
resource_key=null
resource_scope=null
match_key=null
```

Resource Instance는 유효한 `match_key`를 요구하고 Resource Type은 `match_key=null`을 사용한다.

reporter의 KG fixture 역시 `graph_query_result 0.2.0`과 Resource Instance 구조로 갱신했다.

평가 로직에서는 Ground Truth를 KG 내부 표현에 직접 종속시키지 않도록 KG 0.2 Resource를 평가 시점에만 정규화한다.

호환되는 Ground Truth 표현 예:

```text
resource_type=order
external_id=order-b
```

또는:

```text
name=order
```

이들은 KG 0.2의 `resource_key` 및 `match_key.identifiers`와 의미 기준으로 비교한다.

검증 결과:

```text
test_contracts.py
38 passed

test_evaluation_service.py
19 passed

reporter 전체
141 passed
```

이번 변경은 일반 `report` operation의 진단 결과 생성 흐름을 변경하지 않는다.

주요 영향 범위는 development 모드의 `evaluate`가 최신 Knowledge Graph 출력을 정상적으로 소비하고 Resource를 Ground Truth와 정확하게 비교할 수 있도록 하는 것이다.

### 2026-10-07 — collector Page.account_id 계약 동기화

collector의 `crawl_result.json`에서 `Page.account_id`가 필수 필드로 추가됨에 따라 reporter의 `crawl_result` 입력 계약과 fixture를 최신 상태로 동기화했다.

변경 내용:

```text
Page.account_id
- required
- type: string
```

reporter의 collector fixture에도 `account_id`를 반영하고, 해당 필드가 누락된 `Page`를 입력 계약에서 거절하는 테스트를 추가했다.

검증 결과:

```text
test_contracts.py
24 passed

reporter 전체
124 passed
```

이번 변경은 reporter의 진단·평가 로직을 변경하지 않고 collector 입력 계약만 최신 상태로 맞춘다.

### 2026-10-07 — KG AccessRow 계약 동기화

knowledge_graph의 `role_resource_access` 출력에 원본 요청 추적을 위한 `request_ids`가 추가됨에 따라 reporter의 `graph_query_result` 입력 계약을 동기화했다.

변경 내용:

```text
AccessRow.request_ids
- required
- minItems: 1
- uniqueItems: true
- non-empty string
```

실제 `role_resource_access` 결과를 포함하는 reporter fixture를 추가해 최신 KG 출력이 reporter 계약을 통과하는지 확인했다.

검증 결과:

```text
test_contracts.py
23 passed

reporter 전체
123 passed
```

이번 변경은 reporter의 진단·평가 로직을 변경하지 않고 KG 입력 계약만 최신 상태로 맞춘다.

### 2026-10-09 — 계정 source 전환 3단계: 검증 입력 계약 동기화

기준 커밋 `5fe4057`의 KG 계정 조회·관계 저장 구현에 맞춰 reporter의
검증 결과 입력 사본을 동기화했다. 사용자 합의대로 `schema_version=0.2.0`을
유지하며, 같은 버전의 이전 검증 관계와는 호환되지 않는다.

#### 입력 경계와 유지한 처리

```text
verification_results.graph_updates.relationships
    source_account_id: 원본 실행 계정 ID
    target_id: 기존 Resource instance node_id
          ↓
reporter 입력 Schema·artifact/해시·근거 검증
          ↓
results를 후보·시나리오·Safety 판정과 연결
          ↓
기존 진단 분류·리포트 생성 / development 평가
```

- 입력 Schema의 검증 관계 필수 필드와 속성 정의를
  `source_id`에서 `source_account_id`로 변경했다. 이전 필드·동시 입력·누락은
  `CONTRACT_INVALID`로 실패하고 정상 빈 결과나 리포트로 숨기지 않는다.
- 일반 semantic 관계와 KG `structure_snapshot`의 node_id 기반 `source_id`는
  변경하지 않았다. Reporter는 검증 관계의 계정을 KG User로 해석하거나,
  DB 존재 여부를 조회하거나, 검증 관계를 저장하지 않는다.
- `VERIFIED_ACCESS`·`VERIFIED_DENIAL`, `basis=verified`, 실행 근거,
  신규 Resource 생성 금지와 기존 검증 상태·Policy·revision·계획 해시 검사는 유지했다.
- 진단 분류, 개발 평가, 내부 모델, 공개 operation·CLI 및 출력 Schema는
  변경하지 않았다. 운영 코드 변경 없이 입력 사본·fixture·테스트만 동기화했다.

#### Fixture와 SHA-256

Reporter 소유 verification fixture의 source를 `user:account_user_a`에서
원본 계정 ID인 `account_user_a`로 변경했다. 이 값은 fixture의 User 공개 속성과
대응하며, KG node_id를 원본 계정 ID로 간주하지 않는다.

변경된 verification 파일 바이트의 실제 SHA-256을 계산해 리포트 fixture
6개(completed 진단·평가 2개, partial/failed 진단·평가 4개)의 `input_refs`를
갱신했다. 후보·시나리오·Safety·semantic·KG fixture는 수정하지 않았다.
시나리오 파일이 바뀌지 않았으므로 `scenarios_sha256`도 유지했다.
테스트용 검증 User 생성 helper는 account_id와 별도의 node_id를 사용하도록
수정했으며, 계정 ID로 node_id를 추측하는 방식을 넣지 않았다.

#### 검증과 다음 작업

```text
신규 계정 source 계약·공개 실행 테스트
34 passed

신규 계정 source + 기존 0.2 계약 이행 테스트
89 passed

reporter 전체
390 passed

knowledge_graph + safety_policy + reporter 기본 회귀
778 passed, 4 skipped
```

신규/이전/동시/누락 source, 잘못된 계정 값·타입, 원본 계정 ID 보존,
target 필수 제약, partial/failed envelope, 일반 그래프 관계의 node_id 계약을
확인했다. `report`·`evaluate` 공개 실행에서도 이전 입력 거절·출력 미생성,
계정 source 수신·입력 불변·실제 출력 참조 해시, 검증 파일 해시 불일치 차단을
검증했다. 기존 회귀 테스트로 판정·미검증 상태·근거·평가 지표 동작을 확인했다.

이번 단계의 변경은 `modules/reporter/` 내부 11개 파일뿐이다.
다른 모듈·공용 명세·의존성·실제 실행 결과는 수정하지 않았다.
Verifier 출력의 필드 전환은 생산자 담당 작업으로 남기며, 입력 사본의 전환과
전체 pipeline 연결 완료를 구분한다.

skip 4건은 KG 실제 Neo4j 통합 테스트다. 다음 4단계에서 새로운 계정 source의
실제 DB 변환·저장·revision·중복 반영·snapshot·실패 시 rollback을 확인한다.

### 2026-10-09 — 계정 source 전환 5단계: 소비자 완료 범위·생산자 전달

기준 커밋 `b2aec96`에서 KG·Reporter의 소비자 측 변경을 최종 정리했다.
이번 단계는 두 모듈 README만 변경하며 기존 이력은 보존한다.
이전 이력의 테스트 건수와 다음 작업은 기록 당시 기준이며, 아래는 최신 완료 범위다.

#### Reporter 완료 범위

- 입력 사본·fixture·참조 해시·독립 테스트 전환은 `3143752`에 반영했다.
  `schema_version=0.2.0`을 유지하며 검증 관계에 `source_account_id`를 필수로 받는다.
- source는 실제 접근 계정의 원본 ID이며, target은 기존 Resource instance node_id다.
  이전 필드·동시 입력·누락은 거절하고 입력을 묵시적으로 변환하지 않는다.
- Reporter는 해당 검증 관계의 계정→User 변환·DB 존재 확인·관계 저장을 하지 않는다.
  자기 Schema·해시·ID·근거·Policy·계획·revision 검증과 기존 진단 분류는 유지한다.
- `evaluate`의 semantic/snapshot에 대한 `GraphReferenceIndex`와 원본 ID 참조
  검증은 별도 책임이다. 검증 관계를 DB에 반영하지 않는다는 원칙과 충돌하지 않는다.
- 일반 그래프·snapshot의 node_id 기반 `source_id`, 리포트·평가 출력 계약,
  공개 operation·CLI·HTML 생성, 미실행·판단불가의 분류 방식은 변경하지 않았다.

#### 생산자 전달과 실제 수신 확인

verifier 담당자는 검증 관계 출력 Schema·직렬화·소유 fixture·테스트를 새 필드에
맞춘다. B가 A 자원에 접근했으면 source는 B의 원본 account_id이며 소유자 A나
User node_id로 대신하지 않는다. 버전은 `0.2.0`을 유지하고 모든 직접 소비자와
같은 계약을 적용한다. 기존 입력 구성은 유지하며 Reporter용 별도 결과 파일을 만들지 않는다.

기준 커밋에서 verifier 출력 Schema는 아직 `source_id`를 요구한다.
빈 graph update의 수신이나 Reporter 소유 fixture 통과만으로 생산자 전환·실제
pipeline 연결이 완료됐다고 판단하지 않는다. 공용 명세 반영도 담당자·관리자 작업이다.
이번 단계는 전달 내용을 준비한 것이며 팀원에게 외부 메시지를 발송하지 않는다.

실제 수신 확인 항목은 다음과 같다.

- [ ] 생산자가 새 검증 관계 필드로 생성한 동일 run의 실제 결과 파일과 정확한 SHA-256 확보
- [ ] 기존 후보·계획·Safety 판정과 ID·revision·계획 해시·실행 근거 대응 확인
- [ ] `report`에서 새 필드 수신, 입력 불변과 출력 `input_refs` 해시, 미검증 상태 보존 확인
- [ ] 이전 검증 관계 필드 입력은 `CONTRACT_INVALID`이며 리포트 파일이 생성되지 않는지 확인
- [ ] 필요 시 development 평가 입력·정답·실제 snapshot을 확보해 `evaluate` 확인

KG의 계정 조회·target·DB 반영·revision·재반영 확인 항목은
[KG README](../knowledge_graph/README.md)의 계정 source 전환 5단계 체크리스트를 따른다.
검증 파일을 바꿨으면 새 artifact와 정확한 참조 해시로 전달하며, 완료 파일을
덮어쓰거나 node_id 값을 새 필드명으로 단순 변경하지 않는다.

#### 검증 기록과 후속 작업

3단계 Reporter 독립 검증은 390건 통과했다. 4단계 `b2aec96`의 담당 모듈
회귀는 기본 778 passed·12 skipped, 실제 Neo4j 활성화 시 790 passed였다.
실제 DB 결과는 KG 통합 검증이며 Reporter가 Neo4j에 직접 연결했다는 의미가 아니다.
이번 단계는 문서 정리이고 실제 Neo4j를 다시 실행하지 않는다.

5단계 문서 수정 후 담당 3개 모듈의 기본 회귀를 재실행해
`778 passed, 12 skipped`를 확인했다. skip 12건은 이번 단계에서 활성화하지 않은
KG 실제 Neo4j 통합 테스트다. `git diff --check`도 통과했다.

소비자 측 계정 source 전환의 구현·독립 검증·문서 정리는 마무리한다.
생산자의 출력 전환과 실제 동일 run의 산출물 수신, 전체 pipeline 연결 검증은
후속 작업으로 남긴다. 기존에 기록한 다른 upstream 계약·Ground Truth 관련
미해결 항목도 이번 문서 작업으로 해결됐다고 간주하지 않는다.

### 2026-10-10 — Scenario.resource_ids 소비자 동기화와 합동 회귀

명세 `docs/spec/m5-scenario_generator.md`의 합의된 소비자 계약을
`645efaf`에 반영했다. Safety Policy 대응은 `91425cd`이며 두 변경 이후 합동 회귀를 확인했다.

- 시나리오 입력 Schema에 필수 `resource_ids`를 추가하고 버전 `0.2.0`을 유지했다.
  누락·`null`·빈 배열·빈 문자열·잘못된 항목 타입과 미정의 키는 거절한다.
- 시나리오 fixture에는 대응 후보의 자원 ID를 값·순서 그대로 복사했다.
  Safety·Verifier fixture의 계획 해시, 리포트/평가 fixture 6개(completed 2개,
  partial/failed 4개)의 입력 참조 해시를 실제 파일 바이트로 갱신했다.
  갱신한 fixture는 총 9개이며 다른 모듈이나 실제 산출물은 수정하지 않았다.
- 테스트 28건을 추가했다. 불투명 ID·순서·중복 값 수용, report/evaluate의
  원본 입력 보존, 정상/partial 결과 분류 유지, 잘못된 입력의 출력 미생성을
  확인했다. 자원 ID가 바뀌면 Safety 또는 Verifier의 이전 계획 해시를 거절한다.
- 운영 Python 코드·내부 모델·결과 분류·평가 방식·공개 출력 계약·의존성은
  변경하지 않았다. 검증 관계의 계정→User 해석·DB 반영은 계속 KG 책임이다.

검증 명령과 결과:

```bash
.venv/bin/python -m pytest modules/reporter -q
# 418 passed

.venv/bin/python -m pytest modules/reporter/tests/test_contracts.py modules/reporter/tests/test_contract_migration.py -q
# 107 passed

.venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter -q -rs
# 826 passed, 12 skipped
```

SG 공개 시나리오 2개, Safety 판정 2개, KG 질의 결과 4개의 Reporter 입력 Schema
검증을 통과했다. 리포트 fixture 6개의 `input_refs` 24개와 Safety·Verifier의
계획 해시가 실제 파일과 일치한다. Python 32개 파일의 다른 모듈 import와
Schema 10개의 외부 `$ref`는 각각 0건이다. `git diff --check`도 통과했다.
skip 12건은 KG 실제 Neo4j 테스트이며 이번 단계에서 DB는 실행하지 않았다.

소비자 측 호환 수정·독립 검증·README 정리는 완료한다. Verifier의 입력 0.2
대응 및 검증 관계 출력 전환, 동일 run 실제 산출물 수신과 전체 pipeline 연결은
후속 작업이다. Ground Truth의 기존 0.1 계약과 별도 미해결 항목도 유지한다.

### 2026-10-10 — PR #52·#53 병합 후 현재 상태·명세 동기화

기준 커밋 `695b3c8`에서 현재 입력 계약·연결 상태 요약과
`docs/spec/m8-reporter.md`를 갱신했다. 이전 단계의 테스트·미전환 안내는 당시
기록으로 보존하고 현재 상태와 구분한다.

- Verifier 입력 3종 `0.2.0` 및 출력 `source_account_id` 전환 완료를 반영했다.
  Reporter 입력 사본은 최신 생산자 Schema를 수용하며 계정→User 변환은 계속 KG 책임이다.
- 실행 입력 7종·출력 2종의 버전, Resource Type/Instance 개발 평가 대응,
  HTML 구현 범위와 미검증 후보 보존을 명세에 기록했다.
- Ground Truth는 기존 `0.1.0`을 유지한다. 공통 명세의 11개 파일 0.2 안내와
  정답 파일 0.1 표의 불일치는 합의가 필요한 항목으로 남긴다.

```bash
.venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter modules/verifier -q -rs
# 897 passed, 12 skipped
```

skip 12건은 이번에 활성화하지 않은 KG 실제 Neo4j 테스트다. Verifier 공개 결과
2개는 입력 Schema를 통과하지만 검증 관계는 0개다. 실제 관계 생성 후 같은 run의
리포트·평가 연결을 별도로 검증해야 한다. 이번 변경은 문서만 갱신하며
코드·Schema·fixture·테스트·의존성을 변경하지 않는다.

### 2026-10-14 — 실행 산출물 0.3.0 및 Collector 입력·Endpoint 발견율 보완

팀 합의에 따라 실행 입력 7종·출력 2종의 Schema와 출력 adapter·정상/부분/실패
fixture를 `0.3.0`으로 맞췄다. Ground Truth 계약·fixture는 기존 `0.1.0`으로 유지한다.
구버전 0.1·0.2 및 미지원 0.4 거절, 계획 해시·리포트 input_refs 실제 바이트 일치를 확인했다.
같은 작업의 마지막에 m8 현재 상태·계약·변경 이력을 갱신했다.

- Collector 독립 입력 사본에 discovered_endpoints, response.identifiers와
  identifiers_truncated를 반영했다. 필수 필드·허용 식별값·JSON 타입·null/빈 배열을 검증한다.
- endpoint_recall은 Endpoint 노드(추론 포함)와 normalized_requests를 함께 사용한다.
  중복 노드·요청은 분자를 늘리지 않고 매칭 키 누락·불일치는 제외한다.
- Endpoint 발견을 실제 접근·검증 성공으로 바꾸지 않는다. Parameter 출처·후보/확정
  TP/FP/FN·GT 분모·default-v1·Resource 원문 식별값 비교·리포트 상태 분류는 유지한다.

```bash
.venv/bin/python -m pytest modules/reporter -q --tb=short
# 462 passed

.venv/bin/python -m pytest modules/reporter/tests/test_contracts.py modules/reporter/tests/test_contract_migration.py modules/reporter/tests/test_crawl_0_3_contract.py -q --tb=short
# 132 passed

.venv/bin/python -m pytest modules/reporter/tests/test_evaluation_service.py -q --tb=short
# 32 passed
```

Collector·KG·Safety Policy 출력과 Reporter 입력 사본은 최상위 설명 메타데이터를
제외하면 일치한다. 외부 생산자·공통 명세·Web 버전 수용은 각 담당자가 전환한다.
새 버전의 실제 진단·성능 향상·전체 pipeline·Docker 검증은 아직 수행하지 않았다.

### 2026-10-14 — PR #81 추론 Parameter 발견율 보완

Endpoint→USE→Parameter를 따라 method·path_template·name·location을 구성하고
parameter_recall에 포함하도록 evaluation_service를 보완했다. 기존 normalized_requests
평가를 유지하며 입력과 snapshot을 수정하거나 요청·ACCESS·후보를 만들지 않는다.

평가 회귀 24건으로 observed/inferred·매칭 키·잘못된 연결·누락/타입·중복·민감
Parameter 메타데이터·실패 입력의 null·다른 지표/진단 집계 유지·공개 evaluate 저장을
검증했다. 수정 전 누락 재현은 5 failed·51 passed였고 수정 후 모두 통과했다.
공개 Schema·default-v1·정답·판정 의미·의존성은 유지하며 마지막에 m8을 갱신했다.

```bash
.venv/bin/python -m pytest modules/reporter/tests/test_evaluation_service.py -q --tb=short
# 56 passed

.venv/bin/python -m pytest modules/reporter -q --tb=short
# 486 passed

KG_RUN_NEO4J_INTEGRATION=0 .venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter -q --tb=short
# 1051 passed, 12 skipped
```

실제 Neo4j 12건과 새 pipeline·Docker·실모델 진단은 미실행이다.
semantic_analyzer 출력 등 외부 계약의 0.3 전환은 해당 담당자의 후속 작업이다.

### 2026-10-14 — 실행별 계정 매핑 분리·benchmark Precision 수정

기준 `913a0df`에서 Reporter development 평가만 보완했다. GT 입력·원본 dataset을
변경하지 않고 `evaluation_config.py`와 선택적 환경변수로 dataset 범위의 actor binding을
받는다. Candidate·User·관계에 같은 원본 account_id 비교를 적용하고 기존 alias fallback을
유지한다. 설정 누락·오류·중복·경로 이탈·해시 변경을 정상 입력으로 숨기지 않는다.

FP를 전체 prediction−양성 일대일 TP로 수정했다. 양성 우선 최대 매칭과 별도 음성
진단을 유지하며 후보·확정의 0/null·중복 예측을 검증했다. 기존 fixture의 후보 3개는
TP=1·명시적 음성=1·미매칭=1이므로 FP 기대값을 1에서 2로 수정했다.
기존 테스트를 삭제하지 않고 계정/평가 회귀 47건을 추가했다.

```bash
.venv/bin/python -m pytest modules/reporter -q --tb=short
# 533 passed

.venv/bin/python -m pytest modules/reporter/tests/test_evaluation_actor_bindings.py modules/reporter/tests/test_evaluation_scoring.py -q --tb=short
# 47 passed

KG_RUN_NEO4J_INTEGRATION=0 .venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter -q --tb=short
# 1098 passed, 12 skipped
```

수정 전 scoring 회귀는 4 failed·2 passed였고 수정 후 통과했다. 공개 evaluate 저장에서
기존 출력 Schema·GT/실행 입력 해시·설정 해시·원자 저장 검증을 확인했다. 공개 run()/CLI,
report/HTML·진단 분류, 실행 산출물 0.3.0·GT 0.1.0·default-v1·의존성은 유지한다.
마지막에 m8 현재 상태·계산식·설정·변경 이력을 갱신했다. 새 pipeline·Docker·실제 진단
성능은 미검증이며 Resource canonicalization·non-JSON assertion·Policy 판단은 수정하지 않았다.

### 2026-10-15 — snapshot 관계 비교·Web 평가 설정 전달 보완

`cb9dc8e` 기준 Page.path↔KG Page.url과 Parameter의 Endpoint 문맥 누락을 수정했다.
비교용 속성만 구성하고 snapshot·GT·node_id·실제 관계는 보존한다. 공유 Parameter의
Endpoint 혼동, 잘못된 방향/종류, 필드 누락, 중복 관계, legacy 키, 비변이 및 공개 evaluate
Schema·해시·저장 회귀 25건을 추가했다. 기존 actor binding·TP/FP/FN 계산을 재사용한다.
관계 탈락 정보는 기존 notes에 추가했으며 출력 필드·버전·report 분류를 바꾸지 않았다.

local_web Compose/.env.example에 선택적 `REPORTER_EVALUATION_CONFIG_PATH` 연결을
추가했다. Web은 비어 있는 설정만 자식 환경에서 제거하고 잘못된 비어 있지 않은 설정을
숨기지 않는다. 실제 자식 프로세스로 미설정·빈 값·명시 경로·공백·없는 경로 전달 5건을 검증했다.

```bash
.venv/bin/python -m pytest modules/reporter -q --tb=short
# 558 passed
.venv/bin/python -m pytest modules/reporter local_web/tests -q --tb=short
# 844 passed, 18 skipped (Chromium·읽기 전용 마운트 선택 테스트)
KG_RUN_NEO4J_INTEGRATION=0 .venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter -q --tb=short -rs
# 1123 passed, 12 skipped (실제 Neo4j)
.venv/bin/python -m pytest tests/pipeline -q --tb=short
# 58 passed (Runner 대역)
```

수정 전 회귀는 13 failed·60 passed로 문제를 재현했다. Web HTTP 테스트는 샌드박스
소켓 제한을 해제한 실행에서 통과했다. Compose config 검증도 통과했다.

지정 run을 임시 컨테이너에 읽기 전용으로 마운트하고 Reporter의 입력 검증→메모리 평가만
실행했다. 공개 산출물 10개와 GT의 SHA-256 11개 불변을 확인했다. 원본은 발행·덮어쓰기하지 않았다.

| 지표 | 저장된 원본 | 관계 수정만 | 관계 수정 + 명시적 기존 계정 설정 |
| --- | --- | --- | --- |
| User recall | 0/1 | 0/1 | 1/1 |
| Relationship recall | 3/52 | 32/52 | 32/52 |
| Candidate TP/FP/FN | 0/2/5 | 0/2/5 | 1/1/4 |
| Workflow recall | 0/5 | 0/5 | 0/5 |
| Confirmed recall | 0/5 | 0/5 | 0/5 |

계정 설정은 `configs/shop_demo.evaluation.example.json`을 비교 실행에서 명시했고
운영 .env·Docker 서비스는 변경하지 않았다. 관계 미매칭 20개·Flow 이름/순서 불일치·
정책 차단은 남아 있다. 의미 분석의 자원 canonicalization(user/id, product/products,
order/orders), scenario_generator의 non-JSON response_json assertion,
Safety Policy의 block/require_approval은 이번 수정 범위 밖이다. inferred 후보의 report
분류를 confirmed로 올리거나 GT에 맞춰 upstream 오류를 보정하지 않았다.

## 변경 이력 — 2026-10-16 Workflow 비교 프로필 추가

- 기준: `7c0863e` + 이번 작업 트리. 기존 `default-v1`과 CLI 기본값을 유지하고,
  이름을 제외한 순서 부분수열 비교를 `default-v2`로 추가했다.
- `matching.py`: 정렬된 액션열 추출과 단일 workflow 부분수열 비교. 기존 이름+전체
  액션열 signature는 유지한다. `evaluation_service.py`: 프로필별 workflow recall·GT
  중복 키 검사·notes를 구분한다. 다른 지표·report 분류·계정 설정은 변경하지 않는다.
- 신규 회귀 40건: 이름 불일치, 삽입 단계, 순서 역전, 누락, 반복 횟수, order 정렬,
  입력 불변, 분리된 workflow 연결 금지, 중복 GT, 분모 0, v1 보존, 공개 evaluate 출력
  Schema·입력 SHA-256·GroundTruthRef. 작은 액션열 961쌍을 조합 기반 참조식과 대조했다.
- 검증: `.venv/bin/python -m pytest modules/reporter -q --tb=short` → 600 passed.
  `KG_RUN_NEO4J_INTEGRATION=0 .venv/bin/python -m pytest modules/knowledge_graph modules/safety_policy modules/reporter -q --tb=short`
  → 1165 passed·12 skipped. `.venv/bin/python -m pytest local_web/tests/test_evaluation.py local_web/tests/test_runner.py -q --tb=short`
  → 122 passed. 실제 Neo4j·브라우저·저장소 전체 테스트·새 pipeline은 실행하지 않았다.
- 동일 입력 비교: 위 두 run은 모두 v1/v2 0/5로 실제 개선은 없었다. 합성 이름/삽입
  단계 대조에서는 0/1→1/1을 확인했다. 입력 액션 토큰 일치와 실제 탐색 범위가 별도의
  선행 조건이며, 상세 한계와 읽기 전용 비교 절차는 위 Workflow 평가 절에 기록했다.
- 범위: Reporter 코드·신규 테스트·이 README와 마지막 단계의 m8 명세만 변경했다.
  실제 GT·snapshot·완료 산출물·다른 모듈·Web/Runner·환경변수·Docker·의존성·공개
  Schema는 변경하지 않았다. 커밋/푸쉬·PR은 별도 요청 후 진행한다.

### 2026-10-16 — Workflow 부분 점수 1~6단계 완료

- 기준: `695e36b` + 작업 트리. 앞선 default-v2의 전체 포함률은 유지하고
  workflow_step_recall·workflow_ordered_step_recall을 기존 Metric 배열과 notes에 추가했다.
  default-v1·CLI 기본값·비Workflow 계산·report 분류·계정 설정·Schema/버전은 유지한다.
- A안: GT마다 LCS→구성 일치 수→원문 workflow_id 순으로 관찰 하나를 선택한다.
  두 점수는 같은 흐름에서 구하고 반복 횟수·전체 GT 단계 분모·0/null·실패 구분을 보존한다.
  순수 함수는 frozen WorkflowMatch와 단일 행 LCS DP를 사용하며 외부 호출/입력 변경이 없다.
- 신규 회귀는 앞선 40건에 순수 계산 43건·평가 연결 16건·통합 37건을 더해 총 136건이다.
  Reporter `696 passed`, SA+담당 3개 `1357 passed, 12 skipped`(실제 Neo4j opt-in),
  Web 평가/Runner `122 passed`. 검증 명령은 위 테스트 절과 이전 단계 기록을 따른다.
- 실제 입력 비교: 두 기존 run의 전체 v1/v2 0/5·부분 구성/LCS 0/29다. GT/관찰 토큰
  교집합 0·SA/KG 액션열 동일을 확인했고, 독립 참조 계산과 출력 Schema/무결성을 대조했다.
  비Workflow 12개 지표와 입력/저장 평가 9개 파일/run은 불변이다. 탐지 성능 개선으로 보고하지 않는다.
- 마지막 6단계에서 README와 m8 현재 상태·계산식·한계·이력을 정리했다. 과거 기록을
  보존하며 이번 문서 단계는 기능 코드를 추가 수정하지 않았다. SA 업무흐름 분리/필수
  조건 추론은 선행 요구가 아니고, 같은 어휘의 새 GT·SA 산출물 검증은 후속으로 남긴다.
- 새 진단·평가 재발행·다른 모듈 추가 수정·Web/Runner·실제 설정·Docker·의존성 변경은
  하지 않았다. 커밋/푸쉬·PR·운영 배포는 별도 요청 후 진행한다.
