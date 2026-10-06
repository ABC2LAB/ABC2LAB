# 명세 스냅샷 (docs/spec/)

노션 `ABC2LAB_인터페이스_명세서_v0.1`과 하위 페이지를 export한 사본입니다.
노션 무료 플랜에서는 Claude Code가 노션을 읽을 수 없어서, 모듈 필드 표를 파일로 읽을 수 있게 레포에 둡니다.

**노션이 원본입니다. 이 폴더는 직접 수정하지 않습니다.** 노션과 다른 곳을 발견하면 GitHub 관리자에게 알립니다.

- 원본: https://app.notion.com/p/59862d24e2b68392a7b701df0bd15bbb
- 기준일: export 2026-10-06 (명세 v0.1, 10/5 확정)

## 파일

본문은 export 그대로이고, 페이지 사이 링크만 이 폴더의 파일명으로 바꿨습니다.

| 파일 | 노션 페이지 | URL |
|---|---|---|
| `00-index.md` | ABC2LAB_인터페이스_명세서_v0.1 | https://app.notion.com/p/59862d24e2b68392a7b701df0bd15bbb |
| `01-dev-standard.md` | 개발 기준 · 환경 일원화와 GitHub 초기 설정 | https://app.notion.com/p/82c62d24e2b683e4a21e01d4e7c41318 |
| `02-common-contract.md` | 공통 계약 · 파일 형식과 전달 규칙 | https://app.notion.com/p/ecf62d24e2b683d7a566013eed4d7d6b |
| `03-runner-layout.md` | 실행 제어 · runner와 폴더 구조 | https://app.notion.com/p/a7d62d24e2b68218988981a208c29863 |
| `04-examples-refs.md` | 연결 예시 · 참고 자료 | https://app.notion.com/p/59162d24e2b683e99b2a81dfa486961e |
| `m1-collector.md` | 웹 정보 수집기 · collector | https://app.notion.com/p/0c662d24e2b6822c83d9819276cc2624 |
| `m2-semantic_analyzer.md` | 의미 분석기 · semantic_analyzer | https://app.notion.com/p/b8362d24e2b68328b85c813f2cb65116 |
| `m3-knowledge_graph.md` | 지식 그래프 저장소 · knowledge_graph | https://app.notion.com/p/c9862d24e2b682a8884381dce73bdb20 |
| `m4-access_analyzer.md` | 접근 통제 분석기 · access_analyzer | https://app.notion.com/p/27b62d24e2b683419a9701569e9057cb |
| `m5-scenario_generator.md` | 시나리오 생성기 · scenario_generator | https://app.notion.com/p/fbf62d24e2b683bbbbd6814799e6804f |
| `m6-safety_policy.md` | Safety Policy · safety_policy | https://app.notion.com/p/9b862d24e2b683c0ac0e012f09df80a0 |
| `m7-verifier.md` | 재현·검증기 · verifier | https://app.notion.com/p/3c962d24e2b6822a81fe8180c4c16833 |
| `m8-reporter.md` | 평가·리포트 · reporter | https://app.notion.com/p/d7d62d24e2b6821a8f4481c91d28bda0 |

## 갱신 방법 (GitHub 관리자)

1. 노션 명세 메인 페이지를 하위 페이지 포함, Markdown 형식으로 export합니다.
2. 이 폴더의 md 13개를 새 export로 교체합니다. 파일명은 위 표를 따르고, 노션 페이지 사이 링크는 바뀐 파일명으로 고칩니다. 본문은 고치지 않습니다.
3. 이 README의 기준일을 고칩니다. 페이지가 추가·삭제됐으면 표도 고칩니다.
4. `docs: 명세 스냅샷 갱신 (YYYY-MM-DD)` PR을 올립니다.
5. 머지되면 디스코드에 공지합니다.
