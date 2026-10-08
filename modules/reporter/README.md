# reporter

진단 결과를 정리해 `diagnosis_report.json`과 로컬 HTML을 생성하고, development 모드에서 Ground Truth 기반 평가 결과를 생성하는 모듈이다.

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

- 진단 실행 입력 7종: `0.2.0` (`crawl_result`, `semantic_analysis`, `graph_query_result`, `vulnerability_candidates`, `test_scenarios`, `safety_decisions`, `verification_results`)
- reporter 출력 2종: `0.2.0` (`diagnosis_report`, `evaluation_results`)
- development 평가 전용 `ground_truth` 입력: 기존 `0.1.0` 유지
- 입력 Schema: `schemas/input/`
- 출력 Schema: `schemas/output/`
- 실행 산출물: `runs/<run_id>/artifacts/iteration-<NNN>/reporter/`
- HTML 리포트: `runs/<run_id>/reports/`
- Ground Truth: `datasets/<dataset_id>/ground_truth.json`

모든 입력은 JSON Schema, 파일 SHA-256, `run_id`, iteration, revision 및 참조 관계를 검증한 뒤 사용한다.

입력 Schema는 producer의 공개 계약을 reporter 내부에 복제해 관리하며, 다른 모듈 구현 코드를 직접 import하지 않는다.

생산자 계약이 변경된 경우 reporter는 해당 생산자의 정상 출력을 소비할 수 있도록 직접 사용하는 입력 Schema만 동기화한다.

진단 실행 입력의 `0.1.0` 및 미지원 버전은 거절하며, 기존 산출물의 버전을 묵시적으로 변환하지 않는다. `graph_query_result`는 이미 0.2 계약이므로 이번 동기화에서 변경하지 않았다.

현재 `test_scenarios` 입력은 팀의 0.2 전환 방향을 반영한다. 기준 커밋 `986e9d7`의 scenario_generator는 아직 0.1을 출력하므로 생산자 측 전환이 필요하다. 입력 Schema 동기화와 전체 파이프라인 호환 완료는 구분한다.

`ground_truth`는 공통 계약의 11개 파일 0.2 안내와 `m8-reporter.md`의 정답 파일 0.1 표가 일치하지 않는다. 이번 단계에서는 기존 정답 Schema·fixture를 유지하고, 정답 계약 전환은 별도 검토 대상으로 남긴다.

---

## Knowledge Graph 입력

development 평가에서 사용하는 `graph_query_result.json`은 현재 Knowledge Graph의 `schema_version=0.2.0` 계약을 따른다.

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

KG 0.2에서는 Resource 종류와 실제 Resource 인스턴스를 구분한다.

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

다른 query 결과 역시 producer의 `graph_query_result 0.2.0` 계약에 따라 정상적으로 검증할 수 있어야 한다.

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

Ground Truth와 분석 결과는 내부 ID 문자열을 직접 비교하지 않고 의미 정보를 기준으로 연결한다.

주요 비교 정보:

- Page path
- Endpoint method + path template
- Parameter 위치·이름
- Role 이름
- Resource 정규화 키

### KG 0.2 Resource 정규화

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

따라서 기존 Ground Truth의 표현을 유지하면서 최신 KG 0.2 Resource를 평가할 수 있다.

실제 KG artifact 자체를 변환하거나 수정하지 않는다.

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
216 passed
```

계약 테스트:

```bash
.venv/bin/python -m pytest modules/reporter/tests/test_contracts.py modules/reporter/tests/test_contract_migration.py -q
```

현재 결과:

```text
93 passed
```

평가 테스트:

```bash
.venv/bin/python -m pytest modules/reporter/tests/test_evaluation_service.py -q
```

현재 결과:

```text
21 passed
```

추가 실행 의존성 없이 팀 공통 Python 환경과 루트 의존성을 사용한다.

---

## 변경 이력

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
