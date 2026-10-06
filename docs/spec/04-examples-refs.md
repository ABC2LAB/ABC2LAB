# 연결 예시 · 참고 자료

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md)

<aside>
📎

연결 예시는 가상 데이터다. 실제 처리 코드는 각 모듈 폴더에서 구현하며 실제 웹 진단·벤치마크 결과와 구분한다.

</aside>

## 모듈별 fixture와 연결 예시의 책임

**공통 환경과 개발 시작 기준:** [개발 기준 · 환경 일원화와 GitHub 초기 설정](01-dev-standard.md)

- fixture·대역 테스트도 팀 공통 Python과 루트 잠금 환경에서 수행한다. 테스트 코드·입력/출력 검증은 각 모듈 담당자의 책임이며, 관리자 초기 설정 완료와 모듈 구현 완료는 따로 확인한다.
- 각 모듈 담당자는 자기 `tests/fixtures/`에 필요한 입력 JSON·근거·자기 출력 샘플을 두고 자기 입력/출력 Schema로 검증한다. 다른 모듈의 실제 코드 없이도 명세 입력을 사용해 독립 검증한다.
- 생산자가 출력 계약의 기준 Schema·샘플을 자기 폴더에서 관리한다. 직접 소비자는 합의한 샘플과 Schema를 자기 입력 fixture·Schema로 관리하며 다른 모듈 파일을 직접 import/$ref하지 않는다.
- 계약을 바꾸면 생산자와 직접 소비자가 각자 자기 Schema·adapter·fixture·README를 수정하고 실제 생성 샘플로 수신 동작을 확인한다. 예시 URL·계정·역할·배열 길이·배열 위치를 구현에 하드코딩하지 않는다.
- 입력 변화·빈 배열·null·partial, 잘못된 버전·형식·ID·해시·외부 의존성 실패와 Safety 차단·승인 대기를 해당 모듈 담당자가 테스트한다.
- 평가용 Ground Truth·테스트 웹·초기 상태는 자료 제공자가 책임지고 reporter는 자기 개발 평가 입력으로만 사용한다.

## 예상 fixture 위치와 해석

- 모듈별 run_root 예시: `modules/<module_id>/tests/fixtures/runs/run_demo_001/`. 그 아래 artifacts·evidence 경로를 실행 파일의 상대 참조와 동일하게 구성한다.
- 필요한 근거와 input_refs 대상 파일을 자기 fixture run_root에 함께 둔다. 다른 모듈의 구현 폴더를 참조하지 않는다. 원래 입력 파일과 해당 해시는 정확히 대응시킨다.
- GroundTruthRef는 기존 계약처럼 project_root 기준 `datasets/<dataset_id>/ground_truth.json`을 가리킨다. 자기 평가 fixture에서 필요한 project_root와 정답 경로를 제공한다.
- 기존에 제공한 가상 연결 예시의 JSON 구조·ID·요청/근거 대응은 참고 자료로 사용한다. 모듈별 예상 위치로 fixture를 구성할 때 모듈 담당자가 상대 참조·실제 바이트 SHA-256을 검증한다.
- 이번 변경은 모듈별 소유권과 예상 폴더 설계 변경이다. 실제 저장소 파일 이동이나 모듈 코드·테스트 구현 완료를 의미하지 않는다.

## 참고

- JSON Schema 공식 설명: [https://json-schema.org/overview/what-is-jsonschema](https://json-schema.org/overview/what-is-jsonschema)
- JSON Schema Draft 2020-12: [https://json-schema.org/draft/2020-12](https://json-schema.org/draft/2020-12)
- OWASP WSTG 인가 테스트: [https://owasp.org/www-project-web-security-testing-guide/v42/4-Web_Application_Security_Testing/05-Authorization_Testing/02-Testing_for_Bypassing_Authorization_Schema](https://owasp.org/www-project-web-security-testing-guide/v42/4-Web_Application_Security_Testing/05-Authorization_Testing/02-Testing_for_Bypassing_Authorization_Schema)
- OWASP WSTG 업무 흐름 테스트: [https://owasp.org/www-project-web-security-testing-guide/v42/4-Web_Application_Security_Testing/10-Business_Logic_Testing/06-Testing_for_the_Circumvention_of_Work_Flows](https://owasp.org/www-project-web-security-testing-guide/v42/4-Web_Application_Security_Testing/10-Business_Logic_Testing/06-Testing_for_the_Circumvention_of_Work_Flows)

[ABC2LAB_인터페이스_명세서_v0.1](00-index.md)