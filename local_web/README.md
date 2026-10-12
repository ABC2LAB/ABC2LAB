# ABC2LAB `local_web` 아키텍처와 통합 검증 MVP

> 상태: MVP 1~6단계 구현 · 산출물 결과·KG·개발 평가 화면 연결 · 7단계 실제 Docker 전체 진단 검증은 미완료
>
> 핵심 원칙: **경계는 강하게, 구현은 느슨하게 유지한다.**

`local_web`은 ABC2LAB의 사용자 인터페이스 계층이다. 이 문서는 현재 단계에서
반드시 지켜야 할 책임 경계와 제품 방향을 정의한다. 구체적인 API, 데이터 구조,
프레임워크 등 미정 사항은 실제 공개 인터페이스를 확인하고 사용자와 합의한 뒤
확정한다. 기존 책임 경계는 유지하며, 이번 통합 검증 MVP의 합의·현재 연결 조건·
구현 단계는 [19절](#19-통합-검증-mvp--2026-10-11)에 구분해 기록한다.

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
- 이번 MVP는 기존 루트 `pipeline.py`를 실행 경계로 사용한다.
- 최종 제품의 Runner 확장과 통합 계층 구조는 별도로 구체화한다.
- Knowledge Graph, 승인 요청, 검증 결과, 최종 리포트를 주요 화면으로 제공한다.

### 향후 모듈 구현 후 확정할 사항

- REST API 경로와 Web-facing DTO 필드
- Runner와 Web 사이의 JSON protocol과 event 이름
- 최종 artifact 경로와 모듈 operation 이름
- Runner, process, service, adapter의 구체적인 구성
- 인증정보와 session context 전달 방식
- Graph visualization 데이터 포맷
- 최종 제품의 Frontend·Backend framework와 상태 저장 방식
- SSE, WebSocket, polling 등 진행 상태 전달 방식
- 최종 제품의 Local LLM provider, 모델, port, API 형식

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

이 흐름은 최종 제품 경험의 방향이다. 이번 MVP는 ABC2LAB도 Docker에서 실행하고
로컬 브라우저로 사용한다. MVP의 ABC2LAB·Neo4j·Ollama 분리 배치는 19.7절에
기록한다. 테스트 대상은 19.10절의 external 네트워크로 연결하고, Collector의
설정 origin 허용 변경(PR #69)을 반영한다. 실제 대상 접속은 7단계에서 검증한다.
최종 제품의 process 구성과 승인·재개 기능이 구현됐다는 의미는 아니다.

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

### MVP의 Runner와 최종 제품 통합 구조

이번 MVP는 이미 구현된 루트 `pipeline.py`의 공개 CLI를 사용한다. Web은 실행
인자와 모듈의 공개 설정을 전달하고 결과를 표시하며, 호출 순서나 진단 로직을
다시 구현하지 않는다. 기존 Runner를 수정하지 않는다.

최종 제품의 통합 계층 확장 방식은 여전히 미정이다. Pipeline Runner, adapter,
별도 process 등의 세부 구성은 각 모듈의 공개 인터페이스를 확인한 뒤 결정한다.

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
최종 제품의 Runner 확장 방식
구체적인 process 모델
Web API와 DTO
SSE / WebSocket / polling
Graph visualization library
Secret 관리 방식
Local LLM provider
Web 상태 저장 방식
```

정적 HTML·JavaScript, React·Vue, FastAPI·Uvicorn, SQLite, SSE, subprocess 등은
모두 검토 가능한 초기 후보 예시다. 최종 제품의 필수 의존성으로 간주하지
않는다. 이번 MVP에 한정해 선택한 표준 라이브러리 서버·정적 화면·Ollama는
19.7절에 구분한다.

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
최종 artifact 배치
최종 제품의 Runner 확장 구현
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

## 19. 통합 검증 MVP — 2026-10-11

### 19.1 목적과 확정된 범위

목적은 테스트 웹으로 전체 모듈의 연결과 실제 산출물을 확인하는 것이다.
완성된 제품이나 모델 성능을 보장하는 단계가 아니다. 아래 항목은 설계 합의이며
Web 기능의 구현 완료를 뜻하지 않는다.

| 항목 | MVP 합의 |
| --- | --- |
| 실행 환경 | ABC2LAB·Neo4j·Ollama는 Compose로 함께 실행하되 별도 컨테이너. 테스트 웹은 별도 실행된 대상 |
| MVP 기술 | Python 표준 라이브러리 Web 서버와 정적 HTML·CSS·JavaScript. Ollama는 CPU 기본, NVIDIA GPU는 선택 설정 |
| 화면 입력 | 대상 URL, 역할별 복수 테스트 계정, 평가 데이터셋 |
| 사전 설정 | 로그인 방식·탐색 제한, Neo4j, 모듈별 LLM, Safety Policy, 평가 matching_profile |
| 추론 방식 | 테스트 전에 각 모듈의 provider·모델을 설정하고 실제 Local LLM 사용. Web에 모델 선택 기능을 넣지 않음 |
| 실행 경계 | 기존 Runner의 development 단일 회차 실행 |
| 결과 표현 | 실행 상태·공개 산출물·KG snapshot·진단 결과 |
| 정답 비교 | Reporter가 계산한 지표·집계만 표시. 정답 항목별 상세 비교는 제외 |
| 수정 범위 | `local_web/**`만. 모듈·Runner·Schema·루트 의존성·루트 Compose·명세는 변경하지 않음 |

URL·로그인 방식·계정·역할·데이터셋을 특정 테스트 웹의 값이나 배열 위치로
하드코딩하지 않는다. 화면의 계정 입력은 기존 Collector 계약의 역할·별칭·
로그인 ID·비밀번호를 수용하며 비밀값은 공개 결과에 포함하지 않는다.

### 19.2 최소 사용자 흐름과 화면

```text
사전 설정과 모델 준비
    → ABC2LAB Docker 실행
    → 로컬 브라우저에서 URL·테스트 계정·데이터셋 입력
    → 기존 Runner 실행
    → 실행 상태와 산출물 확인
    → KG·진단 결과 확인
    → Ground Truth 평가 지표 확인
```

화면은 다음 목적만 구현한다. 현재 새 진단의 입력 준비·기존 Runner 시작과
진행 상태를 연결했다. 분석 결과·성능 평가는 미연결 골격이다. 세부 결과 컴포넌트와
Graph library는 미정이다.

| 화면 목적 | 공개 정보 |
| --- | --- |
| 입력·실행 | URL·계정·데이터셋 입력, 실행 요청 |
| 진행·연결 확인 | 프로세스 상태, 발행된 산출물, 종료 후 Runner의 단계별 요약 |
| 분석 결과 | crawl_result·semantic_analysis 요약, graph_query_result의 structure_snapshot |
| 진단 과정·결과 | vulnerability_candidates·test_scenarios·safety_decisions·verification_results·diagnosis_report |
| 개발 평가 | evaluation_results의 발견율·Precision·Recall·TP/FP/FN·미검증 집계·평가 한계 |

화면은 같은 실행의 공개 결과만 Presentation 계층에서 읽어 변환한다.
Ground Truth 내용은 `reporter.evaluate`만 읽으며 Web에서 매칭이나 평가를
재구현하지 않는다. 데이터셋 선택은 정답을 분석 모듈에 전달한다는 뜻이 아니다.

### 19.3 현재 확인된 공개 설정과 실행 조건

초기 문서 단계의 기준 커밋은 `2e4a689`다. 2026-10-12에는 PR #69 병합
커밋 `11291f2`와 현재 feature 기준 `45b7e30`에서 Collector의 설정 origin
허용 변경을 확인했다. 실제 컨테이너 대상 접속과 전체 진단은 아직 검증하지 않았다.

| 연결 | 현재 공개 설정·조건 |
| --- | --- |
| Collector | `COLLECTOR_CONFIG_PATH`, `COLLECTOR_SECRETS_PATH` 또는 공개 계정 환경변수. URL·역할·계정·로그인 설정 필요 |
| Semantic Analyzer | `SEMANTIC_ANALYZER_CONFIG_PATH`. 커밋된 기본 설정은 fake이므로 실제 모델 설정 필요 |
| Scenario Generator | `SCENARIO_GENERATOR_CONFIG_PATH`. 커밋된 기본 설정은 none이므로 실제 provider·모델 설정 필요 |
| Knowledge Graph | 기존 `NEO4J_*` 설정으로 모듈이 접속. Web은 DB나 Cypher에 직접 접근하지 않음 |
| Safety Policy | 운영자가 작성한 정책 원본을 `SAFETY_POLICY_CONFIG_PATH`로 제공. 모듈이 자기 private 사본 준비 |
| 개발 평가 | Runner에 dataset_id·matching_profile 필요. 정답 경로는 기존 `datasets/<dataset_id>/ground_truth.json` |
| Runner | 대상 URL은 Collector 설정과 동일해야 함. 한 회차 실행 후 stdout 요약 JSON과 종료 코드 제공 |

Web 입력을 공개 설정으로 전달하는 작업과 모듈 내부 상태를 관리하는 작업을
구분한다. Web은 다른 모듈의 private 폴더에 쓰지 않는다. 정책의 origin·계정
범위가 입력과 맞지 않으면 설정 문제로 표시하며 자동 확대하거나 허용 정책을
생성하지 않는다. 비밀값 전달·보관·폐기 구현은 7절·15절의 보안 경계 안에서
별도로 합의한다.

**Collector Docker 접속 조건 — 2026-10-12 갱신:** PR #69에서 loopback 전용
제한을 제거했다. `target_url`의 origin과 운영자가 명시한 `extra_allowed_origins`를
scheme·호스트·포트로 정확히 비교한다. 기존 TOML·설정 환경변수·출력 `0.2.0`은
유지되어 Web 입력 adapter와 호환된다. Web이 허용 범위를 자동 확대하지 않는다.
서비스명·네트워크 alias와 컨테이너 내부 포트로 입력하며, 이전 이미지는 새
Collector를 포함하도록 다시 빌드해야 한다. DNS·실제 접속 성공은 7단계 검증 대상이다.

Collector의 같은 호스트·다른 포트/스킴 리다이렉트 hop은 차단하지 못하고
`REDIRECT_OUT_OF_SCOPE`·`partial`로 기록하는 한계가 있다. Web은 이 부분 결과를
정상 전체 완료로 바꾸지 않는다. 세부 오류 코드의 안전한 표시 보완과 실제 요청
범위 확인은 7단계 점검 대상으로 남긴다. 이 한계를 Web에서 모듈 변경으로 우회하지 않는다.
MVP 내부 Neo4j·Ollama 배치는 19.7절로 구체화한다. Collector 설정·데이터셋의
읽기 전용 마운트와 입력 준비는 19.9절에 기록한다. 테스트 대상 네트워크,
다른 모듈 설정·실행 결과 마운트와 Runner 비밀값 주입은 19.10절로 구체화했다.
실제 Docker 대상 접속과 전체 진단은 운영 설정이 준비된 뒤 검증한다.

### 19.4 결과와 제한 사항의 표현

- 실행 중에는 프로세스 상태와 실제 발행된 산출물을 표시한다. 현재 Runner에는
  구조화된 실시간 event 계약이 없으므로 추정 진행률이나 현재 단계를 확정하지 않는다.
- 종료 후 Runner 요약의 completed·partial·failed·skipped와 종료 코드를 표시한다.
  프로세스 종료를 모든 모듈의 성공으로 바꾸어 표시하지 않는다.
- 미생성·실패·잘못된 결과를 정상 0건으로 숨기지 않는다. 앞 단계 실패로 뒤 결과가
  없으면 미실행 상태로 표시한다.
- 후보·의심·판단불가·정책 차단·승인 대기는 확정 취약점과 구분한다.
- 현재 KG snapshot은 검증 전 query 시점의 결과다. 검증 반영 후 최신 상태라고
  표시하지 않으며 후속 snapshot을 Web이 임의로 생성하지 않는다.
- 평가 지표는 Reporter의 값을 그대로 표시한다. null은 미측정/계산 불가로 표시하고,
  분자·분모·미검증 건수·평가 한계를 함께 보여준다.
- 실제 모델을 사용하더라도 설정 누락·모델 오류를 fake/replay나 가상 성공으로
  자동 대체하지 않는다. 연결 성공과 실제 진단 성능은 별도로 확인한다.

### 19.5 제외 범위와 미정 사항

이번 MVP에는 승인 후 재개·다회차 실행·모듈 선택 실행·Web의 모델 선택·정답
항목별 상세 비교·취소/재개 제어·고급 dashboard·PDF 생성 기능을 넣지 않는다.
승인 대기는 상태와 사유만 표시하며 Safety 판정을 수정하지 않는다.

최종 제품의 framework·process 구성은 확정하지 않는다. MVP Web 골격·내부
서비스·공개 포트는 19.7절로 구체화하고 입력 준비·secret 임시 보관은 19.9절에
기록한다. MVP 실행용 API·polling·대상 네트워크·모듈 설정·Runner secret 전달은
19.10절에 한정한 구현이다. 최종 Web 계약·Graph 형식·제품 process 구성은 미정이다.
공개 계약을 벗어나는 요구는 해당 담당자와 협의한다.

### 19.6 구현 단계와 완료 기준

| 단계 | 작업 | 완료 기준 |
| --- | --- | --- |
| 1 | README에 MVP 합의·책임 경계·연결 조건 기록 | 문서 승인·반영. 미완료 외부 조건은 별도 표시 |
| 2 | Docker 실행 구성과 최소 Web 골격 | 합의된 배치로 컨테이너 실행 후 로컬 브라우저 접속 |
| 3 | URL·복수 계정·데이터셋 입력 연결 | 기존 공개 설정으로 전달하고 비밀값 노출 방지 |
| 4 | 기존 Runner 실행과 상태 표시 | 실행 여부·발행된 산출물·종료 후 단계별 결과 확인 |
| 5 | 산출물 기반 결과 화면 | 수집 요약·KG snapshot·후보·정책·검증·리포트 표시 |
| 6 | Reporter 평가 화면 | 평가 지표·분자/분모·미검증·미측정 상태 표시 |
| 7 | 실제 테스트 웹 통합 검증 | 정상 및 로그인·설정·중간 실패 확인, 결과와 한계를 이력에 기록 |

2단계 Web·Docker 골격, 3단계 입력 준비, 4단계 Runner 연결, 5단계 공개 산출물
결과 화면, 6단계 Reporter 평가 화면을 구현했다. 7단계 실제 전체 진단 검증은
미완료다. 독립 검증 결과와 한계는 19.8~19.12절에 기록한다.
Docker 접속 조건과 모듈별 실제 모델·정책 설정이 준비되지 않으면 실제 전체
실행을 완료로 기록하지 않는다.

### 19.7 2단계 실행 골격 — 2026-10-12

이번 MVP의 선택이며 최종 제품 기술을 고정하지 않는다.

| 책임 | 2단계 구성 | 준비 완료의 의미 |
| --- | --- | --- |
| ABC2LAB 컨테이너 | CPython 3.12.13, 기존 잠금 의존성·Chromium·Runner·모듈, 표준 라이브러리 Web 서버 | 화면과 Web 자체 상태 응답만. 진단은 아직 실행하지 않음 |
| Neo4j 컨테이너 | 기존 팀 버전인 5.26.31-community, 프로젝트 전용 data volume | healthcheck는 Bolt TCP 수신 확인. KG 인증·질의 성공을 보장하지 않음 |
| Ollama 컨테이너 | 공식 이미지의 검증한 digest, 프로젝트 전용 모델 volume | healthcheck는 서버 응답만. 지정 모델 준비·추론 성공을 보장하지 않음 |
| GPU 선택 설정 | 별도 NVIDIA GPU Compose override | 기본 CPU 실행에는 적용하지 않음. host driver·Container Toolkit·Docker GPU 연결은 별도 준비 |
| 테스트 웹 | 별도 컨테이너 | 본 Compose에서 시작하거나 설정하지 않음. Collector 연결 조건은 19.3절 유지 |

Web은 Neo4j·Ollama를 직접 호출하지 않는다. 내부 모듈 연결 주소는 Neo4j
`bolt://neo4j:7687`, Ollama `http://ollama:11434`이며 모듈 공개 설정으로 전달한다.
LLM provider·model_id 설정과 모델 준비는 운영자가 테스트 전에 수행한다.
자동 모델 다운로드·모델 선택·fake/replay 대체는 제공하지 않는다.

Web은 기본 `127.0.0.1:8080`에만 공개하며 `LOCAL_WEB_PORT`로 호스트 포트를
변경할 수 있다. Neo4j·Ollama의 호스트 포트는 공개하지 않는다. Docker socket,
host network, privileged 실행을 사용하지 않는다. 기존 루트 Compose와 다른
프로젝트 및 volume을 사용하므로 기존 DB와 테스트 앱을 재사용·초기화하지 않는다.

ABC2LAB은 비 root 사용자와 읽기 전용 root filesystem으로 실행한다.
Dockerfile별 ignore는 코드·Schema·공개 기본 설정·잠금 파일·Web 정적 파일만
빌드에 포함하며 실제 `.env`·실행 결과·개인 기록·Ground Truth는 포함하지 않는다.
모듈 기본 설정을 이미지에 포함했다는 사실은 실제 모델 설정이 준비됐다는 뜻이 아니다.
2단계에서 미정이었던 Collector 설정·데이터셋 마운트는 19.9절로 구체화했다.
다른 모듈 설정·실행 결과 마운트는 19.10절에 추가했다.

#### 실행

저장소 루트에서 실행한다. `.env.example`을 참고해 커밋하지 않는
`local_web/.env`를 직접 준비하고 Neo4j 비밀번호, Collector TOML·datasets·
모듈 설정·결과 폴더의 호스트 절대 경로와 대상 네트워크를 설정한다(19.9~19.10절).
실제 비밀번호는 커맨드 인자·공유 로그에
출력하지 않는다. 테스트 계정 비밀값은 이 파일이 아니라 Web 입력으로 받는다.

```bash
docker compose --env-file local_web/.env -f local_web/compose.yml up -d --build
docker compose --env-file local_web/.env -f local_web/compose.yml ps
```

기본 브라우저 주소는 `http://127.0.0.1:8080`이다. 기본 포트 변경 시 변경한 주소를
사용한다. GPU를 사용할 때만 기본 파일에 override를 추가한다.

```bash
docker compose --env-file local_web/.env -f local_web/compose.yml -f local_web/compose.gpu.yml up -d --build
```

이미지·기존 의존성·브라우저 설치에는 초기 네트워크 준비가 필요하다.
폐쇄망 최종 배포의 이미지·모델 사전 반입 방식은 이 단계에서 구현하지 않는다.
모델은 모듈 담당자가 정한 것으로 별도 준비하며, 모델이 없는 Ollama도
Web 골격 확인에는 사용할 수 있다. 이것을 전체 진단 준비 완료로 표시하지 않는다.

#### 제공·제외·검증 경계

- `/`·`/style.css`·`/app.js`는 명시적으로 허용한 정적 화면만 제공한다.
- `GET/HEAD /health`는 Web만 확인하며 `scope=web_only`·`diagnosis_enabled=false`를 반환한다.
  이 경로·필드는 MVP의 임시 구현이지 모듈 또는 최종 Web 계약이 아니다.
- 임의 파일 경로·디렉터리 목록·설정·private·artifact·GT 원문은 제공하지 않는다.
  Host·Origin·정적 경로를 검사하고 요청 원문을 로그·오류에 반영하지 않는다.
- 2단계에서는 입력·실행 버튼을 비활성화했고 3단계는 입력 준비만 활성화했다.
  4단계는 준비 입력과 Runner 연결 설정을 확인한 뒤 시작 버튼을 활성화한다.
  가상 KG·취약점·집계·진행률을 만들지 않는다.
- 승인 후 재개·계정 영구 저장·Web의 LLM 직접 호출은 제공하지 않는다.
  Runner 시작·산출물 존재 조회는 19.10절에 추가했고 본문 표시는 후속 단계다.
- Python `http.server` 기반 서버는 개발 프로토타입용이다([공식 문서](https://docs.python.org/3.12/library/http.server.html)). 최종 제품 배포 서버나
  인증·세션·CSRF·요청 크기/동시성 제한의 완성 구현으로 간주하지 않는다.
- 독립 테스트는 `local_web/tests`를 명시해 실행한다. 기존 루트 pytest 설정은 변경하지 않는다.
  Compose 검증은 CPU 기본과 GPU override 병합을 구분하고, 실제 GPU 추론과
  전체 진단 실행은 해당 환경·모델·모듈 설정이 준비된 후 별도로 검증한다.

### 19.8 2단계 검증 결과 — 2026-10-12

| 검증 | 실제 결과 | 범위·한계 |
| --- | --- | --- |
| Web 독립·기존 Runner 회귀 | `.venv/bin/python -m pytest local_web/tests tests/pipeline -q --tb=short`: **94 passed**(Web 36 + Runner 58) | Runner는 기존 대역 기반 회귀. 실제 전체 진단 실행이 아님 |
| Compose 설정 | CPU 기본과 GPU override 병합 설정 검사 모두 exit 0 | 실제 GPU 장치 연결·추론은 미검증 |
| Docker 이미지 | 빌드 성공, CPython 3.12.13·UID 10001·8개 공개 entrypoint import·Chromium 기동 확인 | 실제 모듈 operation은 호출하지 않음. 실제 `.env`·Collector 설정·GT·모듈 테스트·개인 기록 제외 확인 |
| CPU Compose | ABC2LAB·Neo4j·Ollama 3개 서비스 running·healthy | 각 healthcheck 범위만 확인. 모델 준비·추론·KG 인증/질의 성공을 의미하지 않음 |
| 호스트 접속 | `127.0.0.1:8080`의 health와 정적 화면 3개 경로 모두 HTTP 200 | 임시 검증 컨테이너에서 확인한 결과. 현재 상시 실행 중이라는 뜻이 아님 |
| Chromium 화면 | 1280×800에서 메뉴 4개 전환, 390×844에서 가로 넘침 없음, JavaScript 오류 0건 | 컨테이너 Chromium의 기능·레이아웃 검사. 사용자의 실제 브라우저 시각 검수는 별도 |

검증용 Compose 프로젝트는 `abc2lab-local-web-stage2-test`를 사용했다.
DB 비밀번호는 메모리에서 생성해 자식 프로세스 환경으로만 전달했으며 저장소나
공유 출력에 남기지 않았다. 기존 테스트 앱·DB는 변경하거나 초기화하지 않았다.
검증용 Compose 컨테이너와 추가 호스트 접속 검사 컨테이너는 검사 후 제거했다.
새로 생성한 `abc2lab-local-web-stage2-test_neo4j_data`와
`abc2lab-local-web-stage2-test_ollama_data` volume은 삭제하지 않고 보존했다.

실제 LLM 모델 다운로드·추론, 테스트 대상 수집·검증, 전체 pipeline 진단,
Ground Truth 평가와 GPU 실행은 이번 단계에서 수행하지 않았다.
당시 다음 작업은 3단계 URL·역할별 복수 계정·평가 데이터셋 입력 연결이었다.
현재 3단계 상태는 아래에 기록한다.

### 19.9 3단계 입력 준비 — 2026-10-12

**구현 상태:** 입력 준비만 연결했다. Runner·Collector·LLM·Neo4j를 호출하지
않으며 실제 수집·진단·평가는 실행하지 않는다. 입력 준비 성공은 로그인·모델·
KG 연결 성공이나 전체 진단 준비 완료를 뜻하지 않는다.

#### 읽기 전용 원본과 화면 입력

`local_web/.env`에 아래 호스트 절대 경로를 지정한다. 실제 파일·디렉터리는
운영자가 미리 준비하고, 컨테이너 사용자 UID 10001이 읽을 수 있는 권한을 부여한다.
경로가 없으면 Compose가 새 디렉터리를 자동 생성하지 않고 실행을 거절한다.

| 호스트 설정 | 컨테이너 경로 | 용도 |
| --- | --- | --- |
| `LOCAL_WEB_COLLECTOR_CONFIG_PATH` | `/config/collector.toml` | 비밀값 없는 Collector TOML. 읽기 전용 |
| `LOCAL_WEB_DATASETS_DIR` | `/app/datasets` | `<dataset_id>/ground_truth.json` 위치. 읽기 전용 |

Web 서버에는 각각 `LOCAL_WEB_COLLECTOR_TEMPLATE_PATH`·`LOCAL_WEB_DATASETS_PATH`로
전달한다. 테스트 계정 로그인 ID·비밀번호를 원본 TOML이나 이미지에 넣지 않는다.
모듈 기본 설정 파일을 덮어쓰거나 모듈의 private 폴더에 쓰지 않는다.

화면은 대상 URL, 역할·공개 별칭·로그인 ID·비밀번호의 복수 계정, 데이터셋을 받는다.
역할·별칭의 초기 제안은 원본 TOML에서 가져오며 추가·삭제·수정할 수 있다.
같은 역할의 복수 계정을 허용하고, guest는 입력하지 않아도 Collector가 자동 추가한다.
별칭은 로그인 ID와 달라야 한다. 역할·계정 수·URL을 특정 테스트 웹에 고정하지 않는다.

로그인 ID 입력칸은 입력값을 확인할 수 있도록 평문으로 표시하고, 비밀번호만
마스킹한다. 이메일·계정 식별자를 받을 수 있도록 로그인 ID에는 역할·별칭의
소문자 식별자 규칙을 적용하지 않으며 기존 4096자 제한을 유지한다.
이 표시 변경은 로그인 ID를 공개 결과·API 응답·로그·브라우저 저장소에 남겨도
된다는 뜻이 아니다. 요청 완료·페이지 이탈 시 로그인 ID와 비밀번호를 함께 비운다.

MVP 대상 URL은 http(s)와 경로만 받으며 userinfo·query·fragment는 거절한다.
이는 비밀값 유입을 제한하기 위한 Web 입력 제한이다. 실제 origin·실행 허용·
인증·계정 대응의 최종 검증은 Collector·Safety Policy 등 해당 모듈에 남긴다.

데이터셋 목록은 파일 이름·존재 여부만 확인한다. 데이터셋 폴더 또는 GT 파일의
symlink와 경로 탈출 입력은 거절한다. GT 내용·버전·Schema·해시는 Web에서
읽거나 계산하지 않으며, 실제 평가 시 기존 Runner·Reporter가 처리한다.

#### 임시 설정과 비밀값 수명

`inputs.py`는 원본 TOML의 로그인·탐색 제한·추가 허용 origin·상태 변경 설정을
유지한다. URL·역할·계정 별칭만 화면 입력으로 바꾼 비밀값 없는 TOML과 빈
`.env`를 Web 소유 임시 디렉터리에 준비한다(디렉터리 0700, 파일 0600).
원본은 불변이며 Safety Policy 범위나 상태 변경 허용을 자동 확대하지 않는다.

로그인 ID·비밀번호는 브라우저별 서버 메모리에만 보관한다. 준비 응답은
준비 상태·불투명 ID·계정 수·남은 시간만 반환하고 인증값을 포함하지 않는다.
화면 입력칸은 요청 완료·페이지 이탈 시 비우며 localStorage·sessionStorage·
URL·일반 파일·로그에 저장하지 않는다.

입력 세션 생성 후 최대 15분 동안 보관한다. 준비 취소·교체·세션 만료·서버 종료
시 비밀값 참조와 Web 임시 설정을 폐기한다. 서버 재시작 후에는 재입력이 필요하다.
Python·브라우저 문자열의 물리적 메모리 소거 또는 OS 메모리 보호를 보장하는
구현은 아니며, 운영 환경 보호와 제품용 secret 관리는 별도 고도화 대상이다.

4단계 연결용 adapter는 `COLLECTOR_CONFIG_PATH`와 `COLLECTOR_ACCOUNT_*`의
자식 프로세스 환경을 구성할 수 있다. 기존 계정 환경변수와 secret 파일 경로는
제외하고 빈 `.env`를 지정해 다른 실행의 비밀값이 섞이지 않게 한다. 현재는
이 환경으로 프로세스를 실행하지 않는다. 준비를 인계받은 호출자는 사용 후
임시 설정을 정리해야 한다.

#### Web 경계와 검증

입력 준비 경로는 MVP 내부 예시 계약이며 최종 Web API·DTO로 고정하지 않는다.
Host·Origin·Fetch Metadata·브라우저별 HttpOnly/SameSite cookie·CSRF token을
검사하고 JSON의 중복 키·비유한 수·잘못된 타입·크기를 거절한다. 요청·오류 원문,
인증값·내부 경로를 화면 응답과 요청 로그에 반영하지 않는다.

요청 64 KiB, 계정 64개, 동시 입력 세션 16개의 제한은 Web 메모리 보호용이다.
모듈의 계정 수 계약이나 결과 배열 길이를 바꾸는 것이 아니다. 제품용 인증·
다중 사용자 권한·rate limit·TLS 등 전체 보안 구현이 완료됐다는 뜻은 아니다.

```bash
.venv/bin/python -m pytest local_web/tests tests/pipeline -q --tb=short
node --check local_web/frontend/app.js
```

- Web 100건·기존 Runner 58건, **158 passed · 2 skipped**. skipped 2건은 Chromium·
  읽기 전용 Docker 마운트 검증이며 해당 Docker 환경에서 별도 실행해 **2 passed**.
- Docker 이미지 빌드, UID 10001·읽기 전용 root filesystem에서 설정 준비·원본
  보존·GT 비열람 확인. CPU 기본/GPU override 모두 읽기 전용 입력 마운트 2개 검증.
- 실제 Chromium에서 계정 추가·동일 역할 3계정 입력·준비·취소·비밀값 입력칸
  초기화·브라우저 저장소 비사용·메뉴 4개·390px 모바일 폭·JavaScript 오류 0건 확인.
- 테스트 컨테이너는 종료 후 제거했다. 기존 DB·모델 volume·테스트 웹은 변경하지 않았다.
- 실제 계정 로그인·LLM 추론·진단·Ground Truth 평가는 미실행이다.

**3단계 완료 당시 다음 작업:** 4단계 기존 Runner 실행과 상태 표시. Collector의 Docker 대상 접속
설정, 대상 네트워크, 실제 모듈별 모델·Safety 설정, 실행 결과 저장소를 먼저
확인한 뒤 준비 입력을 자식 프로세스로 전달한다. 모듈·Runner·Schema는 변경하지 않는다.

### 19.10 4단계 Runner 연결 — 2026-10-12

**현재 구현:** `runner.py`는 기존 `pipeline.py` CLI를 별도 자식 프로세스로
실행한다. 공개 CLI 인자·종료 요약·발행 경로만 연결하며 모듈 내부를 import하지
않는다. 호출 순서·Safety 판정·검증·Graph 갱신·Reporter 평가를 재구현하지 않는다.
MVP의 임시 API·표현 모델이며 최종 Web-facing 계약이나 버전을 고정하지 않는다.

#### 확정한 배치·사전 설정

| 호스트 설정 | 컨테이너 연결 | 역할 |
| --- | --- | --- |
| `LOCAL_WEB_TARGET_NETWORK` | 기존 external Docker network | ABC2LAB만 대상 네트워크에 추가 참여. Neo4j·Ollama는 내부 기본 네트워크만 사용 |
| `LOCAL_WEB_RUNS_DIR` | `/app/runs` · 쓰기 가능 | 운영자가 미리 만든 호스트 결과 폴더. 완료 파일과 모듈 private를 보존 |
| `LOCAL_WEB_SEMANTIC_CONFIG_PATH` | `/config/semantic_analyzer.toml` · 읽기 전용 | `SEMANTIC_ANALYZER_CONFIG_PATH`로 모듈에 전달 |
| `LOCAL_WEB_SCENARIO_CONFIG_PATH` | `/config/scenario_generator.toml` · 읽기 전용 | `SCENARIO_GENERATOR_CONFIG_PATH`로 모듈에 전달 |
| `LOCAL_WEB_POLICY_CONFIG_PATH` | `/config/policy.json` · 읽기 전용 | `SAFETY_POLICY_CONFIG_PATH`로 모듈에 전달. 정책 내용·private 배치는 Safety Policy 소유 |
| `LOCAL_WEB_MATCHING_PROFILE` | CLI `--matching-profile` | 현재 Reporter 공개 profile `default-v1` 기본. Web은 매칭을 수행하지 않음 |

Collector 설정·datasets까지 포함해 읽기 전용 bind 5개와 결과 bind 1개다.
모든 bind는 `create_host_path=false`이므로 누락된 파일/폴더를 자동 생성하지 않는다.
대상 네트워크는 이미 실행한 테스트 웹에서 사용 중인 **실제 이름**을 지정한다.
Web이 네트워크를 만들거나 대상 컨테이너를 변경하지 않는다. 루트 Compose도
변경하지 않으며 대상 URL·origin을 다른 주소로 자동 치환하거나 허용 범위를 넓히지 않는다.

결과 폴더는 UID 10001이 쓸 수 있고 그룹·다른 사용자 쓰기가 금지된 디렉터리여야
한다(권장 mode `0700`). 설정 파일은 해당 UID가 읽을 수 있어야 한다. 운영자가
소유권·권한을 준비하며 Web이 기존 디렉터리를 chmod/chown하거나 결과를 삭제하지
않는다. Runner 자식은 umask `0077`로 시작한다. 호스트 결과에는 모듈의 private도
포함되므로 폴더 전체를 웹서버·공유 저장소에 공개하지 않는다.

기존 대상 네트워크의 다른 `neo4j`와 혼동하지 않도록 내부 DB는 전용 network alias
`local-web-neo4j`로 접속한다. 두 LLM TOML은 `[llm]`의 `provider="ollama"`, 실제
`model_id`, 내부 주소 `base_url="http://ollama:11434"`로 준비한다. Web은 공개 설정의
최소 provider·모델 지정과 파일/저장소 접근만 확인한다. 전체 설정·모델의 존재·
서비스 readiness·추론 성공·정책 유효성은 해당 모듈이 실행할 때 확인한다.
fake·none·replay 또는 빈 모델 설정으로 실제 모델 진단이 된 것처럼 시작하지 않는다.
모델 다운로드·대상 DB 초기화·허용 정책 생성은 자동 수행하지 않는다.

#### 실행·소유권·비밀값 수명

```text
입력 준비 (최대 15분)
    → Origin·입력 cookie·CSRF·preparation_id 확인
    → 동시에 1건 / 설정·저장소 확인
    → 준비 입력 한 번 소비
    → 기존 Runner CLI (development, iteration 0)
    → Collector 임시 TOML + 계정 환경변수
    → 프로세스·공개 파일 존재 조회
    → 종료 요약 검증·표현
    → 임시 설정 폐기 (완료·실패·서버 종료)
```

Runner는 현재 공통 Python으로 `shell=False` 실행하며 고정 CLI에 URL·dataset_id·
profile·새 난수 run_id·신뢰된 경로만 전달한다. 비밀번호·로그인 ID는 인자가 아닌
자식 환경에만 넣는다. 기존 계정 환경변수와 `.env` fallback은 제거하고 부모의
계정 참조·전달용 환경 dict는 자식 생성 직후 폐기한다. Collector 임시 TOML은
세션 재사용 창구가 끝날 때까지 유지하고 모든 종료 경로에서 정리한다.
Python/브라우저 문자열의 물리적 zeroization을 보장한다는 뜻은 아니다.

입력 cookie의 15분 수명과 별도의 HttpOnly·SameSite=Strict run cookie로 실행
소유권을 분리했다. 입력 만료·준비 취소·브라우저 새로고침으로 실행을 중단하지
않으며 같은 브라우저는 자기 최근 실행만 조회한다. 다른 브라우저에는 실행 중
여부만 제공하고 run_id·결과·경로·계정은 공개하지 않는다. URL이나 브라우저 저장소에
소유 token을 넣지 않는다. Web 인메모리 소유 기록은 최대 16개이며 같은 브라우저의
다음 실행은 최근 기록을 교체한다. 이 한도는 Web 자원 제한이지 모듈 계약이 아니다.

Web 종료·SIGTERM은 자신이 생성한 process group을 종료하고 필요 시 제한 시간 후
강제 종료한다. 진단 취소/재개 메뉴를 추가했다는 뜻은 아니다. 호스트 산출물은
보존하지만 Web 재시작 후 실행 상태·소유권 자동 복구와 과거 실행 목록은 미구현이다.
실행 중에는 컨테이너를 재시작하지 않는다.

#### 진행 화면·공개 계약 보호

- MVP 내부 경로: `POST /api/runs`는 준비 입력을 소비해 시작하고,
  `GET/HEAD /api/run-status`는 run cookie 소유 실행 상태만 반환한다.
  실행 요청은 기존 Origin·CSRF·JSON 타입/크기 검사를 그대로 적용한다.
- 실행 중에는 프로세스 상태·경과 시간·고정 공개 산출물 10종의 **파일 존재**만
  조회한다. JSON 본문·GT·private·근거·로그는 읽지 않는다. Symlink 경로를 거절한다.
  파일 존재는 원자적 발행 위치 확인이며 유효한 Schema/성공 판정이라는 뜻은 아니다.
- 실행 중 상태는 2초 간격 polling으로 갱신한다. 현재 단계나 진단 진행률을 추정하지
  않는다. 종료 후에만 현재 Runner의 12단계 이름·상태·오류 건수를 표시한다.
- stdout은 최대 1 MiB로 제한해 읽고 나머지도 배출해 pipe 교착을 막는다. run_id·
  mode·iteration·경로·단계·상태·종료 코드의 대응을 검증한다. 로그가 섞이거나
  요약이 없고/잘못됐으면 `failed`·요약 확인 불가로 표시하며 정상 완료로 대체하지 않는다.
- 원문 stderr는 전달·저장하지 않는다. 요약의 임의 문자열인 detail·error_codes·
  control·내부 report 경로도 API/화면으로 옮기지 않는다. 단계 오류 건수와 고정 Web
  오류 코드만 제공한다. 모듈 자체 로그의 생성·마스킹은 계속 해당 모듈 책임이다.
- `completed`는 Runner의 공개 완료 상태이지 취약점 없음·모든 시나리오 실행을
  뜻하지 않는다. partial·failed·skipped를 구분하며 판단불가를 0건으로 숨기지 않는다.

#### 검증·외부 미완료 조건

```bash
.venv/bin/python -m pytest local_web/tests tests/pipeline -q --tb=short
node --check local_web/frontend/app.js
```

| 검증 | 실제 결과 | 한계 |
| --- | --- | --- |
| Web·기존 Runner 회귀 | **228 passed · 5 skipped** (Web 170 + 기존 Runner 58) | 기존 Runner 및 새 CLI 대역 기반. 실제 전체 진단이 아님 |
| 읽기 전용 Docker | Web 전체 **175 passed**, UID 10001·root filesystem 읽기 전용 | skipped 5개를 포함해 실행. 실제 Collector 원본·datasets 마운트 검사는 파일 존재·설정만 사용 |
| Chromium | 입력 준비·시작·실행 상태·산출물 존재·completed/partial/failed·12단계 요약, 새로고침·입력 만료 후 조회, 비밀값 비노출, 390px 가로 넘침 없음·JS 오류 0건 | 테스트 CLI 대역만 사용. 사용자 브라우저 검수·실제 모델 진단은 별도 |
| 프로세스·경계 | 한 번 소비·동시 1건·다른 브라우저 정보 분리·잘못된/누락/초과 요약·시작 실패·종료 신호 무시·임시 설정 정리·run/artifact 권한 확인 | 모듈 business logic·실제 세션/취약점 판정 검증을 대체하지 않음 |
| Compose | CPU/GPU 설정 검사 성공. 읽기 전용 5개·결과 쓰기 1개·host path 자동 생성 금지·ABC2LAB만 external target 참여·localhost 포트 확인 | 새 운영용 bind와 실제 대상 네트워크 연결·GPU 추론은 미실행 |
| 빌드·정적 검사 | `abc2lab-local-web:stage4` 빌드·JavaScript syntax·`git diff --check` 성공 | 전체 Compose 진단 실행 성공을 의미하지 않음 |

검증용 컨테이너는 종료 후 제거했고 기존 DB·모델 volume은 보존했다.
프로세스 대역은 Web 테스트 fixture에서만
사용하며 운영 이미지에는 넣지 않는다. 실제 실행에서 대역·fake/replay로 대체하는
경로는 없다. Docker 테스트도 기존 DB·모델 volume·대상 테스트 웹을 변경하지 않는다.

4단계 완료 당시에는 Collector의 loopback 제한이 남아 있었다. 이후 PR #69에서
설정 origin 허용으로 변경됐다(19.3절). 실제 대상 네트워크·계정·모듈별 모델·Safety
원본·저장소 권한이 준비되면 실제 전체 진단을 검증해야 한다. 4단계의 완료는
Web 실행 연결·경계 검증이며
실제 대상 로그인·LLM 추론·Neo4j 질의·Ground Truth 평가의 성공을 의미하지 않는다.

**다음 5단계:** 공개 산출물의 수집 요약·KG snapshot·후보·정책·검증·Reporter
결과를 화면으로 표현한다. 판단·Schema·산출물 자체는 변경하지 않는다.

### 19.11 5단계 공개 산출물 결과 화면 — 2026-10-12

#### 현재 화면과 책임

A안인 **기본 SVG 그래프와 노드·관계 표**를 구현했다. 기존 정적 UI를 확장하며
Graph library·새 framework·의존성을 추가하지 않았다. 이 단계의 화면 모델과
조회 경로는 MVP 내부 구현이며 최종 Web API·DTO·Graph 형식을 확정하지 않는다.

| 화면 | 사용하는 공개 결과 | 표현 범위 |
| --- | --- | --- |
| 수집 요약 | `crawl_result` | 역할·계정·페이지·행동·관찰 요청 건수. 인증정보·URL 원문·응답 본문은 옮기지 않음 |
| 의미 분석 요약 | `semantic_analysis` | 정규화 요청·노드·관계·업무 흐름 및 노드 종류별 건수 |
| Knowledge Graph | `graph_query_result`의 `structure_snapshot` | 실제 node_id·관계 ID·source_id/target_id·종류·basis·Resource type/instance·업무 흐름. 다른 질의 결과의 배열 위치를 snapshot으로 가정하지 않음 |
| 진단 과정 | 후보·시나리오·Safety·검증·Reporter | candidate_id·scenario_id·decision_id·verification_id로 연결. 계획 단계는 명시된 order로 정렬. 정책 판정·실행 상태·재현 결과를 구분하고 Reporter의 분류별 집계·확정 외 상태를 별도 표시 |
| 승인 대기 | `safety_decisions` | require_approval만 별도 메뉴에 표시. 시나리오·후보 ID와 사유. 승인·정책 수정·재개 기능 없음 |
| 결과 리포트 | `diagnosis_report` | Reporter가 confirmed로 분류한 항목만 목록 표시. 관련 ID·설명·마스킹된 근거 참조·진단 한계 |

메뉴는 새 진단·진단 진행·분석 결과·승인 대기·성능 평가의 5개다. 5단계 완료 당시
성능 평가는 안내 화면이었으며 6단계에서 연결했다(19.12절). 후보를 확정 취약점으로
바꾸거나 verifier의 success를
직접 confirmed로 승격하지 않는다. 미실행·판단불가·승인 대기를 취약점 없음으로
합치지 않는다. Reporter의 집계와 실제 항목 분류가 불일치하면 조회 오류로 표시하고,
Web이 판정·집계를 덮어써 보정하지 않는다.

KG는 **검증 전 query 시점**의 snapshot이며 검증 반영 후 최신 그래프가 아니다.
snapshot 조회 실패·누락·복수 snapshot은 빈 그래프나 임의 첫 결과로 대체하지 않는다.
현재 화면은 단일 snapshot만 지원한다. Neo4j 재조회·Cypher·그래프 생성·revision
변경·LLM 호출은 수행하지 않는다. 업무 흐름은 기존 snapshot의 명시된 order를
표현할 뿐 새 업무 규칙을 추론하지 않는다.

#### 공개 파일 조회 경계

- 5단계에는 `local_web/artifacts.py`에서 위 **8종** 공개 JSON만 고정 경로로 읽어 최소 화면
  모델로 변환한다. `graph_query`·`evaluation_results`는 4단계의 파일 존재 확인만
  유지한다. Ground Truth 내용·private·로그·근거 본문·HTML 리포트 파일을 읽지 않는다.
- MVP 내부 `GET/HEAD /api/run-results`는 HttpOnly run cookie로 **자기 최근 실행**만
  조회한다. 다른 브라우저에는 실행 결과·ID를 공개하지 않는다. 요청의 run_id·파일
  경로·query parameter로 조회 대상을 선택하는 기능이나 임의 파일 다운로드는 없다.
- 절대 경로의 각 디렉터리·파일을 `dir_fd`와 `O_NOFOLLOW`로 열어 symlink 경로와
  교체를 따라가지 않는다. 일반 파일만 읽고 hardlink·FIFO·루트 밖 경로를 거절한다.
- JSON 중복 키·NaN/Infinity·비유한 수·잘못된 인코딩을 거절하고 envelope의 버전
  0.2.0·run_id·development·iteration 0·작성자·종류·필수 필드·상태/data/errors 대응을
  확인한다. 표시 필드의 타입·enum·ID 중복과 그래프 끝점도 확인한다. 이것은 **Web의
  최소 표시 검증**이며 각 모듈의 전체 JSON Schema·참조·의미 검증을 대신하지 않는다.
- 이미 읽은 공개 입력과 input_refs의 ID·회차·고정 경로·실제 바이트 hash를 대조한다.
  Safety·Verifier의 scenarios_sha256도 읽은 시나리오 바이트와 대조한다. 원문 오류
  message·input_refs·내부 경로는 API에 전달하지 않는다.
- 실행 중에는 최종 원자 발행 경로의 파일과 envelope를 확인해 부분 조회한다.
  Runner의 최종 완료 통지·해시가 아직 미확정임을 표시한다. 종료 후에는 검증한
  Runner 요약에 실제 제공된 파일 hash와 다시 대조하며 파일 변경을 거절한다.
  파일을 발견한 사실을 전체 진단 완료나 성능 검증 완료로 해석하지 않는다.
- 미발행·failed·invalid·partial·completed를 구분한다. 실패·잘못된 파일의 view는
  null이며 정상 0건으로 바꾸지 않는다. 유효한 빈 결과만 0건으로 표시한다.
- properties·match_key·세션 참조·파라미터 값·인증 헤더·쿠키·요청/응답 본문을
  화면으로 옮기지 않는다. 공개 사유·설명은 생산자의 마스킹 계약에 따르며, HTML로
  실행하지 않고 textContent로 표시한다. Web이 생산자의 비밀값 제거 책임을 대체하는
  것은 아니다. 근거는 redacted=true 참조의 ID·종류·hash만 표시하고 **근거 파일의
  존재·해시 일치·본문을 확인했다고 주장하지 않는다.**

조회 중 전체 실행 lock을 잡지 않는다. 결과 메뉴에서 실행 중에는 기존 상태
polling에 맞춰 조회하고, 종료 후에는 메뉴 진입·새로고침·결과 다시 확인으로
갱신한다. 오래된 요청 응답이 새 실행 화면을 덮어쓰지 않도록 요청 순서를 확인한다.
Web 재시작 후 소유권 복구·과거 실행 조회는 계속 제외한다.

#### MVP 표시 제한

파일당 최대 8 MiB, 표시 검증에 사용하는 배열당 최대 2,000개, 표시 문자열당
최대 8,192자다. 초과하면 고정 조회 오류를 표시하며 정상 결과로 대체하지 않는다.
이는 Web 자원 제한이지 진단 모듈 Schema의 새 제한이 아니다.

SVG는 최대 80개 노드와 그 노드 사이 최대 240개 관계, 표는 최대 100개 행을
표시한다. 전체 건수와 표시 건수·한도를 함께 공개한다. 도식에서 잘린 노드·관계를
없어진 데이터로 처리하지 않는다. 대규모 그래프의 페이지 이동·검색·확대/이동·
배치 알고리즘 고도화·근거 파일 열람은 이번 단계에 추가하지 않았다.

#### 검증 결과와 남은 범위

```bash
.venv/bin/python -m pytest local_web/tests tests/pipeline -q --tb=short
node --check local_web/frontend/app.js
node --check local_web/frontend/results.js
```

| 검증 | 실제 결과 | 한계 |
| --- | --- | --- |
| Web·기존 Runner 회귀 | **284 passed · 8 skipped** (Web 226 + 기존 Runner 58) | 공개 fixture·CLI 대역. 실제 전체 진단 아님 |
| 읽기 전용 Docker | Web 전체 **234 passed**, UID 10001·root filesystem 읽기 전용 | 선택 검증 8건 포함. 운영용 대상·모델·DB 연결은 실행하지 않음 |
| Chromium 결과 화면 | completed/partial/failed, 실행 중·종료 후·새로고침 조회, 실제 fixture의 SVG·표·Resource 범위·ID 연결·confirmed 분리·승인 대기·HTML 문자열 비실행, 다른 브라우저 차단, 390px 가로 넘침 없음·JS 오류 0건 | 공개 계약 fixture와 Web 프로세스 대역만 사용 |
| 표시·경로·비밀값 경계 | 기존 생산자 출력 Schema가 fixture를 수용하는지 확인. 잘못된/누락/초과 JSON·버전/run/회차·해시/참조 불일치·중복 ID·잘못된 그래프 끝점·unsafe 근거·symlink/hardlink/FIFO 거절, GT/private/로그/근거 파일 비열람 | 모듈의 전체 의미·보안·진단 판정 검증을 대체하지 않음 |
| 빌드·정적 검사 | `abc2lab-local-web:stage5` 빌드, JavaScript syntax·`git diff --check` 성공 | Compose 전체 진단 성공을 뜻하지 않음 |

테스트 fixture·CLI 대역은 운영 이미지에서 제외한다. Docker 검증 컨테이너는
자동 제거하며 기존 테스트 웹·Neo4j·Ollama·DB/모델 volume을 변경하지 않았다.
모듈·루트 Runner·기존 Schema·루트 의존성·루트 Compose·docs/spec는 수정하지 않았다.

**5단계 완료 시점의 다음 작업(6단계, 아래 구현 이력 참조):** Reporter의 evaluation_results를 연결해 지표·분자/분모·미검증
집계·미측정/null·평가 한계를 표시한다. Web은 GT 원문을 읽거나 성능을 재계산하지 않는다.
그다음 7단계에서 외부 연결 조건·실모델·정책·대상 계정이 준비된 상태로 실제
Docker 테스트 웹 전체 진단을 검증한다. 이번 단계에서는 실제 모듈·LLM·Neo4j·
Ground Truth 평가 성능을 완료로 기록하지 않는다.

### 19.12 6단계 Reporter 개발 평가 화면 — 2026-10-12

#### 연결·표현 경계

기존 Runner가 development 모드에서 호출하는 `reporter.evaluate`의 공개 출력
`evaluation_results.json`(`schema_version=0.2.0`)을 성능 평가 메뉴에 연결했다.
Runner 호출·평가 계산·모듈 Schema는 수정하지 않았다. 기존 표·모바일 스타일을
재사용했으며 외부 사이트 등록·배포·새 의존성은 없다.

| 표시 영역 | 공개 출력 필드 | 처리 원칙 |
| --- | --- | --- |
| 평가 조건 | ground_truth_ref의 dataset_id·dataset_version·sha256, source_graph_revision·matching_profile | 메타데이터만 표시. 정답 경로·원문은 반환하거나 열지 않음 |
| 성능 지표 | metrics의 metric_id·value·numerator·denominator | 순서·값 보존. ID·배열 길이를 하드코딩하지 않음. value와 표시용 백분율을 함께 제공 |
| 후보/확정 집계 | candidate_counts·confirmed_counts | TP·FP·FN을 별도 행으로 표시. 두 단계를 합치지 않음 |
| 미검증 | unverified_counts | 정책 차단·승인 대기·판단불가를 별도 표시 |
| 모델·생성 조건 | models | 실제 공개 모델/프롬프트 버전·temperature·seed 표시. Web에서 모델 선택·LLM 호출 없음 |
| 실행 측정값 | run_metrics | 시간(ms)·LLM 호출·토큰·최대 메모리(MB). null은 미측정 |
| 평가 한계 | notes | textContent로 표현. HTML·명령으로 실행하지 않음 |

Web은 지표를 분자/분모로 재계산하지 않는다. 비율 null은 **미측정/계산 불가**,
원시 0은 실제 기록된 0으로 구분한다. 미발행·failed·조회 오류는 점수 0으로
대체하지 않으며 partial에는 미처리 범위가 있다는 설명을 유지한다. 값이 없는
모델·지표 목록도 이미 제공된 빈 목록과 미발행 상태를 구분한다.

#### 조회·검증

- 5단계의 소유 run 전용 `/api/run-results`를 재사용한다. 조회 목록은 공개 결과
  9종으로 늘어나며 사용자 지정 경로·다른 run·private·로그·근거 본문은 읽지 않는다.
- 버전·run·회차·작성자·envelope 상태, 입력 참조·해시와 종료 통지 해시 검증을
  그대로 적용한다. 평가 source_graph_revision이 발행된 KG revision과 다르면 거절한다.
- 표시 필드의 타입·필수 키·비율 범위·중복 지표 ID·음수/boolean 집계·정답 참조의
  안전한 상대 경로·SHA-256을 검사한다. 정답 해시와 실제 원문 일치는 Reporter 책임이다.
- 평가 메뉴 진입·실행 중 polling·다시 확인·새로고침으로 발행 결과를 조회한다.
  이전 요청의 늦은 응답과 다른 브라우저 결과를 표시하지 않는다. 조회 실패 시 이전
  평가 내용을 지우며 정상 빈 결과로 표시하지 않는다.
- Web 테스트의 공개 평가 fixture는 생산자 출력 Schema로 확인한다. fixture와 CLI
  대역은 테스트에만 사용하며 실제 운영 실행의 fallback이나 성능 측정값이 아니다.

#### 검증 결과·다음 단계

```bash
.venv/bin/python -m pytest local_web/tests tests/pipeline -q --tb=short
LOCAL_WEB_BROWSER_TESTS=1 .venv/bin/python -m pytest local_web/tests tests/pipeline -q --tb=short
node --check local_web/frontend/app.js
node --check local_web/frontend/results.js
```

| 검증 | 실제 결과 | 한계 |
| --- | --- | --- |
| Web·기존 Runner 회귀 | **332 passed · 8 skipped** (Web 274 + Runner 58) | Chromium·마운트 전용 검증은 기본 실행에서 제외 |
| 최종 Chromium 포함 회귀 | **339 passed · 1 skipped**, 67.98초 (Web 281 + Runner 58) | 기존 브라우저 7건 포함. 읽기 전용 Docker 마운트 1건은 미실행 |
| 추가 평가 테스트 | **48 passed** | Reporter 공개 출력 fixture 기반. 실제 지표를 계산·측정한 결과 아님 |
| 평가 화면 | completed/partial/미발행, 비율·분자/분모·null·후보/확정 TP/FP/FN·모델·측정값·한계, 새로고침·다시 확인·조회 실패 후 삭제/복구·다른 브라우저 분리 | 실제 Web 서버·Chromium·CLI 대역만 사용 |
| 입력·보안 경계 | producer 출력 Schema 수용, 버전/run/회차·해시/참조·KG revision·잘못된 집계/모델·중복 지표·위험 경로 거절, GT/private/로그 비열람·HTML 비실행·비밀값 비노출·390px 가로 넘침 없음·JS 오류 0건 | 생산자 진단·평가 로직의 정확성 검증을 대체하지 않음 |
| 정적 검사 | JavaScript syntax·Python compile·git diff --check 성공 | 실제 이미지 재빌드·전체 Compose 진단은 미실행 |

샌드박스의 소켓 차단으로 기본 서버 테스트가 처음에는 setup 오류를 냈으며, 로컬
HTTP 소켓 실행을 허용받아 회귀를 재실행했다. 최종 결과는 위 성공 실행 기준이다.
검증 서버·소유 자식 프로세스는 테스트 종료 시 정리했다. 실제 테스트 웹·DB·모델
volume·사용자 설정을 변경하지 않았다. `local_web/` 밖의 모듈·Runner·Schema·
의존성·루트 Compose·docs/spec는 수정하지 않았다.

실제 Docker 대상·LLM·Neo4j·정답 성능 측정과 독립 fixture 기반 표시 검증은
구분한다. 이 단계의 fixture 점수는 프로젝트 성능 수치가 아니다.

**다음 7단계:** PR #69 반영 이미지 재빌드 → 대상 external 네트워크/DNS·내부
포트·로그인·계정·모델·Safety origin·데이터셋·결과 권한 준비 → 실제 브라우저
진단 → KG·취약점 리포트·평가 확인. 정상 흐름뿐 아니라 로그인/설정/중간 실패와
리다이렉트 partial도 확인하고 실제 결과·한계를 기록한다. 모듈 변경이 필요하면
해당 담당자에게 요청하며 Web에서 판단·정책·허용 origin을 임의로 수정하지 않는다.

### 19.13 7단계 준비: vulnerable 실행 설정 초안 — 2026-10-12

사용자가 승인한 첫 테스트용 설정을 `local_web/configs/vulnerable/`에 추가했다.
아직 서비스 실행·모델 다운로드·실제 로그인·전체 진단을 하지 않았다.
테스트 앱에만 해당하는 값은 외부 설정에 두며 Web·모듈 코드에 하드코딩하지 않는다.
이 초안은 제품 기본 정책이나 임의 웹에 공통 적용할 설정이 아니다.

| 파일 | 승인한 설정 | 변경 시 확인할 사항 |
| --- | --- | --- |
| collector.toml | target_url=http://testapp-vulnerable:8000, extra_allowed_origins 없음, max_depth=3, allow_state_changing=false | 대상의 실제 Docker DNS·내부 포트·로그인 폼과 맞아야 함 |
| semantic_analyzer.toml | Ollama qwen2.5:1.5b, http://ollama:11434, temperature=0.0, timeout_seconds=300.0 | CPU용 첫 모델 선택. 설치·메모리·응답 시간·추론 품질은 별도 확인 |
| scenario_generator.toml | 의미 분석과 동일한 모델·주소·temperature·timeout | 모듈별 변경 가능. 두 설정이 같다는 사실은 두 단계 성공을 보장하지 않음 |
| policy.json | policy_config 0.1.0·policy_version=draft-1, 해당 origin의 /orders 범위만, 시나리오당 최대 20건·60초 | 운영자가 범위·별칭·역할·요청 영향을 검토. Web이 입력에 맞춰 자동 수정하지 않음 |

#### Collector 입력과 정책 계정

- 로그인은 `/login`, input name은 `email`·`password`, 성공 판정은
  `left_login_page`다. 현재 테스트 앱 폼·라우터에서 확인했으며 다른 웹에는 재확인한다.
- 화면 초기 계정 별칭은 `user_a`·`user_b`(user), `admin_a`(admin)다. Guest는
  Collector가 자동 추가한다. 로그인 ID·비밀번호는 어떤 초안 파일에도 넣지 않는다.
- 정책은 Collector의 공개 ID 규칙에 맞춘 `account:guest`/`role:guest`,
  `account:user_a`·`account:user_b`/`role:user`, `account:admin_a`/`role:admin`을
  명시한다. Guest 외 계정은 세션이 필요하다. 실제 계정 비밀값은 Web 화면에서 받는다.
- Web에서 별칭·역할·대상 URL을 변경하면 정책 원본도 운영자가 별도로 검토한다.
  기존 정책과 안 맞는 입력은 실행 허용으로 대체하거나 범위를 자동 확대하지 않는다.

#### 첫 검증 범위와 한계

현재 테스트 앱의 GET `/orders`·`/orders/{order_id}` 구현은 조회만 하므로 이 범위에
`state_change=none`·`data_impact=read_only`·`service_impact=low` 규칙을 작성했다.
GET이라는 이유만으로 모든 경로를 읽기 전용으로 분류한 것이 아니다. 경로 prefix는
정확한 `/orders`와 `/orders/` 하위 경로를 의미하며 `/orders-other`는 포함하지 않는다.
대상 앱에 새 하위 경로나 상태 변경 동작이 추가되면 이 prefix의 규칙을 다시 검토한다.

- 동일 경로의 POST·PUT·PATCH·DELETE는 세 영향 모두 `block`이다.
- 다른 origin·경로는 허용 범위 밖이므로 차단한다. 허용 범위 안에서 규칙이 없는
  method는 unknown/승인 대기로 남기며 요청을 실행하지 않는다.
- 읽기 전용 규칙이라도 계획의 state_change가 unknown이면 승인 대기를 유지한다.
  상태 변경 추정과 충돌하거나 다른 assessment가 실패해도 Web이 allow로 바꾸지 않는다.
- 로그인 세션 획득은 Collector의 공개 인증 창구 책임이다. verifier 계획에 담긴
  별도 POST `/login`은 이 정책 범위 밖이다. 이 초안으로 인증 POST를 검증 요청에
  허용하는 것이 아니다.
- 수집은 대상 전체를 탐색할 수 있지만 verifier의 첫 허용 범위는 주문 조회에 한정한다.
  따라서 장바구니·관리자 기능 등 전체 취약점 검증 완료나 취약점 없음으로 해석하지 않는다.
- 20건·60초는 **시나리오당 검증 요청 제한**이다. 전체 진단의 총량 제한이나 LLM의
  호출당 300초 타임아웃과 다르다. 재시도·리다이렉트는 verifier의 실제 요청 예산에 포함된다.

#### 실행 연결 전 준비

`local_web/.env.example`의 안내를 따라 커밋하지 않는 `local_web/.env`를 직접
준비한다. 설정 4개에는 다음 **호스트 절대 경로**를 지정한다. `<저장소 절대 경로>`는
실제 위치로 치환하며, 예시 문자열 그대로 실행하지 않는다.

```dotenv
LOCAL_WEB_COLLECTOR_CONFIG_PATH=<저장소 절대 경로>/local_web/configs/vulnerable/collector.toml
LOCAL_WEB_SEMANTIC_CONFIG_PATH=<저장소 절대 경로>/local_web/configs/vulnerable/semantic_analyzer.toml
LOCAL_WEB_SCENARIO_CONFIG_PATH=<저장소 절대 경로>/local_web/configs/vulnerable/scenario_generator.toml
LOCAL_WEB_POLICY_CONFIG_PATH=<저장소 절대 경로>/local_web/configs/vulnerable/policy.json
```

설정 4개는 이미지에 넣지 않고 Compose의 기존 읽기 전용 마운트로 전달한다.
추가로 실제 대상 네트워크, datasets 디렉터리, UID 10001이 쓸 수 있는 결과
디렉터리, Neo4j 비밀번호가 필요하다. 비밀번호는 템플릿·공유 로그·커맨드 인자에
넣지 않는다. `compose.gpu.yml`은 NVIDIA 전용이므로 Intel UHD 620 환경에서는
사용하지 않고 CPU 기본 구성을 사용한다. 모델 준비와 실행은 다음 작업이며,
설정 파일 생성만으로 준비 완료나 실제 진단 성공을 표시하지 않는다.

#### 설정 검증

policy JSON은 소유 모듈의 공개 Schema를 읽어 검사한다. Web에서 모듈 내부
평가 함수를 호출하거나 private에 쓰지 않는다. 실제 평가·차단·승인 판정은
여전히 Safety Policy 책임이다. 검증 명령·결과는 아래에 기록한다.

```bash
.venv/bin/python -m pytest local_web/tests/test_execution_configs.py -q --tb=short
.venv/bin/python -m pytest local_web/tests/test_execution_configs.py local_web/tests/test_inputs.py local_web/tests/test_runner.py -q --tb=short
```

- 새 설정 검증: **7 passed**. TOML·Web 공개 설정 검사, 정책 Schema `0.1.0`,
  origin·범위·계정 ID·조회/차단 규칙·예산 대응, 계정 비밀값 키 비포함을 확인했다.
- 설정·기존 입력·Runner 선택 회귀: **104 passed**, 1.42초. Runner는 테스트
  CLI 대역이며 실제 대상·Ollama·Neo4j·Safety 판정·리포트 성능 검증이 아니다.
- `git diff --check` 성공. 실제 `.env`·비밀값·결과 폴더는 생성하지 않았다.
  설정 4개·연결 안내·테스트만 `local_web/`에서 변경했고 모듈·Schema·루트 설정·
  의존성·docs/spec는 수정하지 않았다. **7단계 전체 완료가 아니라 실행 준비 초안이다.**

다음은 `local_web/.env`의 실제 경로·네트워크·비밀번호와 결과 저장 권한 준비,
CPU Compose 이미지 재빌드·서비스 기동·모델 설치다. 대상 앱의 기존 DB를
임의로 초기화하지 않으며 실제 진단과 실패/제한 검증은 별도로 진행한다.

## 20. 변경 이력

| 날짜 | 변경 | 구현·계약 영향 |
| --- | --- | --- |
| 2026-10-11 | 기존 책임 경계를 유지하고 Docker·사전 설정·실모델·지표 중심 통합 검증 MVP와 7단계 계획 추가 | README만 변경. Web/Docker 코드는 미구현. 모듈·Runner·Schema·의존성·기존 산출물 계약 변경 없음 |
| 2026-10-12 | MVP 2단계 Web 화면·Docker 분리 배치·CPU 기본 Ollama·선택 GPU 설정과 공개 경계 구현·검증 | 94 passed, Docker 빌드·3개 서비스 healthy·호스트 접속·메뉴 4개·모바일 폭 확인. 입력·Runner·산출물 연결·모델 추론·전체 진단은 후속 단계. 모듈·Runner·Schema·루트 의존성 변경 없음 |
| 2026-10-12 | MVP 3단계 읽기 전용 Collector 설정·데이터셋과 URL·복수 계정 입력 준비, 브라우저별 비밀값 임시 보관·폐기 구현 | Web·Runner 158 passed·2 skipped, 해당 Docker 환경 2 passed. 원본 설정 보존·GT 비열람·CSRF·만료·비밀값 비노출 확인. 실제 진단·평가·Runner 실행은 4단계 이후. 모듈·Schema·루트 설정·의존성 변경 없음 |
| 2026-10-12 | MVP 4단계 기존 Runner 자식 실행·소유 실행 상태·공개 파일 존재·종료 단계 요약, external 대상 네트워크·호스트 결과 폴더·읽기 전용 모듈 설정 연결 | Web·Runner 228 passed·5 skipped, Docker Web 175 passed. CPU/GPU Compose·빌드·Chromium·단일 실행·세션 분리·실패/종료/비밀값 경계 확인. 실제 Docker 대상 진단·LLM/Neo4j·GT 평가는 미검증. 모듈·Runner·Schema·루트 설정·의존성 변경 없음 |
| 2026-10-12 | MVP 5단계 공개 산출물 기반 수집/의미 요약·기본 SVG KG와 표·ID 연결 진단 과정·승인 대기 메뉴·confirmed 전용 리포트, 소유 실행의 최소 표시 검증 구현 | Web·Runner 284 passed·8 skipped, 읽기 전용 Docker Web 234 passed. fixture Schema·Chromium·부분/실패/미발행·해시/참조·파일 경계·HTML 비실행·390px 화면 확인. 실제 전체 진단·평가 지표 연결은 후속 단계. 모듈·Runner·Schema·루트 설정·의존성·docs/spec 변경 없음 |
| 2026-10-12 | MVP 6단계 Reporter 공개 평가 결과·지표/분자/분모·후보/확정 TP/FP/FN·미검증/null·모델/측정값/한계 표시, 기존 메뉴 조회·새로고침 연결. PR #69의 Collector 설정 origin 허용과 리다이렉트 한계를 현재 연결 조건에 반영 | Web·Runner 332 passed·8 skipped, 최종 Chromium 포함 339 passed·1 skipped. 추가 평가 48건, GT 비열람·원본 지표 보존·참조/revision·조회 실패/복구·모바일·비밀값 경계 확인. 실제 Docker 전체 진단은 7단계. local_web 밖 코드·Schema·의존성·docs/spec 변경 없음 |
| 2026-10-12 | MVP 7단계 준비: 사용자 승인 기반 vulnerable용 Collector·두 LLM·주문 조회 한정 정책 초안, .env.example·README 연결 안내와 설정 검증 추가 | 설정 7 passed, 설정·입력·Runner 104 passed. CPU qwen2.5:1.5b·호출당 300초, GET /orders 한정·검증 시나리오당 20건/60초, 비밀값 미저장. 실제 .env·모델 다운로드·서비스 기동·진단은 미실행. local_web 밖 변경 없음 |
| 2026-10-12 | 로그인 ID 입력을 평문으로 표시하고 비밀번호 마스킹 유지. 인증정보 초기화를 input type 대신 필드명 기준으로 변경 | Chromium 포함 Web·Runner 346 passed·1 skipped. 이메일 형태·4096자 제한, 역할·별칭 규칙 유지. 성공·실패·페이지 이탈 시 로그인 ID·비밀번호 초기화와 브라우저 저장소/API 비노출 확인. JavaScript syntax·git diff 검사 성공. 모듈·Schema·백엔드·의존성 변경 없음 |
