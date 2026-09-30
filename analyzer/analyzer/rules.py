"""
룰 기반 검증 태스크 추출
======================

크롤 데이터(관측된 사실)에서 '검증해야 할 가설'을 결정론적으로 뽑는다.
여기서 나오는 것은 '취약 확정'이 아니라 '검증 태스크'다. 실제 확정은 뒤 단계
(공격 시나리오 실행)가 요청을 보내 판단한다.

왜 이렇게 나누는가:
  크롤은 각 역할이 '정상 흐름'만 밟는다. 그래서 크롤 데이터에는
    - 남의 id로 접근하면 어떻게 되는가 (V1/V2)   -> 데이터에 없음, 검증 필요
    - 링크 없는 admin API를 user가 부르면 (V3)   -> user가 그 페이지에 못 가 미관측, 재검증 필요
    - 숨은 엔드포인트 (V4)                         -> 아예 관측 안 됨, 룰 불가(LLM/소스분석 영역)
    - UI에 없는 파라미터 (V5)                      -> 관측 안 됨, 룰 불가(LLM 가설 영역)
  즉 룰이 잘하는 일은 '관측된 표면에서 검증 대상을 빠짐없이 뽑아 지시서로 만드는 것'.

각 룰은 순수 함수: (CrawlRun) -> list[VerificationTask]. 추가/제거가 독립적이다.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .models import CrawlRun, VerificationTask, RequestObs

# id처럼 보이는 경로 조각을 이미 크롤러가 {id}/{order_id} 등으로 정규화해 준다.
_ID_PLACEHOLDER = re.compile(r"\{[^}]+\}")

# 접근통제상 "성공"으로 볼 상태코드
_OK = {200, 201, 204, 206, 301, 302}
# "차단"으로 볼 상태코드
_DENIED = {401, 403, 404}


def _is_id_parameterized(endpoint: str) -> bool:
    return bool(_ID_PLACEHOLDER.search(endpoint))


def _observed_matrix(run: CrawlRun):
    """(method, endpoint) -> {role: RequestObs(대표 1건)} 매트릭스."""
    mat: dict[tuple[str, str], dict[str, RequestObs]] = defaultdict(dict)
    for req in run.all_requests():
        key = (req.method, req.endpoint)
        # 같은 역할이 같은 엔드포인트를 여러 번 부르면 첫 성공 관측을 대표로
        cur = mat[key].get(req.role)
        if cur is None or (req.status in _OK and cur.status not in _OK):
            mat[key][req.role] = req
    return mat


def _linked_endpoints(run: CrawlRun) -> set[str]:
    """어떤 페이지의 링크로든 도달 가능한 엔드포인트 집합 (UI 노출 표면)."""
    linked = set()
    for page in run.all_pages():
        for link in page.links:
            if link.endpoint:
                linked.add(link.endpoint)
    return linked


# ---------------------------------------------------------------------------
# R1: id 파라미터화 엔드포인트 -> 교차접근 검증
# ---------------------------------------------------------------------------

def rule_cross_access(run: CrawlRun) -> list[VerificationTask]:
    """
    resource_ids가 있고 경로가 {id}로 파라미터화된 엔드포인트는,
    각 역할이 '본인 자원'만 관측했을 가능성이 높다. 다른 주체의 id로
    접근했을 때도 성공하는지(=IDOR) 검증해야 한다.
    → V1(주문), V2(프로필) 후보.
    """
    tasks = []
    mat = _observed_matrix(run)
    seen = set()
    for (method, endpoint), by_role in mat.items():
        if not _is_id_parameterized(endpoint):
            continue
        # 어느 역할이든 성공 관측 + 구체 resource_id를 남긴 경우
        observed_ids = {}
        ok_roles = []
        for role, req in by_role.items():
            if req.status in _OK:
                ok_roles.append(role)
                if req.resource_ids:
                    observed_ids[role] = req.resource_ids
        if not ok_roles:
            continue
        key = (method, endpoint)
        if key in seen:
            continue
        seen.add(key)

        # 검증 지시: 한 역할이 관측한 id를, 그 자원의 소유자가 아닌 다른 세션으로 접근
        tasks.append(VerificationTask(
            task_id=f"T-R1-{method}-{_slug(endpoint)}",
            rule_id="R1",
            category="cross_access",
            endpoint=endpoint,
            method=method,
            rationale=(
                f"{method} {endpoint} 는 id로 파라미터화되어 있고 "
                f"{sorted(ok_roles)} 역할에서 200 관측됨. 각 역할이 본인 자원만 접근했을 수 있어, "
                f"타 주체의 id로 접근 시에도 성공하는지(IDOR) 검증 필요."
            ),
            verify={
                "type": "cross_object_access",
                "steps": [
                    "역할 A 세션으로 A 소유 자원의 id를 확보",
                    "역할 B(비소유자) 세션으로 같은 id에 동일 요청 전송",
                    "기대(secure): 403/404, 위반(vulnerable): 200 + 타인 데이터 반환",
                ],
                "observed_resource_ids": observed_ids,
            },
            evidence={"observed_roles": sorted(ok_roles)},
            expected_vuln_hint=_hint_for_cross_access(endpoint),
            severity_hint="high",
        ))
    return tasks


def _hint_for_cross_access(endpoint: str) -> str:
    if "orders" in endpoint:
        return "V1"
    if "users" in endpoint:
        return "V2"
    return "IDOR?"


# ---------------------------------------------------------------------------
# R2: 링크 없는 fetch 엔드포인트 -> 숨은 표면
# ---------------------------------------------------------------------------

def rule_hidden_surface(run: CrawlRun) -> list[VerificationTask]:
    """
    resource_type이 fetch/xhr 인데 어떤 페이지의 링크에도 없는 엔드포인트는
    'UI에 링크되지 않은 API 표면'이다. 열거/직접호출로 접근통제를 검증해야 한다.
    → V2(프로필 API) 후보. (V4는 아예 관측조차 안 되므로 여기서 안 잡힘 - 정상)
    """
    tasks = []
    linked = _linked_endpoints(run)
    seen = set()
    for req in run.all_requests():
        if req.resource_type not in ("fetch", "xhr"):
            continue
        # 상태변경(POST 등)은 R4가 다룬다. 숨은 '조회' 표면만 여기서 본다.
        # (예: 상품상세의 '장바구니 담기' 버튼이 부르는 POST /cart/add 는
        #  링크(<a>)가 아니어서 linked에 안 잡히지만, 숨은 IDOR 표면이 아니라
        #  정상 상태변경 액션이므로 hidden_surface로 오분류하면 안 됨)
        if req.method != "GET":
            continue
        if req.endpoint in linked:
            continue
        key = (req.method, req.endpoint)
        if key in seen:
            continue
        seen.add(key)
        tasks.append(VerificationTask(
            task_id=f"T-R2-{req.method}-{_slug(req.endpoint)}",
            rule_id="R2",
            category="hidden_surface",
            endpoint=req.endpoint,
            method=req.method,
            rationale=(
                f"{req.method} {req.endpoint} 는 fetch로만 관측되고 어떤 페이지 링크에도 없음. "
                f"UI 비노출 API 표면 → 저권한/비로그인 세션의 직접 호출 및 id 열거로 접근통제 검증 필요."
            ),
            verify={
                "type": "direct_call_and_enumerate",
                "steps": [
                    "비로그인/저권한 세션으로 해당 엔드포인트 직접 호출",
                    "id 파라미터가 있으면 관측값 외 다른 값으로 열거",
                    "기대(secure): 401/403, 위반: 200 + 데이터",
                ],
                "response_shape": req.response_shape,
            },
            evidence={"resource_type": req.resource_type,
                      "observed_by": req.role,
                      "resource_ids": req.resource_ids},
            expected_vuln_hint="V2",
            severity_hint="high",
        ))
    return tasks


# ---------------------------------------------------------------------------
# R3: admin 계열 엔드포인트의 역할 커버리지 갭
# ---------------------------------------------------------------------------

def rule_coverage_gap(run: CrawlRun, privileged_prefixes=("/admin",)) -> list[VerificationTask]:
    """
    /admin 등 관리자 표면인데 저권한 역할에서 '관측 자체가 없는' 엔드포인트.
    크롤이 저권한 세션으로 그 페이지에 못 들어가 호출이 안 일어났을 수 있다.
    이 경우 '저권한으로 직접 호출하면 막히는가'를 반드시 재검증해야 한다.
    → V3(admin API를 user가 호출) 후보. 화면은 막혀도 API는 뚫린 케이스를 잡는다.
    """
    tasks = []
    mat = _observed_matrix(run)
    all_roles = set(run.role_names())
    seen = set()
    for (method, endpoint), by_role in mat.items():
        if not any(endpoint.startswith(p) for p in privileged_prefixes):
            continue
        observed_roles = set(by_role.keys())
        missing = all_roles - observed_roles
        # 관리자 전용으로 보이는데, 저권한 역할에서 미관측 → 검증 대상
        if not missing:
            continue
        key = (method, endpoint)
        if key in seen:
            continue
        seen.add(key)
        # fetch(=화면 뒤 API)면 우선순위/힌트를 높인다: 화면과 API의 접근통제 분리 케이스.
        # document(=관리자 HTML 페이지)는 대개 항상 보호되는 decoy 성격이라 우선순위를 낮춘다.
        is_api = by_role and any(r.resource_type in ("fetch", "xhr") for r in by_role.values())
        if is_api:
            severity, hint, priority = "high", "V3", "high"
            note = "  (fetch API → 화면은 막혀도 API만 뚫린 케이스 주의: V3 유형)"
        else:
            severity, hint, priority = "low", None, "low"
            note = ("  (관리자 HTML 페이지 → 대개 항상 admin 검사가 있어 안전(decoy)일 확률 높음. "
                    "낮은 우선순위로 최소 확인만)")
        tasks.append(VerificationTask(
            task_id=f"T-R3-{method}-{_slug(endpoint)}",
            rule_id="R3",
            category="coverage_gap",
            endpoint=endpoint,
            method=method,
            rationale=(
                f"{method} {endpoint} 는 관리자 표면인데 {sorted(missing)} 역할에서 미관측. "
                f"저권한 세션이 페이지 진입에 막혀 호출이 없었을 뿐, 표면 자체는 열려 있을 수 있음. "
                f"저권한/비로그인으로 직접 호출해 실제 차단 여부 재검증 필요." + note
            ),
            verify={
                "type": "privilege_probe",
                "priority": priority,
                "steps": [
                    f"미관측 역할 {sorted(missing)} 세션으로 {method} {endpoint} 직접 호출",
                    "비로그인 세션으로도 호출",
                    "기대(secure): 403(비로그인은 401/403), 위반: 200 + 관리자 데이터",
                ],
            },
            evidence={"observed_roles": sorted(observed_roles),
                      "missing_roles": sorted(missing),
                      "is_api_behind_page": is_api},
            expected_vuln_hint=hint,
            severity_hint=severity,
        ))
    return tasks


# ---------------------------------------------------------------------------
# R4: 상태변경 엔드포인트 -> Safety Policy 플래그
# ---------------------------------------------------------------------------

def rule_state_change_flag(run: CrawlRun) -> list[VerificationTask]:
    """
    상태를 바꾸는 요청(POST/PUT/PATCH/DELETE, 또는 is_state_changing 링크)은
    실제 검증 시 부작용(주문 생성/삭제 등)을 낼 수 있다. 8단계 Safety Policy가
    실행 전 반드시 검토하도록 플래그를 남긴다. 취약점 후보라기보다 '실행 통제' 신호.
    """
    tasks = []
    seen = set()
    for req in run.all_requests():
        if req.method not in ("POST", "PUT", "PATCH", "DELETE"):
            continue
        key = (req.method, req.endpoint)
        if key in seen:
            continue
        seen.add(key)
        params = sorted(set(req.query_params) | set(req.body_params))
        tasks.append(VerificationTask(
            task_id=f"T-R4-{req.method}-{_slug(req.endpoint)}",
            rule_id="R4",
            category="state_change",
            endpoint=req.endpoint,
            method=req.method,
            rationale=(
                f"{req.method} {req.endpoint} 는 상태변경 요청. 실제 검증 시 부작용 가능 → "
                f"Safety Policy 사전 검토 및 파라미터 조작(값 신뢰) 가설 대상."
            ),
            verify={
                "type": "state_change_review",
                "observed_params": params,
                "steps": [
                    "Safety Policy가 실행 허용 여부 판정 (idempotent 여부, 롤백 가능성)",
                    "허용 시: 서버가 신뢰하면 안 될 파라미터(가격 등) 조작 검증은 LLM 가설로 확장",
                ],
            },
            evidence={"observed_params": params, "observed_by": req.role,
                      "status": req.status},
            expected_vuln_hint="V5?" if "cart" in req.endpoint else None,
            severity_hint="review",
        ))
    return tasks


# ---------------------------------------------------------------------------
# 유틸 & 실행기
# ---------------------------------------------------------------------------

def _slug(endpoint: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", endpoint).strip("_") or "root"


ALL_RULES = [
    rule_cross_access,
    rule_hidden_surface,
    rule_coverage_gap,
    rule_state_change_flag,
]


def run_rules(run: CrawlRun) -> list[VerificationTask]:
    tasks: list[VerificationTask] = []
    for rule in ALL_RULES:
        tasks.extend(rule(run))
    # task_id로 안정 정렬
    tasks.sort(key=lambda t: (t.rule_id, t.endpoint, t.method))
    return tasks
