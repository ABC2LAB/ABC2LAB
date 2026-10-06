# 실행 제어 · runner와 폴더 구조

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md)

<aside>
⚙️

각 담당자가 자기 모듈의 입력 처리·기능·출력 검증·저장·오류 처리·동작 확인을 전부 책임진다. 전체 실행의 최소 스크립트는 순서와 경로만 연결한다.

</aside>

## 공통 개발환경과 관리자 초기 설정

**환경·루트 파일·clone 후 설치·변경/병합 기준:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

모든 모듈은 같은 Python과 루트 잠금 파일로 만든 환경에서 개발·검증한다. 독립성은 입력·출력·내부 구현의 책임 경계에 적용한다. 각 담당자는 자기 fixture·공개 대역으로 독립 실행하며 다른 모듈의 실제 구현을 요구하지 않는다.

## 각 모듈 담당자의 전체 책임

각 담당자는 명세 입력을 받아 계약된 출력을 완성하는 모든 구현·검증·동작 확인을 자기 모듈 안에서 수행한다. 다른 모듈은 이 모듈의 내부 구현을 알 필요 없이 공개 입력·출력만 사용한다.

| 자기 모듈 경로 | 담당 내용 |
| --- | --- |
| `README.md` | 입력·출력·버전·직접 소비자·독립 실행 방법·실패 처리·필요한 외부 연결을 기록 |
| `entrypoint.py` | 공개 실행 창구·CLI. 인자와 입력을 검증하고 자기 처리·출력 검증·저장·완료/실패 통지를 수행 |
| `service.py` 및 추가 내부 파일 | 실제 분석·추론·질의·Policy·재현·리포트 로직 |
| `schemas/input/` | 이 모듈이 실제 읽는 JSON별 입력 Schema. 생산자의 출력 계약과 합의한 버전을 자기 폴더에 둠 |
| `schemas/output/` | 자기 출력 JSON별 Schema. 이 모듈 담당자가 출력 계약과 검증을 관리 |
| `utils/` 또는 내부 adapter | 파싱·타입·ID·참조·해시·경로·검증·원자적 저장을 자기 구현으로 처리 |
| `configs/` | 자기 모델·프롬프트·규칙·DB/서비스 접속·실행 설정. 신뢰된 사용자 허용 범위와 제한을 확대하지 않음 |
| `requirements.txt` | 실제 사용하는 직접 의존성과 지원 버전 선언. 담당자가 제안하고 관리자가 전체 루트 lock에 반영한다. 실행·테스트 환경은 팀 공통 잠금 파일을 사용 |
| `tests/fixtures/`, `tests/` | 자기 입력 fixture·출력 샘플·정상/부분/실패·의존성 오류·참조·안전 조건의 테스트와 동작 확인 |
- Schema는 각 파일에 필요한 중첩 정의를 포함한다. 다른 모듈이나 중앙 정의에 대한 런타임 import/$ref를 요구하지 않는다.
- 출력의 기준 Schema는 생산자 소유다. 직접 소비자는 같은 계약을 자기 입력 Schema로 관리하며 계약 변경 때 양쪽을 함께 맞춘다.

## 예상 상위 폴더와 실행 파일

| 경로 | 역할·수정 범위 |
| --- | --- |
| `modules/collector/` | 수집 로직·수집 JSON·세션 창구·자기 검증·모듈 설정 |
| `modules/semantic_analyzer/` | 정규화·의미 추론·자기 입력/출력 검증·모듈 설정 |
| `modules/knowledge_graph/` | Neo4j 연결·질의·적재·revision·검증 반영·자기 검증·모듈 설정 |
| `modules/access_analyzer/` | 질의 계획·접근 관계 분석·후보 생성·자기 검증·모듈 설정 |
| `modules/scenario_generator/` | LLM 시나리오 생성·계획 검증·모듈 설정 |
| `modules/safety_policy/` | Policy·승인 창구·허용/차단/승인 대기 판정·자기 검증·모듈 설정 |
| `modules/verifier/` | 허용 계획 재현·세션 소비·실행 제한·근거/출력 검증·모듈 설정 |
| `modules/reporter/` | 진단·개발 평가·리포트·자기 입력/출력 검증·모듈 설정 |
| `README.md` | GitHub 관리자가 표준 환경·최초 설치·pull 후 동기화·8개 모듈의 실행 문서 링크를 관리 |
| `.python-version` | 관리자만 수정. 공통 Python 정확한 패치 버전 |
| `.gitignore` | 관리자만 수정. `.venv`·비밀값·캐시·실행/빌드 결과 제외 |
| `requirements.txt` | 관리자만 수정. 초기 공통 패키지와 8개 모듈의 requirements를 참조 |
| `requirements.lock.txt` | 관리자만 도구로 생성·갱신. 전체 의존성의 정확한 버전·해시와 팀원 설치 기준 |
| `pipeline.py` | GitHub 관리자가 루트 호출 순서·파일 경로·실행 인자 연결을 관리. 모듈의 업무 검증·판정을 대신하지 않음. 최초 설정 범위 이후 전체 실행이 필요한 단계에 구현 |
| `runs/<run_id>/` | 각 모듈이 자기 경로에 작성한 실행 JSON·근거·private 상태·로그·리포트 |
| `datasets/<dataset_id>/ground_truth.json` | 개발 평가에만 사용하는 읽기 전용 외부 입력 자료. reporter만 계약에 따라 사용 |
- 중앙 공유 도구·Schema·업무 설정·검증 구현 폴더를 두지 않는다. 필요한 구현은 각 모듈의 utils·schemas·configs·tests에 둔다. 루트의 Python·의존성·Git 제외 설정은 개발환경을 맞추는 관리자 관리 파일로 유지한다.
- 루트의 최소 호출 스크립트는 연결 순서를 실행하기 위한 파일이다. 각 모듈은 이 스크립트 없이도 명세 입력과 자기 설정으로 독립 실행할 수 있어야 한다.

## 독립 실행과 공개 호출 규약

- 공개 함수 표기는 `entrypoint.run(operation, input_paths, output_dir, context)`로 유지한다. context에는 명세된 실행 값만 전달하고 각 모듈이 자기 내부 타입과 검증으로 처리한다. 공통 RunContext 클래스나 공용 검증 라이브러리를 import하지 않는다. 다른 모듈의 service·DB driver·브라우저/세션 객체·전역 가변 상태를 context에 넣어 공유하지 않는다.
- 각 모듈은 자기 CLI 실행 방법도 제공한다. 최소 호출 스크립트가 같은 공통 `.venv`의 Python으로 모듈을 호출할 수 있도록 완료 상태·생성 파일 경로·제어 메타데이터·실패를 명확히 알린다. 별도 프로세스 호출에도 팀 기준 Python·잠금 환경을 사용한다.
- 전달 값은 실행 ID·회차·mode·신뢰된 루트·허용 범위·사용자 제한·필요한 graph 상태 등 입력 표에 명시한 정보다. 각 모듈이 자기 입력 JSON·참조·범위와 대응을 검증한다.
- 입력 처리와 출력 검증은 각 모듈에서 완료한다. 호출 스크립트는 완료/실패와 파일 경로를 다음 호출로 전달하며 JSON 업무 필드·Schema·LLM 결과를 해석하지 않는다.
- 미발행·저장 불가·잘못된 입력은 해당 모듈의 실패 규약으로 통지한다. 유효한 partial은 누락 근거와 함께 전달한다. 받는 모듈은 자기 계약의 정상/부분/실패 처리와 후보 보존을 수행한다.

| module_id | 공개 operation |
| --- | --- |
| collector | collect |
| semantic_analyzer | analyze |
| knowledge_graph | ingest · query · apply_verification |
| access_analyzer | prepare_queries · analyze |
| scenario_generator | generate |
| safety_policy | evaluate |
| verifier | verify |
| reporter | report · evaluate |

### 공개 연결을 구현하기 전의 합의

- 제공자와 직접 소비자는 인자 이름·값/타입·지원 버전, 반환 상태·실제 생성 파일 경로·제어 메타데이터, 오류 전달 방식과 처리 책임을 문서와 샘플로 고정한다. 함수·CLI 간 같은 의미를 사용한다.
- 파일 경로·ID·revision 같은 전달값은 합의한 기본 타입으로 표현한다. 제공자의 내부 클래스·예외 타입·DB/브라우저 객체를 소비자 입력의 필수 타입으로 만들지 않는다.
- 세션 창구는 접근 방식·허용 동작·유효성·만료·대여/반납·종료와 요청/응답·오류 규약을 collector와 verifier가 확정한다. KG 제어 응답은 graph_id·revision·준비/실패 상태의 값/타입을 KG와 직접 소비자가 확정한다.
- 소비자는 자기 adapter와 같은 계약의 테스트 대역으로 창구를 사용한다. 내부 객체 공유로 연결을 대신하지 않는다. 필요한 실제 바인딩·경로 전달은 루트 호출부의 연결 책임이며 모듈 업무 처리를 옮기지 않는다.
- 모듈은 다음 모듈의 실행을 책임지지 않는다. 루트 호출부 없이 자기 명세 입력·설정으로 독립 실행하고, 다른 모듈 실제 소스 없이 대역으로 테스트할 수 있어야 한다.
- 공개 창구의 세부 규약 합의, 모듈별 독립 테스트, 실제 연결 실행은 각각 확인한다. 현재 명세는 설계 기준이며 이 확인들이 이미 완료됐다는 뜻은 아니다.

## JSON으로 전달하지 않는 상태의 직접 연결

| 연결 | 제공 모듈 책임 | 소비 모듈 책임 |
| --- | --- | --- |
| collector ↔ verifier 세션 공개 창구 | collector가 세션의 생성·유효성·갱신·대여/반납·종료와 공개 adapter를 관리. 기존 수집기 core 탐색 유지 | verifier가 session_ref·계정·역할과 공개 접속 규약을 검증하여 사용. 비밀값을 전달 JSON·LLM에 넣지 않음 |
| knowledge_graph ↔ access_analyzer / 평가 입력 | KG가 DB·graph_id·revision·준비 상태·query_key/결과 의미·오류를 관리 | access_analyzer는 합의한 질의 JSON을 작성하고 결과를 검증. reporter는 명시된 snapshot을 자기 입력으로 검증 |
| 사용자 ↔ safety_policy 승인 창구 | safety_policy가 자기 승인 입력·기록·계획 해시 대응을 검증하고 새 판정을 발행 | verifier는 해당 판정과 계획 해시·제한을 검증하고 allow만 실행 |
- 이 연결은 필요한 모듈 간 공개 계약이다. 세션 객체·Neo4j driver·내부 타입을 다른 모듈의 공유 구현으로 만들지 않는다.
- 공개 창구·완료 응답의 값/상태/오류·수명 규약도 제공자와 직접 소비자가 책임지고 명세화한다. 현재는 설계 상태이며 해당 코드가 구현되었다는 뜻은 아니다.

## 실행 결과의 소유 경로

| 경로 | 작성 책임 |
| --- | --- |
| `runs/<run_id>/artifacts/iteration-<NNN>/<producer>/` | 해당 producer가 검증된 자기 JSON을 원자적으로 공개 |
| `runs/<run_id>/evidence/<producer>/` | 근거를 생성한 모듈이 비밀값 제거·해시·불변 근거를 관리 |
| `runs/<run_id>/private/<module_id>/` | 해당 모듈의 보호된 가변 내부 상태. 세션은 private/collector 아래에서 collector가 소유 |
| `runs/<run_id>/logs/<producer>/` | 각 모듈의 자기 실행·오류 로그 |
| `runs/<run_id>/reports/` | reporter가 작성하는 로컬 리포트 |
- 소비자는 전달된 파일·input_refs·EvidenceRef만 읽고 다른 모듈 결과를 덮어쓰지 않는다. 재처리·수정은 새 artifact_id·회차·경로로 발행한다.

## KG 조회·검증 반영의 연결 순서

1. knowledge_graph.ingest가 graph_id·revision·준비 상태를 자기 공개 실행 응답으로 반환한다.
2. access_analyzer.prepare_queries가 합의한 graph 상태로 graph_query.json을 작성·검증한다.
3. knowledge_graph.query가 입력 질의를 검증하고 graph_query_result.json을 작성·검증한다.
4. access_analyzer.analyze가 질의 결과를 자기 입력으로 검증하고 vulnerability_candidates.json을 작성한다.
5. 시나리오 생성·Policy·검증 뒤 knowledge_graph.apply_verification이 verification_results.json의 실제 근거를 검증하여 반영하고 revision을 갱신한다.
6. 다음 회차는 새 경로와 갱신된 graph 상태로 호출한다. 모듈별 계약에 따라 중복 후보·verification_id 반영·자기 누적 제한을 처리한다.

## 병합과 출력 변경의 책임

- 일반 개발·최적화는 자기 모듈 폴더만 수정하고 공통 잠금 환경·명세 입력으로 기능·출력·실패 처리를 검증한다. 루트 환경 파일 변경은 관리자에게 요청한다. 병합 과정에서 다른 개발자가 내부 코드를 맞춰주는 작업을 전제로 하지 않는다.
- 출력 계약을 바꾸는 담당자는 변경 명세·Schema·버전·샘플을 직접 소비자에게 제시한다. 생산자는 자기 출력, 소비자는 자기 입력 Schema·adapter·테스트를 수정한다. 무관한 모듈의 버전·코드를 함께 바꾸지 않는다.
- 하나의 파일을 여러 모듈이 실제 읽으면 그 소비자들이 협의 대상이다. 각 모듈 페이지의 출력 표와 파일별 작성자/읽기 주체 표를 따른다.
- 각 담당자는 검증한 입력/출력 버전·독립 실행 명령·테스트 결과·외부 의존성·처리 불가 조건을 자기 README에 기록한다. CI를 사용하면 각 모듈의 자체 테스트 명령을 호출한다.
- 완성된 8개 모듈의 전체 호출 결과는 최소 스크립트로 확인할 수 있다. 입력·출력 오류가 드러나면 해당 계약의 생산자와 직접 소비자가 자기 폴더에서 해결한다. 모듈 검증 결과와 전체 실행 확인 결과를 각각 기록한다.

## 각 모듈의 구현 완료 기준

- [ ]  관리자 기준 Python·공통 루트 잠금 파일로 환경을 준비하고 독립 실행·테스트를 확인한다.
- [ ]  모듈 의존성 변경은 관리자 전체 lock 갱신과 영향받는 모듈 검증을 마친 동일 병합본에 포함된다.
- [ ]  명세 입력과 자기 설정으로 독립 실행하고 필요한 외부 창구는 공개 규약으로만 사용한다.
- [ ]  입력 Schema·버전·ID·참조·해시·경로·정상/부분/실패를 자기 코드에서 검증한다.
- [ ]  자기 출력 Schema·필수 필드·의미·근거·판정 조건을 검증한 뒤 실제 저장과 완료/실패를 확인한다.
- [ ]  자기 utils·타입·Schema·설정·의존성 선언·fixture·테스트와 독립 실행 문서가 자기 폴더에 있다. 실제 설치 버전은 공통 루트 lock을 따른다.
- [ ]  출력 변경은 직접 소비자와 합의하고 양쪽의 샘플 수신·동작 확인을 완료한다.
- [ ]  기존 8개 모듈·11개 JSON 파일·Safety 판정·미검증 후보 보존 규칙을 유지한다.

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md)