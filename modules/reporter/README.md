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

- reporter 출력 계약: `0.1.0`
- `graph_query_result` 입력 계약: `0.2.0`
- 그 외 현재 입력 계약: producer 공개 계약에 맞춰 관리
- 입력 Schema: `schemas/input/`
- 출력 Schema: `schemas/output/`
- 실행 산출물: `runs/<run_id>/artifacts/iteration-<NNN>/reporter/`
- HTML 리포트: `runs/<run_id>/reports/`
- Ground Truth: `datasets/<dataset_id>/ground_truth.json`

모든 입력은 JSON Schema, 파일 SHA-256, `run_id`, iteration, revision 및 참조 관계를 검증한 뒤 사용한다.

입력 Schema는 producer의 공개 계약을 reporter 내부에 복제해 관리하며, 다른 모듈 구현 코드를 직접 import하지 않는다.

생산자 계약이 변경된 경우 reporter는 해당 생산자의 정상 출력을 소비할 수 있도록 직접 사용하는 입력 Schema만 동기화한다.

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
python -m pytest modules/reporter/ -q
```

현재 결과:

```text
141 passed
```

계약 테스트:

```bash
python -m pytest modules/reporter/tests/test_contracts.py -q
```

현재 결과:

```text
38 passed
```

평가 테스트:

```bash
python -m pytest modules/reporter/tests/test_evaluation_service.py -q
```

현재 결과:

```text
19 passed
```

추가 실행 의존성 없이 팀 공통 Python 환경과 루트 의존성을 사용한다.

---

## 변경 이력

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