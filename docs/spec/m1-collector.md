# 웹 정보 수집기 · collector

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

<aside>
🌐

- 구현 폴더: `modules/collector/`.
- 공개 operation: `collect`.
- 담당 범위: 대상 웹 탐색, 역할·계정별 요청·응답 수집, 로그인 세션 관리와 공개 결과 내보내기를 담당한다. 기존 수집기 동작을 유지하고 export adapter에서 파일 계약을 맞춘다.
</aside>

**읽는 순서:** 독립 동작·수정 책임 → 입력 변화 기준 → 입력 → 구현할 일 → 출력 → 완료 기준 → 주의사항. 상세 JSON·중첩 레코드는 접힌 제목에서 확인한다.

## 독립 동작·수정 책임

**이 모듈 담당자의 전체 책임:** 명세에 맞는 입력을 받으면 입력 검증·변환·실제 처리·출력 변환·출력 검증·저장·오류 처리·설정·모듈별 의존성 선언·테스트·동작 확인을 자기 폴더 안에서 끝낸다. 실행·테스트는 팀 공통 Python과 루트 잠금 환경에서 수행한다. 외부 모듈의 내부 코드·공유 도구에 의존하지 않는다.

| 영역 | 변경·작성 책임 | 참조·사용 경계 |
| --- | --- | --- |
| 자기 구현·도구·타입·설정·의존성 선언·테스트 | `modules/collector/**`는 collector 담당 수정 | 파서·검증·저장·해시·경로·adapter도 자기 utils와 schemas에서 구현한다. 다른 모듈 코드를 import하지 않는다. |
| 입력 계약·입력 검증 | collector 담당이 자기 `schemas/input/`과 검증을 관리 | 입력 원본은 읽기 전용. 생산자 출력 계약과 일치시키고 원본 오류는 생산자에게 요청한다. |
| 자기 출력 계약·출력 검증 | collector 담당이 자기 `schemas/output/`·출력·근거·문서를 작성·검증 | 합의한 출력 Schema·필드 의미·ID·근거를 만족한 파일만 완료로 공개한다. |
| 입력·외부 참조 | 대상·계정·실행 범위는 사용자가 이 모듈에 제공한다. collector가 직접 검증하며 세션·원본 관찰 ID를 관리한다. | 명시된 파일·근거·공개 창구만 사용한다. 다른 모듈 내부 구현·전역 가변 상태를 읽지 않는다. |
| 출력 변경·수신자 조율 | 변경 제안·출력 구현은 collector / 입력 대응은 아래 직접 소비자 | 소비자 내부 알고리즘은 알 필요가 없다. 그 파일을 실제 읽는 담당자와만 버전·필드·의미·변경 시점을 합의한다. |
| 세션·브라우저·인증·session_ref | collector가 생성·유효성·갱신·종료와 공개 adapter를 관리 | verifier는 공개 창구를 사용하고 대여 컨텍스트를 반납한다. 세션 창구는 collector가 소유하고 verifier가 직접 사용한다. |
| 기존 수집기 core | 기존 탐색 동작과 구현 경계를 유지 | 이번 독립성 규칙은 export/공개 session adapter와 소유권에 적용한다. |

**모듈 내부 경계:** 입력 adapter → 내부 처리 → 출력 adapter → 자기 출력 검증 → 원자적 저장 → 완료 통지. 내부 고도화가 계약을 유지하면 다른 담당자에게 수정을 요구하지 않는다.

**입력·출력 계약과 수정 책임:** [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · **독립 실행·모듈 폴더 구조:** [실행 제어 · runner와 폴더 구조](03-runner-layout.md)

## 공통 환경에서의 독립 개발

**개발 기준·관리자 초기 설정·환경 설치:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

- 같은 `.python-version`과 루트 `requirements.lock.txt`로 만든 저장소 루트 `.venv`에서 실행·테스트한다. 독립 검증은 자기 입력 fixture·공개 창구 대역으로 수행한다.
- `modules/collector/requirements.txt`에는 실제 사용하는 직접 의존성과 지원 버전을 선언한다. 추가·버전 변경은 GitHub 관리자에게 제안하고 후보 잠금 환경에서 자기 모듈을 검증한다.
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
- [ ]  계약에 허용된 요청 수·페이지·행동·역할별 복수 계정의 변화와 미관찰 값을 내보낼 수 있다.
- [ ]  관찰 ID와 계정·역할·세션 대응을 보존하고 실제 인증값을 공개 JSON에 넣지 않는다.
- [ ]  허용된 빈 배열·null·partial, 잘못된 Schema/버전/ID/참조/해시를 구분한다. 입력 오류를 성공·정상 빈 결과로 숨기지 않는다.
- [ ]  자기 출력의 Schema·작성자·필수 필드·ID/근거 대응을 검증하고 자기 경로에 원자적으로 공개한다. 저장 실패를 완료로 알리지 않는다.
- [ ]  위 입력 변화와 의존성 실패를 자기 모듈 테스트에서 검증한다. 다른 모듈 실제 구현 대신 공개 입력 fixture·인터페이스 대역으로 독립 검증할 수 있다.
- [ ]  내부 최적화 이후에도 자기 입력·출력 계약 검증을 통과한다. 계약 변경 시 직접 소비자와 실제 출력 샘플의 수신 검증을 함께 확인한다.
- 보호된 로그인 세션은 collector 소유의 가변 상태다. verifier는 collector의 공개 세션 창구에서 유효성 확인·대여·반납만 수행한다. 기존 core 탐색은 유지한다.

## 입력

| 입력 파일·정보 | 작성·제공 주체 | 사용할 내용 |
| --- | --- | --- |
| 대상 URL·역할별 테스트 계정 | `사용자 / 모듈 실행 인자` | 대상 웹과 테스트 계정 범위. 실제 로그인 정보는 보호된 입력으로 주입한다. |
| 실행 인자 / 자기 모듈 설정 | `사용자 실행 인자 / 자기 모듈 설정` | run_id·iteration·mode, 대상 범위, 허용 계정과 실행 제한. |

## 구현할 일

1. 역할별 계정으로 웹을 탐색하고 페이지·관찰 행동·요청·응답을 수집한다.
2. 각 요청에 request_id, 실행 account_id·role_id와 해당 session_ref를 연결한다.
3. 관찰 사실을 공개 계약 형식으로 변환하고 인증·비밀값을 제거한다.
4. crawl_result.json을 공개하고, 후속 검증이 세션 참조를 사용할 수 있도록 수집기 소유의 공개 창구를 제공한다.

## 출력

| 작성 파일 | 생성 operation | 출력 변경 협의 대상 — 직접 소비자 |
| --- | --- | --- |
| `crawl_result.json` | `collect` | semantic_analyzer · scenario_generator · verifier · reporter(개발 평가) |

정상·부분 완료 파일의 `data` 필드는 아래와 같다. `status=failed`이면 `data=null`로 기록한다. 공통 메타데이터·상태 규칙은 [공통 파일 형식](02-common-contract.md)을 적용한다.

## 완료 기준

- [ ]  기존 웹 탐색 동작을 유지하면서 export adapter가 crawl_result.json Schema를 만족한다.
- [ ]  각 요청의 request_id·account_id·role_id·session_ref가 서로 대응한다.
- [ ]  세션 공개 창구로 허용 계정의 컨텍스트를 제공하고 실제 쿠키·토큰은 수집기 내부에 보관한다.

## 구현 주의사항

- 세션·브라우저 객체와 실제 쿠키·토큰은 수집기 내부에 둔다. session_ref는 해당 상태를 찾는 불투명 참조값이다.
- 재현·검증기는 자기 adapter로 공개 세션 창구를 사용한다. 허용 계정의 실행 컨텍스트는 합의된 값·불투명 핸들·사용 규약이며 내부 브라우저/세션 객체를 직접 공유한다는 뜻이 아니다. 접근 방식·요청/응답·오류·만료·대여/반납·종료 규약은 확정됐다(`modules/collector/README.md` "세션 공개 창구"). 요점: lease 때 재로그인, send가 그 세션으로 대신 전송(쿠키는 collector 안), `is_valid`는 대상 앱 요청 없이 보유·만료만, 만료는 send 응답 신호로 감지, send에서 허용 origin 2차 검사. JSON의 참조 문자열만으로 세션 객체가 복원되지는 않는다.
- 관찰하지 못한 행동은 actions=[]로, 관찰하지 못한 페이지·행동 연결은 허용된 null로 기록한다. 추측으로 관찰 필드를 채우지 않는다.
- 같은 역할에 여러 계정을 둘 수 있어야 한다. 수평 인가 검증에서는 계정 A/B와 각자의 소유 자원을 구분한다.

## 입력·출력 JSON 필드

### crawl_result.json

- 고정 값: `artifact_type=crawl_result`, `producer=collector`, `schema_version=0.2.0`(전원 동시 0.2.0 전환).
- 예상 Schema 경로: `modules/collector/schemas/output/crawl_result.schema.json`.
- 예상 출력 fixture 경로: `modules/collector/tests/fixtures/runs/run_demo_001/artifacts/iteration-000/collector/crawl_result.json`.

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data.target_url` | `string` | 필수 | 사용자가 지정한 대상 웹 URL. |
| `data.roles` | `array<Role>` | 필수 | 테스트 역할 목록. |
| `data.accounts` | `array<Account>` | 필수 | 계정별 역할·세션 참조 목록. |
| `data.pages` | `array<Page>` | 필수 | 관찰 페이지 목록. |
| `data.actions` | `array<Action>` | 필수 | 관찰 행동 목록. 미관찰이면 빈 배열이며 추측으로 채우지 않는다. |
| `data.requests` | `array<ObservedRequest>` | 필수 | 역할·계정·화면·행동과 연결된 요청 및 응답 메타데이터. |

## 중첩 레코드 필드

출력 배열·객체의 항목마다 아래 필수 필드를 적용한다. `properties`·`match_key` 등 명시된 JSON map은 확장 가능하고 일반 객체는 미정의 키를 거절한다.

### Role

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `role_id` | `string` | 필수 | 역할 ID. 계정 ID와 구분한다. |
| `name` | `string` | 필수 | 역할 표시명. 예: Guest, User, Admin. |

### Account

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `account_id` | `string` | 필수 | 진단 안에서 사용하는 계정 별칭 ID. 실제 로그인 ID·비밀번호를 담지 않는다. |
| `role_id` | `string` | 필수 | 이 계정의 역할 ID. 동일 역할에 여러 계정을 둘 수 있다. |
| `alias` | `string` | 필수 | 사람이 구분할 수 있는 테스트 계정 별칭. |
| `session_ref` | `string / null` | 필수 | 수집기가 소유한 세션의 불투명 참조값. 인증 정보 자체가 아니다. |

### Page

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `page_id` | `string` | 필수 | 관찰 페이지 ID. |
| `account_id` | `string` | 필수 | 이 페이지를 관찰한 계정 ID(0.2.0 추가). |
| `url` | `string` | 필수 | 관찰한 페이지 URL. |
| `title` | `string / null` | 필수 | 페이지 제목. 관찰하지 못하면 null. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 페이지·DOM·스크린샷 근거 참조. |

### Action

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `action_id` | `string` | 필수 | 관찰한 사용자 행동 ID. |
| `page_id` | `string` | 필수 | 행동이 발생한 페이지 ID. |
| `kind` | `enum: navigate, click, submit, input, other` | 필수 | 관찰 행동 종류. |
| `label` | `string / null` | 필수 | 버튼·폼·행동 라벨. 관찰하지 못하면 null. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 행동의 관찰 근거 참조. |

### ObservedParameter

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `name` | `string` | 필수 | 관찰한 파라미터 이름. |
| `location` | `enum: path, query, body, header, cookie` | 필수 | 파라미터 위치. |
| `value` | `JsonValue` | 필수 | 공유 가능한 관찰값. 인증·비밀값이면 null. |
| `is_sensitive` | `boolean` | 필수 | 비밀값인지 여부. true면 value=null이어야 한다. |

### HeaderMeta

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `name` | `string` | 필수 | 소문자로 기록한 헤더 이름. |
| `value` | `string / null` | 필수 | 공유 가능한 값. 인증·쿠키·토큰은 null 또는 [REDACTED]. |
| `redacted` | `boolean` | 필수 | 비밀값 제거 여부. |

### ResponseMeta

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `status_code` | `integer / null` | 필수 | HTTP 상태 코드. 응답을 받지 못하면 null. |
| `content_type` | `string / null` | 필수 | 응답 Content-Type. 알 수 없으면 null. |
| `headers` | `array<HeaderMeta>` | 필수 | 비밀값을 제거한 응답 헤더 메타데이터. |
| `body_ref` | `EvidenceRef / null` | 필수 | 별도로 보존한 응답 본문 참조. |

### ObservedRequest

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `request_id` | `string` | 필수 | 수집 요청 ID. 이후 모듈에서 재사용하는 연결 키. |
| `account_id` | `string` | 필수 | 요청을 실행한 계정 ID. |
| `role_id` | `string` | 필수 | 요청 실행 역할 ID. |
| `session_ref` | `string / null` | 필수 | 수집기가 관리한 실행 세션 참조. |
| `page_id` | `string / null` | 필수 | 호출을 유발한 페이지 ID. 연결을 관찰하지 못하면 null. |
| `action_id` | `string / null` | 필수 | 호출을 유발한 행동 ID. 연결을 관찰하지 못하면 null. |
| `method` | `string` | 필수 | 대문자 HTTP Method. |
| `url` | `string` | 필수 | 실제 관찰한 요청 URL. |
| `observed_at` | `string (date-time)` | 필수 | 관찰 시각. UTC RFC3339. |
| `parameters` | `array<ObservedParameter>` | 필수 | 관찰 파라미터 목록. |
| `headers` | `array<HeaderMeta>` | 필수 | 비밀값을 제거한 요청 헤더 메타데이터. |
| `body_ref` | `EvidenceRef / null` | 필수 | 별도로 보존한 요청 본문 참조. |
| `response` | `ResponseMeta` | 필수 | 응답 메타데이터. |
| `evidence_refs` | `array<EvidenceRef>` | 필수 | 이 요청의 관찰 근거 참조. |

**재사용하는 계약 필드:** [ArtifactRef](02-common-contract.md), [ErrorItem](02-common-contract.md), [EvidenceRef](02-common-contract.md), [RuntimeMetrics](02-common-contract.md).

## 변경 이력 (0.1.0 → 0.2.0)

출력 파일 `crawl_result.json`과 근거 파일(`response_evidence`·`dom_evidence`)의 `schema_version`을 0.2.0으로 올렸다(전원 동시 전환). 필드 의미 변경은 아래뿐이고 나머지는 버전 상수만 바뀌었다. 근거 파일을 읽는 모듈은 2단계에서 맞춘다.

- **`Page.account_id` 추가**: 이 페이지를 관찰한 계정 ID(필수). 수평 인가 분석에서 페이지를 계정에 귀속시킨다.
- **자원 식별자 보존**: 소유 관계 관찰을 위해 JSON 응답 근거에 `shape`(키·타입 구조)와 `identifiers`(`[{pointer, value}]`, 키 `id`·`*_id`·`*Id`의 숫자·UUID 값만, 민감 키 제외)를 남기고 `ObservedRequest.response.body_ref`로 연결한다. 응답 원문·HTML은 남기지 않는다.
- **역할당 복수 계정**: 같은 역할에 여러 계정을 둔다(`Account.role_id`). 수평 인가 검증에서 A/B 계정과 각자의 소유 자원을 구분한다.
- **세션 공개 창구 확정**: 규약·만료 신호는 위 구현 주의사항과 `modules/collector/README.md` "세션 공개 창구".

---

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md) · [공통 계약 · 파일 형식과 전달 규칙](02-common-contract.md) · [실행 제어 · runner와 폴더 구조](03-runner-layout.md) ·