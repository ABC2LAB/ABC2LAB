# 공통 계약 · 파일 형식과 전달 규칙

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md)

<aside>
📐

모든 모듈 담당자가 함께 적용할 읽기·쓰기 계약이다. `ground_truth.json`의 정적 입력 계약은 평가·리포트 페이지에서 확인한다.

</aside>

## 최우선 원칙 — 계약을 지키는 독립 모듈

<aside>
🧩

각 모듈의 최우선 책임은 **공개 입력 계약에 맞는 다양한 데이터를 처리하고, 자기 출력 계약을 지키는 결과를 내보내는 것**이다.

독립성은 계약이 허용하는 값·개수·빈 배열·nullable 값·부분 결과가 달라져도 처리할 수 있다는 뜻이다. 필드·타입·enum·의미·버전의 변경은 아래 계약 변경 절차를 적용한다.

성능 고도화로 결과의 개수·내용·정확도는 달라질 수 있다. 출력 구조와 필드의 의미, ID·근거 연결, 파일 작성 주체는 유지한다.

</aside>

### 모듈 안에서 책임질 처리 순서

1. 입력 adapter에서 JSON Schema·지원 버전·run_id·ID·참조·해시를 검증한다.
2. 공개 입력을 자기 모듈의 내부 모델로 변환한다. 원본 파일과 다른 모듈 객체는 읽기 전용으로 사용한다.
3. service에서 분석·추론·질의·검증 등 자기 작업을 수행한다.
4. 출력 adapter에서 고정된 공개 계약으로 변환하고 Schema와 ID·근거 대응을 다시 검증한다.
5. 자기 결과를 원자적으로 저장하고 실제 저장 바이트의 해시와 완료 상태를 공개 규약으로 반환한다.
- 내부 클래스·알고리즘·프롬프트·캐시·자료구조·배치 처리는 담당자가 개선한다. 다른 모듈의 내부 코드를 import하거나 전역 상태를 직접 읽는 방식에 의존하지 않는다.
- 테스트 예시의 URL·역할명·계정 ID·배열 길이·배열 위치를 하드코딩하지 않는다. 명세의 식별자와 명시된 order로 대응시킨다.
- 필요한 LLM·DB·HTTP 의존성은 자기 설정과 내부 adapter로 다룬다. 의존성 실패도 아래 표에 따라 기록한다.

### 입력이 달라졌을 때의 처리 책임

| 입력 상태 | 받는 모듈의 책임 | 출력·연결 원칙 |
| --- | --- | --- |
| 지원 계약의 정상 입력 | 값·개수·자원·역할 조합이 달라도 명세 의미대로 처리 | 자기 Schema를 만족하는 결과를 생성한다. 결과 값이 항상 같을 필요는 없다. |
| Schema가 허용한 빈 배열·null | 미관찰·근거 없음·처리 대상 없음의 의미를 구분 | 필수 키를 유지한다. 허용되지 않은 빈 값·null은 입력 오류로 처리한다. |
| `status=partial`인 유효 입력 | 유효 data를 처리하고 누락·분석 불가 범위를 출처와 오류로 추적 | 자기 작업 상태를 별도로 판정한다. 일부 입력만으로 전체 범위가 정상이라고 단정하지 않는다. |
| `status=failed`, 잘못된 Schema·지원하지 않는 버전·ID/해시 불일치 | 가짜 기본값으로 정상 입력을 만들지 않고 처리 실패를 명시 | 안전하게 출력 파일을 쓸 수 있으면 기존 `failed / errors / data=null` 계약을 사용한다. 미재현·취약점 없음으로 바꾸지 않는다. |
| LLM·DB·HTTP·세션·개별 항목 처리 실패 | 유효 결과가 있으면 partial, 사용할 결과가 없으면 failed로 기록 | 미확인 항목을 보존한다. Policy 허용 조건이 충족되지 않으면 실행하지 않는다. |
| 출력 경로·저장·프로세스 실패로 파일 생성 불가 | 자기 공개 호출·CLI의 실패 응답·예외 규약으로 호출자에게 알림 | 존재하지 않는 파일을 완료됐다고 통지하지 않는다. 호출자는 실패 사실을 다음 소비자에게 명시한다. 각 소비자는 자기 계약의 실패 처리와 기존 후보 보존을 책임진다. |
- JSON을 만들지 않는 KG ingest·apply_verification은 같은 실패 원칙을 공개 제어 응답에 적용한다. 임의의 새 결과 JSON은 추가하지 않는다.

## 참조 원본과 계약 수정의 책임

### 데이터·문서·코드의 책임 구분

| 대상 | 작성·관리 책임 | 직접 연결 상대의 책임 |
| --- | --- | --- |
| 입력 Schema·파서·검증·입력 adapter | 입력을 받는 모듈 담당 / 자기 schemas/input·utils | 생산자의 출력 계약을 사용한다. 원본 오류는 생산자에게 요청하고 새 산출물로 받는다. |
| 출력 Schema·출력 adapter·저장·검증·근거·출력 계약 문서 | 출력을 만드는 모듈 담당 / 자기 schemas/output·README | 직접 소비자가 자기 입력 Schema와 파서를 관리한다. 합의된 계약을 양쪽 폴더에 같은 내용으로 둔다. |
| 내부 모델·알고리즘·프롬프트·설정·의존성 선언·테스트·동작 확인 | 해당 모듈 담당 | 공개 입력·출력만 의존한다. 내부 소스·utils·Schema 파일을 다른 모듈에서 import/$ref하지 않는다. |
| 실행 JSON·근거 파일 | 아래 표의 단일 작성자 | 완료된 입력은 읽기 전용. 다른 모듈 결과를 직접 정정하지 않는다. |
| 공개 실행 인자·완료/실패 응답·세션/KG 연결 | 해당 공개 창구의 모듈 담당 | 직접 연결 상대와 값·상태·오류·수명 규약을 합의한다. 공통 Python 클래스나 공유 구현을 요구하지 않는다. |
- 이 페이지는 전달 규칙을 기록하는 명세이다. 실제 Schema·검증 코드·내부 타입·테스트는 각 모듈 폴더에서 독립적으로 관리한다.
- 출력 Schema의 기준은 생산자가 공개한 출력 계약이다. 소비자의 입력 Schema는 그 계약을 자기 폴더에서 구현한 사본이며, 임의로 다른 의미를 정의하지 않는다. 계약 변경 시 해당 생산자와 직접 소비자가 함께 수정한다.

### JSON 파일별 단일 작성자와 읽기 주체

| 파일 | 작성·재발행 주체 | 읽기 주체 |
| --- | --- | --- |
| `crawl_result.json` | collector | semantic_analyzer · scenario_generator · verifier · reporter(개발 평가) |
| `semantic_analysis.json` | semantic_analyzer | knowledge_graph · reporter(개발 평가) |
| `graph_query.json` | access_analyzer | knowledge_graph |
| `graph_query_result.json` | knowledge_graph | access_analyzer · reporter |
| `vulnerability_candidates.json` | access_analyzer | scenario_generator · reporter |
| `test_scenarios.json` | scenario_generator | safety_policy · verifier · reporter |
| `safety_decisions.json` | safety_policy | verifier · reporter |
| `verification_results.json` | verifier | knowledge_graph · reporter |
| `diagnosis_report.json` | reporter | 로컬 사용자 |
| `evaluation_results.json` | reporter | 개발 평가 사용자 |
| `ground_truth.json` | test_fixture / 테스트 웹 담당 | reporter(개발 평가 전용) |
- 읽기 주체에는 명시된 입력·input_refs를 실제 읽는 직접 소비자를 표시했다. 출력 변경 협의 대상은 이 소비자들이다. 실행 때 파일 경로 또는 불변 input_refs로 전달하고 폴더에서 임의의 최신 파일을 찾지 않는다.
- 최소 호출 스크립트는 순서·경로·실행 인자를 전달한다. 각 모듈이 자기 입력·출력 내용과 상태를 해석·검증하고, 다른 모듈의 JSON을 대신 작성·수정하지 않는다.
- 동일한 `(run_id, iteration, producer, filename)`의 완료 결과는 한 번 공개한다. 재실행·수정·다른 revision 조회는 호출 시 전달받은 새 회차·경로를 사용하고 작성자가 새 artifact_id로 발행한다.
- 입력 오류 수정은 생산자에게 요청한다. 수정된 원본과 영향받는 후속 결과는 새 산출물로 발행한다. 소비자가 수정 사본을 원본인 것처럼 끼워 넣지 않는다.

### ID·참조·근거·상태의 수정 주체

| 항목 | 유일한 책임 주체 | 소비자 처리 |
| --- | --- | --- |
| run_id·iteration·mode·전역 대상/계정/누적 제한·신뢰 경로 루트 | 사용자가 제공한 실행 인자 / 각 모듈이 입력 범위와 일치를 검증 | 읽기 전용. 변경 요청은 공개 제어 절차로 전달한다. |
| artifact_id·producer·created_at·작업 status·errors·자기 측정값 | 해당 산출물 작성 모듈 | 다른 모듈의 값을 정정하지 않고 자기 작업 오류·상태를 기록한다. |
| 원본 account_id·role_id·request_id·session_ref | collector | 원본 ID와 계정·역할·세션 관계를 보존한다. 이름을 바꾸거나 세션을 임의 복원하지 않는다. |
| 추가 노드·업무 흐름·질의·후보·시나리오·판정·검증 ID | 그 레코드를 생성하는 담당 모듈 | 참조 대상 ID를 계승한다. 출력 명세에 없는 식별자 규칙을 새 공통 규약으로 만들지 않는다. |
| graph_id·graph_revision·실제 KG 상태 | knowledge_graph가 생성·갱신하고 자기 공개 실행 응답으로 전달 | 직접 DB를 수정하거나 revision을 올리지 않는다. |
| 근거 파일·EvidenceRef·sha256·비밀값 제거 | 근거를 수집·생성한 모듈 | 원본을 보존한다. 가공 근거가 필요하면 자기 소유 경로에 새 근거로 생성한다. |
| 계획·계획 해시·Safety 결정 | 계획: scenario_generator / 해시로 묶은 판정: safety_policy | verifier는 일치를 검증한다. URL·단계·파라미터 변경은 새 계획과 새 Policy 평가로 처리한다. |
| dataset_version·Ground Truth·테스트 웹 초기 상태 | test_fixture / 테스트 웹 담당 | reporter는 고정 버전을 읽어 평가한다. 정답을 의미·후보·시나리오 생성에 전달하지 않는다. |
- 실행 로그는 자기 로그에 기록한다. EvidenceRef로 인용할 로그는 닫힌 불변 스냅샷을 자기 evidence 경로에 보관한다.

## 고도화와 계약 변경의 경계

- **자기 내부 고도화:** 알고리즘·모델·프롬프트·캐시·배치·DB 인덱스·렌더링·utils는 자기 담당자가 관리한다. 기존 출력 계약과 근거·판정 의미를 유지하고 공통 잠금 환경에서 자기 테스트로 동작을 확인한다. 라이브러리 추가·버전 변경은 아래 공통 환경 변경 절차를 적용한다.
- **출력 계약 변경:** 생산자가 자기 출력 Schema·변경 명세·버전·샘플을 제안한다. 그 JSON 또는 근거를 실제 읽는 직접 소비자만 입력 Schema·adapter·테스트를 검토·수정한다. 한 소비자면 두 담당자, 여러 소비자면 위 표의 해당 담당자들이 협의한다.
- v0.1은 미정의 키를 거절한다. 선택 필드 추가도 소비자가 자동 수용한다고 가정하지 않는다. 새 버전을 받기 전에는 합의한 기존 버전을 유지하거나 버전별 adapter를 제공한다.
- **버전 범위:** schema_version은 해당 파일 계약의 버전이다. 현재 11개 파일은 0.1.0으로 시작한다. 한 연결 계약을 변경해도 무관한 파일·모듈의 계약 버전을 함께 올리지 않는다.
- **변경 순서:** 생산자 제안 → 직접 소비자 합의 → 생산자 출력 Schema/adapter/샘플 수정 + 소비자 입력 Schema/adapter/샘플 수정 → 양쪽 담당자의 검증·실제 샘플 수신 확인 → 합의한 버전·시점으로 병합한다. 각자 자기 폴더만 수정한다.
- 이전 단계에서 읽은 파일과 자기 출력 파일의 계약 버전은 각각 관리한다. 입력 대응을 바꾸더라도 자기 출력 계약을 유지하면 그다음 소비자는 수정할 필요가 없다.

## 허용하는 모듈 간 의존 경계

- 각 모듈은 필요한 공개 입력·불변 근거·명세된 공개 창구에만 의존한다. 다른 모듈의 내부 소스·utils·Schema·전역 변수·private 경로·DB driver·브라우저 객체를 사용하지 않는다.
- 공개 입력은 소비자가 자기 adapter에서 내부 모델로 변환한다. 공개 창구의 client와 테스트 대역도 소비자 폴더에 둔다. 제공자의 클래스나 구현을 가져와 테스트 대역으로 사용하지 않는다.
- 일반 산출물 연결은 명시된 파일·참조·완료 통지를 사용한다. 모듈이 다음 모듈을 import하거나 실행하지 않고, 루트 최소 호출부가 공개 entrypoint만 연결한다.
- 세션 재사용은 collector가 소유한 공개 세션 창구로 제한한다. 공개 컨텍스트는 합의된 값·불투명 핸들·사용 규약이며, 내부 브라우저/세션 객체를 소비자가 직접 조작한다는 뜻이 아니다. verifier는 자기 adapter로 창구를 사용한다.
- KG 소비자는 query_key·parameters와 결과 계약만 사용한다. DB 접속·Cypher·내부 노드 저장 방식·revision 갱신은 knowledge_graph 내부에 둔다.
- 공개 호출의 요청·반환·오류·버전과 세션의 만료·대여/반납·종료 규약은 제공자·직접 소비자가 연결 구현 전에 구체화한다. 입력·출력 계약과 의존성이 그대로인 내부 변경은 다른 모듈의 코드 수정을 요구하지 않는다.
- 독립성은 다른 모듈 실제 구현을 실행 경로에서 제외한 fixture·공개 대역 테스트로 확인한다. 공통 잠금 환경에 따른 패키지 호환 책임과 실제 서비스 연결 검증은 별도로 확인한다.

## 공통 개발환경과 수정 책임

**개발환경·관리자 최초 설정·변경/병합의 기준:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

- 각 담당자는 같은 Python 버전과 루트 `requirements.lock.txt`를 설치한다. 입력 처리·출력 검증·기능·테스트의 책임은 계속 각 모듈에 있다.
- 모듈별 `requirements.txt`는 해당 담당자의 직접 의존성 선언이며, 루트 네 설정 파일과 전체 잠금 파일은 GitHub 관리자만 수정한다.
- 출력 계약 변경은 생산자와 모든 직접 소비자가 각자의 Schema·adapter·fixture를 수정한다. 관리자에게 모듈의 의미·출력 검증 책임을 넘기지 않는다.
- 라이브러리·Python·OS 변경은 JSON 연결 밖의 모듈에도 영향을 줄 수 있다. 관리자가 전체 환경을 해결하고 영향받는 담당자가 같은 환경에서 자기 기능을 검증한다.
- 이 문서는 공통 규칙을 설명하는 참조 문서다. 중앙 공유 구현 폴더·검증 라이브러리·통합 담당을 추가하지 않는다.

## 모듈 담당자에게 부여하는 전체 책임

- 입력 처리·입력 검증·실제 기능·출력 생성·출력 검증·오류/부분 결과 처리·저장·설정·모듈별 의존성 선언·테스트·동작 확인을 해당 모듈 담당자가 끝까지 책임진다. 루트 환경 파일·전체 잠금 파일은 관리자 수정 범위다.
- 파서·해시·경로·원자적 저장·타입·Schema 같은 기능도 자기 모듈에 둔다. 외부 공통 구현이나 다른 모듈 내부 코드를 import하지 않는다. 필요한 중복 도구는 각 담당자가 독립 관리한다.
- 자기 모듈은 팀 공통 잠금 환경과 명세 입력 fixture로 검증할 수 있어야 한다. 명시된 외부 서비스·세션 공개 창구는 공개 규약과 대역으로 분리한다.
- CI를 사용하면 각 모듈 담당자가 작성한 자체 테스트를 호출한다. 중앙 검증기·통합 담당에게 입력/출력 검증 책임을 넘기지 않는다.
- 테스트 웹·Ground Truth는 평가 자료 제공자가 버전·초기 상태를 책임진다. reporter는 개발 평가에서 자기 입력으로 검증하며 정답을 분석·시나리오 생성에 전달하지 않는다.

## 파일 전달·오류·변경 규칙

- 각 모듈은 자기 출력만 작성하고 입력·다른 모듈 결과는 읽기 전용으로 사용한다. 단일 JSON을 여러 모듈이 함께 수정하지 않는다.
- 결과 경로는 `runs/<run_id>/artifacts/iteration-<NNN>/<producer>/<filename>.json`이다. 완료 파일은 불변이며 새 처리·수정 계획은 새 artifact_id·회차·경로에 저장한다.
- 자기 폴더의 임시 파일에 완성 → flush·닫기 → 같은 파일시스템에서 rename/replace → 완료 통지 순서로 공개한다. reader는 완료 통지 뒤 읽는다.
- 입력의 Schema·지원 버전·run_id·작성자·artifact_id·iteration·해시와 필수 참조를 확인한다. 미정의 필드를 조용히 무시하거나 누락 필드를 임의 기본값으로 채우지 않는다.
- input_refs는 필요한 불변 입력의 명시적 읽기 참조이다. 필요한 원본 요청·근거를 이 참조로 읽고 다른 모듈 내부 소스를 import하지 않는다.
- 상대 경로는 신뢰된 루트 안에서 resolve한다. 상위 경로(..)·절대경로·다른 run·루트 밖 symlink를 거절한다. ArtifactRef·EvidenceRef는 run_root, GroundTruthRef는 project_root 기준이다.
- LLM 출력도 프로그램이 타입·ID·참조·범위를 다시 확인한다. JSON Schema 검증과 교차 참조·의미·권한·안전 검증을 함께 구현한다.
- 인증 헤더·쿠키·토큰·비밀번호·계정 원문은 전달 JSON·LLM 입력·공유 로그에 넣지 않는다. 큰 본문·DOM·스크린샷은 EvidenceRef로 연결하고 보호된 세션은 수집기 공개 창구에서 받는다.
- 업무 Rule·LLM 프롬프트·DB·세션·파서·검증·경로·해시·저장·내부 타입·가변 상태를 모두 소유 모듈 안에 둔다. 참조 대상은 명시된 입력 파일·근거 또는 필요한 공개 서비스 창구로 한정한다.
- 파일명·작성자·ID 의미·필수 필드·타입·enum·단위 변경은 생산자와 소비자가 함께 검토하고 계약 버전을 올린다. v0.1은 미정의 키를 거절하므로 선택 필드 추가도 자동 호환된다고 가정하지 않는다.

## 공통 파일 형식

ground_truth.json을 제외한 10개 실행 파일에 적용한다. ground_truth.json의 정적 입력 계약은 [평가·리포트 모듈](m8-reporter.md)에 있다.

필수인 nullable 필드는 키를 포함하고 알 수 없을 때 null을 기록한다. 빈 배열은 []로 표현한다. 시간은 UTC RFC3339, Method는 대문자, 헤더 이름은 소문자, ID는 문자열이다. JsonValue는 JSON의 문자열·숫자·boolean·object·array·null을 뜻한다. NaN·Infinity·중복 키를 허용하지 않는다.

### 공통 envelope

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `schema_version` | `string: 0.1.0` | 필수 | 해당 파일 계약의 버전. 모듈은 합의한 지원 버전을 검증하며, 이 파일의 변경이 무관한 모듈의 일괄 버전 변경을 요구하지 않는다. |
| `artifact_type` | `string` | 필수 | 파일명에서 .json을 뺀 종류. 파일별 Schema가 해당 값을 고정한다. |
| `artifact_id` | `string` | 필수 | 산출물 고유 ID. 완료 후 같은 ID의 내용을 수정하지 않는다. |
| `run_id` | `string` | 필수 | 진단 실행 ID. 다른 실행의 데이터와 섞지 않는다. |
| `iteration` | `integer` | 필수 | 0부터 시작하는 분석 회차. 재분석 시 새 회차 폴더를 사용한다. |
| `producer` | `string` | 필수 | 단일 작성 모듈. 각 모듈 출력 계약과 파일별 Schema에 정한 값. |
| `mode` | `enum: diagnosis, development` | 필수 | 실제 진단 또는 개발 평가 실행 구분. |
| `created_at` | `string (date-time)` | 필수 | 작성 완료 시각. UTC RFC3339. |
| `status` | `enum: completed, partial, failed` | 필수 | 파일을 생성한 모듈 작업 상태. 취약점 재현 성패와 별개이다. |
| `input_refs` | `array<ArtifactRef>` | 필수 | 이 작업이 사용한 불변 입력 산출물 참조 목록. |
| `errors` | `array<ErrorItem>` | 필수 | 작업 단위 오류. completed면 빈 배열; partial·failed면 오류가 있어야 한다. |
| `runtime_metrics` | `RuntimeMetrics / null` | 필수 | 모듈별 측정값. 수집하지 못하면 null. |

| 추가 필드 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `data` | `파일별 object / null` | 필수 | completed·partial이면 모듈 출력 절에 정의한 객체. failed이면 null. |

### 파일 작업 상태

| status | 의미 | errors / data |
| --- | --- | --- |
| `completed` | 담당 작업의 정상 완료. 차단·미재현 결과도 정상 처리에 포함. | errors=[], data=객체 |
| `partial` | 일부 항목 미처리. 빠진 범위를 오류로 추적. | errors 1개 이상, data=유효 객체 |
| `failed` | 사용할 업무 결과를 생성하지 못함. | errors 1개 이상, data=null |

이 status는 파일 작성 작업 상태이다. verifier의 result·execution_status와 각각의 의미에 맞게 별도로 기록한다.

### 공통 참조·오류·측정 레코드

### ArtifactRef

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `artifact_id` | `string` | 필수 | 참조하는 산출물의 고유 ID. |
| `artifact_type` | `string` | 필수 | 참조하는 산출물 종류. 확정한 JSON 파일명에서 확장자를 뺀 값. |
| `iteration` | `integer` | 필수 | 참조 산출물이 만들어진 분석 회차. |
| `path` | `string` | 필수 | `run_root` 기준 상대 경로. 해당 작성 모듈 폴더의 파일을 가리킨다. |
| `sha256` | `string` | 필수 | 참조 파일의 실제 UTF-8 바이트 SHA-256. |

### EvidenceRef

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `evidence_id` | `string` | 필수 | 근거 파일의 고유 ID. |
| `kind` | `enum: request, response, dom, screenshot, state, execution_log` | 필수 | 근거 파일 종류. |
| `path` | `string` | 필수 | `run_root` 기준 근거 파일 상대 경로. `evidence/<producer>/` 아래에 보존한다. |
| `sha256` | `string` | 필수 | 근거 파일 바이트 SHA-256. |
| `redacted` | `boolean` | 필수 | 공유 가능한 근거에서 인증·비밀값을 제거했는지 여부. |

### ErrorItem

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `code` | `string` | 필수 | 모듈이 정의한 오류 코드. 예: SESSION_EXPIRED, CONTRACT_INVALID. |
| `message` | `string` | 필수 | 비밀값을 제거한 오류 설명. |
| `item_ref` | `string / null` | 필수 | 오류가 발생한 요청·시나리오·질의 ID. 파일 전체 오류면 null. |
| `retryable` | `boolean` | 필수 | 동일 조건으로 다시 시도할 수 있는지 여부. 자동 재시도 허가를 뜻하지 않는다. |

### RuntimeMetrics

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `duration_ms` | `integer / null` | 필수 | 모듈 실행 시간. 측정하지 못하면 null. |
| `llm_calls` | `integer / null` | 필수 | LLM 호출 횟수. 미측정은 null, 실제 0회는 0. |
| `input_tokens` | `integer / null` | 필수 | LLM 입력 토큰 수. 미측정은 null. |
| `output_tokens` | `integer / null` | 필수 | LLM 출력 토큰 수. 미측정은 null. |
| `peak_memory_mb` | `number / null` | 필수 | 최대 메모리 사용량(MB). 측정하지 못하면 null. |

### ModelInfo

| 필드 | 타입·허용값 | 필수 | 의미 |
| --- | --- | --- | --- |
| `model_id` | `string` | 필수 | 사용한 모델 식별자. 예시 값은 모델 추천이 아니다. |
| `model_version` | `string` | 필수 | 동일 모델 이름 아래의 버전·체크포인트 식별자. |
| `prompt_version` | `string` | 필수 | 사용한 프롬프트 버전. |
| `temperature` | `number / null` | 필수 | 모델 생성 온도. 사용하지 않으면 null. |
| `seed` | `integer / null` | 필수 | 재현용 seed. 지원하지 않으면 null. |

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md)