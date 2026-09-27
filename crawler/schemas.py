"""크롤러 출력 모델. common/schemas.py가 팀에서 확정되기 전까지 임시로 여기 둔다.

draft와 다른 점(role·method를 str로, resource_type·헤더 추가, 파라미터 값을 list로)은 확정 회의에서 맞추고
확정되면 common/으로 옮긴다.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CapturedRequest(BaseModel):
    """브라우저에서 난 요청 하나와 그 응답. 민감한 값은 마스킹된 채로만 들어온다.

    기본값을 두지 않아서 없는 값도 JSON에 null로 남는다.
    """

    model_config = ConfigDict(frozen=True)

    role: str
    method: str
    # "document"(문서 이동) | "fetch" | "xhr"
    resource_type: str
    url: str
    endpoint: str
    status: int | None
    # ?a=1&a=2 처럼 같은 키가 반복될 수 있어 값은 list로 둔다.
    query_params: dict[str, list[str]]
    body_params: dict[str, list[str]]
    # 경로에서 뽑은 id만. 쿼리 값 중 무엇이 id인지는 KG 단계가 판단한다.
    resource_ids: list[str]
    request_headers: dict[str, str]
    response_headers: dict[str, str]
    source_page: str | None
    source_action: str | None
    captured_at: datetime
