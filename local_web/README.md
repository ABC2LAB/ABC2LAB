# ABC2LAB `local_web` 초기 통합 아키텍처

> 상태: 구현 전 초기 아키텍처 문서
>
> 핵심 원칙: **경계는 강하게, 구현은 느슨하게 유지한다.**

`local_web`은 ABC2LAB의 사용자 인터페이스 계층이다. 이 문서는 현재 단계에서
반드시 지켜야 할 책임 경계와 제품 방향을 정의한다. 구체적인 API, 데이터 구조,
프레임워크, 실행 프로세스는 각 진단 모듈의 공개 인터페이스가 안정화된 뒤
확정한다.

## 1. 문서 목적과 현재 단계

ABC2LAB의 진단 모듈은 현재 구현과 통합이 진행 중이다. 일부 공개 operation,
artifact, Schema, 실행 방식은 아직 최종 확정되지 않았다. 따라서 이 문서는
상세 구현 명세서가 아니라 다음을 정의하는 초기 아키텍처 문서다.

- `local_web`의 책임과 비책임
- 사용자에게 제공할 기능의 방향
- 진단 엔진과 UI의 분리 원칙
- 통합 과정에서 지켜야 할 보안·독립성 원칙
- 구현을 구체화하기 전에 확인할 순서

문서의 내용을 다음 세 범주로 구분한다.

### 현재 확정된 원칙

- `local_web`은 UI·Presentation·User Interaction 계층이다.
- 진단 기능과 판정은 독립된 `modules/*`가 담당한다.
- `local_web`과 `modules/*`는 서로의 내부 구현에 의존하지 않는다.
- UI 요구만으로 모듈 Schema나 business logic을 변경하지 않는다.
- Safety Policy와 최종 취약점 판정의 소유권을 UI로 옮기지 않는다.
- 인증정보와 비밀값을 화면 상태, 일반 결과물, 이벤트, 로그에 노출하지 않는다.

### 현재 예상하는 방향

- 사용자는 ABC2LAB을 실행하고 로컬 브라우저로 진단을 제어한다.
- 여러 독립 모듈 사이에는 별도의 통합 경계가 필요하다.
- UI는 통합 경계가 제공하는 진행 상태와 결과를 화면용 형태로 표현한다.
- Pipeline Runner는 통합 경계의 구현 후보 중 하나다.
- Knowledge Graph, 승인 요청, 검증 결과, 최종 리포트를 주요 화면으로 제공한다.

### 향후 모듈 구현 후 확정할 사항

- REST API 경로와 Web-facing DTO 필드
- Runner와 Web 사이의 JSON protocol과 event 이름
- 최종 artifact 경로와 모듈 operation 이름
- Runner, process, service, adapter의 구체적인 구성
- 인증정보와 session context 전달 방식
- Graph visualization 데이터 포맷
- Frontend·Backend framework와 상태 저장 방식
- SSE, WebSocket, polling 등 진행 상태 전달 방식
- Local LLM provider, 모델, port, API 형식

이 문서의 예시와 후보는 계약이 아니며, 구현 시점에 실제 공개 인터페이스를
확인한 뒤 변경할 수 있다.

## 2. 최종 제품 방향

ABC2LAB은 사용자의 컴퓨터에서 실행되는 온프레미스 웹 취약점 진단
애플리케이션을 목표로 한다. 사용자는 최종적으로 로컬 브라우저 화면에서 진단을
시작하고 진행 상황과 결과를 확인한다.

개념적인 사용자 흐름은 다음과 같다.

```text
ABC2LAB 실행
    ↓
로컬 웹 인터페이스 실행
    ↓
사용자 브라우저 접속
    ↓
대상 URL 입력
    ↓
테스트 계정 입력
    ↓
진단 시작
    ↓
진단 진행 상태 확인
    ↓
필요 시 사용자 승인
    ↓
Knowledge Graph 확인
    ↓
취약점 검증 결과 확인
    ↓
최종 리포트 확인
```

이 흐름은 제품 경험의 방향만 나타낸다. Web server, 진단 process, Runner, DB,
Local LLM을 어떤 process 조합으로 실행할지는 아직 확정하지 않는다.

## 3. `local_web`의 정의

`local_web`은 새로운 취약점 진단 모듈이 아니다. 사용자와 ABC2LAB 사이의
인터페이스 계층이다.

```text
local_web
=
User Interface
+
Presentation
+
User Interaction
```

```text
local_web
≠
Diagnosis Engine
```

프론트엔드는 화면과 사용자 입력을 담당한다. Web의 interface 계층은 사용자
요청을 통합 경계에 전달하고, 통합 경계가 제공하는 상태와 결과를 화면에 적합한
형태로 전달한다. 두 영역 모두 진단 판단을 소유하지 않는다.

## 4. 책임 경계

### 4.1 `local_web`이 책임지는 영역

`local_web`은 사용자 관점의 기능만 담당한다.

- 대상 URL 입력
- 여러 역할의 테스트 계정 입력
- 진단 시작 요청
- 진단 취소·재개 등 사용자 제어 요청
- 진단 진행 상태와 오류 표시
- 사용자 승인 요청 표시
- 승인·거절 입력 전달
- Knowledge Graph 시각화
- 취약점 후보와 검증 결과 표시
- 최종 진단 리포트 표시
- development 모드의 성능 평가 결과 표시
- 사용자에게 필요한 상태와 결과의 화면용 표현

정확한 화면 DTO와 컴포넌트 구조는 현재 확정하지 않는다.

### 4.2 `local_web`이 책임하지 않는 영역

다음 기능은 `local_web`이 소유하거나 복제하지 않는다.

- 웹 크롤링과 HTTP 요청 수집
- 웹 구조 분석과 의미 분석
- LLM prompt 생성과 LLM 직접 호출
- Knowledge Graph 생성과 Neo4j 저장
- Cypher 생성과 실행
- 취약점 후보 생성
- 접근통제 판단
- 공격 시나리오 생성
- Safety Policy 판단
- 실제 취약점 검증 요청 실행
- 검증 성공·실패와 최종 취약점 판정
- Reporter 결과 생성
- 각 진단 모듈의 business logic

이 책임은 계속 `modules/*`와 향후 정해질 통합 계층에 둔다. Web이 결과를 보기
좋게 변환하는 것과 진단 의미를 다시 판정하는 것을 구분한다.

## 5. 모듈 독립성

### 5.1 내부 구현에 의존하지 않는다

`local_web`은 진단 모듈의 내부 함수, helper, repository, prompt, DB driver,
브라우저 객체를 참조하지 않는다. 다음과 같은 변경이 Web 수정으로 직접 이어지지
않아야 한다.

```text
semantic_analyzer 내부 함수 변경
LLM adapter 변경
Knowledge Graph 저장 방식 변경
Neo4j repository 리팩토링
access_analyzer Rule 변경
scenario_generator prompt 구조 변경
safety_policy 내부 정책 변경
verifier HTTP client 변경
reporter 내부 helper 변경
```

목표는 다음과 같다.

```text
modules/* 내부 구현 변경
        ↓
local_web 영향 없음
```

Web은 향후 합의할 안정된 통합 경계만 사용한다. 그 경계의 구현 방식과 구체적인
데이터 모델은 아직 확정하지 않는다.

### 5.2 역방향 의존성을 금지한다

진단 모듈도 `local_web`을 알아서는 안 된다.

```text
금지되는 방향

modules/*
    ↓
local_web
```

각 모듈은 Web 없이 독립 실행과 독립 테스트가 가능해야 한다. Web을 제거하거나
교체해도 진단 엔진의 공개 동작이 유지되어야 한다.

### 5.3 UI 요구로 모듈 계약을 바로 바꾸지 않는다

UI에 필요한 정보가 없다는 이유만으로 진단 모듈의 Schema나 business logic을
즉시 수정하지 않는다. 다음 순서로 확인한다.

```text
기존 공개 결과에 있는가?
        ↓ 있음
Presentation 계층에서 변환

다른 공개 결과에 있는가?
        ↓ 있음
Integration 계층에서 조합

UI에서 계산 가능한가?
        ↓ 가능
Presentation 계층에서 처리

그래도 필요한 데이터가 없는가?
        ↓
공개 계약 변경 필요성을 별도 검토하고 생산자·소비자와 합의
```

화면 디자인 변경은 가능한 한 `local_web` 내부에서 해결한다.

```text
UI 디자인 변경
        ↓
local_web 변경

진단 엔진 변경 없음
```

## 6. 현재 단계의 추상 구조

현재 전체 구조는 책임 관계만 표현한다.

```text
                User
                  │
                  ▼
             local_web
        User Interface Layer
                  │
                  ▼
         Integration Boundary
                  │
                  ▼
        Independent Modules
                  │
                  ▼
          Diagnosis Results
                  │
                  ▼
       Presentation Boundary
                  │
                  ▼
             local_web
                  │
                  ▼
                User
```

통합 경계는 Web과 진단 모듈이 서로의 내부 구현을 알지 않게 하는 역할을 한다.
실제 구현은 Pipeline Runner, adapter, 별도 process, CLI orchestration, service
layer 등의 형태가 될 수 있다. 현재는 이 중 하나를 최종 구조로 고정하지 않는다.

### Pipeline Runner 후보

여러 독립 진단 모듈을 순서대로 연결하는 통합 계층은 필요하다. Pipeline Runner는
그 구현 후보 중 하나이며, 실제 구조는 각 모듈의 공개 인터페이스가 안정화된 뒤
확정한다.

현재 예상 흐름은 개념적으로 다음과 같다.

```text
collector
  ↓
semantic_analyzer
  ↓
knowledge_graph
  ↓
access_analyzer
  ↓
scenario_generator
  ↓
safety_policy
  ↓
verifier
  ↓
knowledge_graph result update
  ↓
reporter
```

이 순서는 제품 흐름을 이해하기 위한 현재 초안이다. 정확한 operation 이름, 호출
인자, 제어 응답, 중단·재개 방식, artifact 경로를 Web의 확정 계약으로 간주하지
않는다.

## 7. 사용자 입력과 인증정보

### 7.1 입력 방향

최종 제품에서는 사용자가 로컬 웹에서 다음 정보를 입력할 수 있는 방향을
고려한다.

```text
대상 URL

테스트 계정
- Role
- username 또는 account identifier
- password 등 로그인에 필요한 인증정보
```

Guest, User, Admin처럼 여러 역할의 테스트 계정을 받을 수 있어야 한다. 정확한
계정 Schema, 로그인 방식, 전달 단위는 아직 확정하지 않는다.

### 7.2 비밀정보 보안 경계

인증정보 전달 구현은 미정이지만 다음 원칙은 지금부터 고정한다.

- 비밀번호를 일반 진단 artifact에 저장하지 않는다.
- 로그에 비밀번호, 쿠키, 토큰 원문을 출력하지 않는다.
- API 응답과 진행 event에 비밀값을 포함하지 않는다.
- 브라우저 `localStorage`와 `sessionStorage`에 비밀값을 저장하지 않는다.
- URL query parameter에 비밀값을 넣지 않는다.
- 진단 리포트와 평가 결과에 비밀값을 포함하지 않는다.
- 오류 화면에 계정 원문이나 내부 secret 위치를 노출하지 않는다.

구체적인 secret 전달·보관·폐기 방식은 실제 모듈 입력과 process 경계를 확인한 뒤
별도로 설계한다.

### 7.3 인증과 session 재사용 방향

수집 단계에서 테스트 계정으로 인증하고 session context가 만들어지는 경우, 이후
검증 단계에서 이를 재사용할 수 있는 방향을 유지한다.

```text
Test Account
     ↓
Collector
     ↓
Authentication / Session Context
     ↓
Later Verification
```

session의 생성, 소유, 대여·반납, 만료, 전달은 `local_web`의 책임이 아니다.
Web은 사용자 입력을 합의된 통합 경계에 전달하고 공개 상태만 표현한다.

## 8. 주요 화면 방향

요구사항 명세서의 주요 화면 예시는 UI 목적과 사용자 흐름을 결정하는 기준으로
사용한다. 화면 예시가 모듈 Schema나 내부 아키텍처를 강제해서는 안 된다.

### 8.1 새 진단

목적:

- 대상 URL 입력
- 역할별 테스트 계정 입력
- 진단 시작

입력 화면의 정확한 필드와 validation protocol은 계정·통합 계약이 정해진 뒤
확정한다.

### 8.2 진단 진행

목적:

- 진단 실행 여부 확인
- 대략적인 진행 단계 확인
- 실패·중단·승인 필요 여부 확인
- 허용되는 범위에서 취소·재개 요청

최종 단계 이름, 상태 enum, event 형식, 실시간 전달 방식은 아직 미정이다.

### 8.3 Knowledge Graph

목적:

- 시스템이 파악한 웹 구조 확인
- Role·Resource 접근 관계 확인
- 주요 업무 Flow와 검증된 관계 확인

Graph library, node·edge DTO, filtering과 layout 형식은 향후 확정한다.

### 8.4 Safety Policy 승인

목적:

- 위험 가능성이 있는 검증과 그 이유를 사용자에게 알림
- 사용자의 승인 또는 거절 입력 전달

`local_web`은 Safety Policy의 판단을 생성하거나 변경하지 않는다. 승인 입력이
어떻게 검증되고 기록되며 재평가로 이어지는지는 Safety Policy와 통합 경계의 공개
계약이 확정된 뒤 설계한다.

### 8.5 취약점 결과와 리포트

목적:

- 발견된 후보와 검증 결과 확인
- 관련 Evidence 확인
- 최종 진단 리포트 확인

`local_web`은 후보를 확정 취약점으로 바꾸거나 검증 결과를 다시 판정하지 않는다.
미실행·판단불가를 취약점 없음이나 접근 실패로 바꾸어 표시하지 않는다.

### 8.6 Development 성능 평가

개발 단계에서는 Ground Truth 기반으로 다음과 같은 평가를 표시할 수 있다.

- Page·Endpoint·Parameter 발견율
- 구조와 관계 정확도
- 후보 탐지와 검증 성능
- Precision, Recall, Coverage

일반 사용자 기능과 development 평가를 분리한다.

```text
Normal Mode
    ↓
진단 결과와 리포트

Development Mode
    ↓
진단 결과
    +
성능 평가
```

평가 화면은 제품 UI에 강하게 결합하지 않는다. 성능이 안정화된 뒤 일반 사용자
화면에서 제거하거나 내부 개발·CI 기능으로만 유지할 수 있다.

테이블·카드, dashboard, navigation, Graph library, 상세 화면 구성은 바뀔 수
있으며 이런 변경은 가능한 한 `local_web` 안에서 해결한다.

## 9. 진단 결과와 Presentation

최종 진단 결과의 생성 책임은 Reporter와 진단 엔진에 있다.

```text
Diagnosis Engine
       ↓
Reporter
       ↓
Diagnosis Result
       ↓
Presentation Boundary
       ↓
local_web
```

`local_web`은 Reporter 또는 통합 계층이 제공하는 결과를 사용자에게 표현한다.
최종 취약점 상태를 자체적으로 재판정하지 않는다. Reporter 출력과 Web 화면의
구체적인 매핑은 Reporter의 공개 계약이 안정화된 이후 확정한다.

## 10. Knowledge Graph 경계

사용자는 최종적으로 웹 구조와 접근 관계를 시각적으로 확인할 수 있어야 한다.
표시 대상은 다음과 같은 개념을 포함할 수 있다.

```text
User
Role
Page
Action
Endpoint
Parameter
Resource
```

관계 표현은 탐색된 웹 구조, Page와 Endpoint 관계, Role별 접근 관계, Resource
접근 관계, 주요 업무 Flow, 검증된 관계 등을 고려한다. 정확한 Graph 화면
데이터 포맷은 현재 확정하지 않는다.

Frontend가 Neo4j에 직접 접근하거나 Cypher를 생성하는 구조는 금지한다.

```text
금지

Frontend
   ↓
Neo4j 직접 접근
```

```text
목표 경계

Knowledge Graph
      ↓
Integration / Presentation Boundary
      ↓
local_web
```

Knowledge Graph의 저장 방식이나 repository가 바뀌어도 화면 경계가 유지되는
구조를 목표로 한다.

## 11. Local LLM 방향

ABC2LAB은 온프레미스 동작을 목표로 하므로 의미 분석과 시나리오 생성에 필요한
LLM도 사용자 환경 내부에서 실행할 수 있는 방향을 고려한다.

```text
semantic_analyzer        scenario_generator
       ↓                         ↓
   Local LLM                 Local LLM
```

다음 구현은 현재 확정하지 않는다.

- Ollama 또는 llama.cpp
- 특정 모델과 quantization
- inference server와 port
- 특정 API 형식

Ollama 등의 이름은 후보 예시일 뿐 확정 기술이 아니다. 책임 경계는 다음과 같다.

- `local_web`은 LLM을 직접 호출하지 않는다.
- `local_web`은 prompt를 생성하거나 수정하지 않는다.
- 추론은 각 진단 모듈의 LLM adapter가 담당한다.
- Local LLM 구현 변경이 Web 변경으로 이어지지 않아야 한다.

## 12. Safety Policy 경계

실행 허용 여부는 `local_web`, 통합 계층, LLM이 아니라 Safety Policy가 결정한다.

- Web은 승인 필요 상태와 이유를 표시한다.
- Web은 사용자 입력을 합의된 공개 경계로 전달한다.
- Web은 Safety 결과 파일이나 판정값을 직접 수정하지 않는다.
- 승인 입력만으로 `block` 또는 판단불가 상태를 우회하지 않는다.
- 허용 여부와 검증 대상의 일치 확인은 진단 엔진의 책임으로 유지한다.

승인자의 식별, 승인 기록의 진위·만료·범위, 재개 방식은 향후 확정한다. 이 계약이
정해지기 전에는 실제 승인 동작을 구현 완료로 간주하지 않는다.

## 13. 공개 계약과 Presentation 모델

내부 모듈 변경이 Web에 직접 전파되지 않도록 다음 구조를 목표로 한다.

```text
Module Output
     ↓
Integration Boundary
     ↓
Stable UI-facing Model
     ↓
local_web
```

`Stable UI-facing Model`은 필요한 최소 정보만 제공하는 경계를 뜻한다. 현재
단계에서는 그 Schema, 버전, 파일 위치, 전송 형식을 정의하지 않는다.

통합 시 다음 원칙을 지킨다.

- Web에서 모듈 내부 코드와 저장소에 직접 의존하지 않는다.
- 모듈 공개 결과를 임의로 수정하지 않는다.
- 화면 편의를 위해 모듈 Schema를 복제하거나 확장하지 않는다.
- 여러 결과의 조합은 진단 판단과 분리된 통합·Presentation 경계에서 검토한다.
- `completed`, `partial`, `failed`, 미실행, 판단불가의 의미를 보존한다.
- 공개 계약 변경이 정말 필요하면 생산자와 모든 직접 소비자가 별도로 합의한다.

## 14. 기술과 디렉터리 선택

### 14.1 지금 확정하지 않는 기술

다음 항목은 모듈 구현과 통합 경계를 확인한 뒤 결정한다.

```text
Frontend framework
Backend framework
Runner 구현 방식
Process 모델
Web API와 DTO
SSE / WebSocket / polling
Graph visualization library
Secret 관리 방식
Local LLM provider
Web 상태 저장 방식
```

정적 HTML·JavaScript, React·Vue, FastAPI·Uvicorn, SQLite, SSE, subprocess 등은
모두 검토 가능한 초기 후보 예시다. 현재 채택된 기술이나 필수 의존성으로
간주하지 않는다.

### 14.2 디렉터리 구조 초안

현재는 정확한 파일과 폴더를 확정하지 않고 책임 분리만 표현한다.

```text
local_web/
├── frontend/ or UI layer
├── backend/ or interface layer
├── presentation/
└── integration boundary
```

이 구조는 참고용 초안이다. 정확한 디렉터리명, adapter 클래스, 파일 배치,
dependency 선언은 구현 방식을 선택한 뒤 정한다. `local_web`을 `modules/*`의
공통 라이브러리로 만들지 않는다.

## 15. 보안 원칙

구체적인 구현과 무관하게 다음 경계는 유지한다.

### 로컬 접근

- 기본 제품은 사용자 컴퓨터 안에서 동작하는 것을 전제로 한다.
- 외부 네트워크 공개를 기본값으로 삼지 않는다.
- bind 주소, 인증, TLS, Host·Origin 검증은 배포 방식과 함께 확정한다.

### 파일과 결과

- 브라우저가 임의의 로컬 경로나 artifact 경로를 지정하게 하지 않는다.
- 신뢰된 실행 범위 밖 파일을 화면 요청만으로 읽지 않는다.
- 전체 실행 디렉터리를 그대로 정적 공개하지 않는다.
- Evidence와 리포트는 합의된 공개 경계를 통해 필요한 범위만 제공한다.

### 비밀값과 로그

- 비밀번호·쿠키·토큰·인증 헤더를 화면 상태와 결과에 남기지 않는다.
- 요청 전체, process 환경 전체, secret 원문을 로그로 남기지 않는다.
- 사용자에게 보여줄 오류와 내부 진단 로그를 분리한다.
- UI와 통합 event에는 공개 상태와 비밀값 없는 식별 정보만 포함한다.

### 실행 권한

- 사용자가 임의의 Python import, shell command, Cypher를 전달할 수 없게 한다.
- 진단 실행과 상태 변경은 Safety Policy와 모듈 공개 경계를 우회하지 않는다.
- `local_web`은 verifier 요청을 직접 구성하거나 실행하지 않는다.

## 16. 강하게 고정할 것과 느슨하게 둘 것

### 강하게 고정할 경계

```text
local_web은 UI다.

진단 로직은 modules/*가 담당한다.

local_web은 modules/*의 내부 구현에 의존하지 않는다.

modules/*는 local_web에 의존하지 않는다.

UI 요구 때문에 진단 모듈을 직접 변경하지 않는다.

Safety Policy 판단은 Safety Policy가 담당한다.

최종 취약점 결과는 진단 엔진과 Reporter가 담당한다.

비밀정보는 사용자 화면이나 일반 결과물에 노출하지 않는다.
```

### 지금 고정하지 않을 구현

```text
API
DTO
JSON protocol
event 이름
artifact path
Runner 구현
framework
Graph format
secret 전달 방식
Local LLM provider
구체적인 process 구성
```

경계를 강하게 정한다는 것은 역할과 금지 방향을 명확히 한다는 뜻이다. 구현을
느슨하게 둔다는 것은 미확정 인터페이스를 가상의 세부 설계로 먼저 고정하지
않는다는 뜻이다.

## 17. 향후 구체화 순서

모듈 구현이 충분히 진행되면 다음 순서로 설계를 구체화한다.

```text
1. 각 모듈의 실제 공개 인터페이스 확인

2. 실제 producer/consumer artifact 확인

3. 전체 pipeline 실행 흐름 확정

4. Integration Layer 역할 확정

5. UI에 필요한 최소 데이터 정의

6. Web-facing contract 정의

7. 실제 Web API 설계

8. UI 구현
```

구체화할 때는 다음을 실제 산출물과 테스트로 확인한다.

- 모듈별 공개 호출과 제어 응답
- 성공·부분 성공·실패·미실행 처리
- 승인 대기와 재개 책임
- 인증/session context의 소유와 전달 경계
- Knowledge Graph와 Reporter 결과의 최소 화면 모델
- development 평가의 제품 UI 분리 방식
- 비밀정보가 process·저장소·로그를 통과하는 방식
- 전체 pipeline과 테스트 앱의 end-to-end 실행

현재 문서에서 이 항목의 세부 계약을 미리 만들지 않는다.

## 18. 문서 기준

향후 설계와 구현은 다음 질문으로 책임 침범 여부를 확인한다.

1. 이 코드는 사용자 입력과 표현을 담당하는가, 진단 판단을 담당하는가?
2. 모듈 내부 구현 변경이 Web 변경으로 직접 이어지는가?
3. Web 요구 때문에 모듈 Schema나 business logic을 바꾸려 하는가?
4. Safety Policy 또는 Reporter의 판단을 UI가 다시 계산하는가?
5. 비밀정보가 브라우저 상태, event, 로그, 일반 artifact에 남는가?
6. 아직 확인하지 않은 공개 계약을 확정된 것처럼 문서화하고 있는가?

핵심 목표는 다음과 같다.

> 현재 단계에서는 책임 경계만 강하게 고정하고, 구체적인 구현 방식은 모듈
> 구현과 공개 계약을 확인한 이후 결정한다.
