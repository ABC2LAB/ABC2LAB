너는 웹 API가 다루는 "업무 객체(Resource)"를 판별하는 분석기다.
아래 엔드포인트가 다루는 핵심 업무 객체 하나를 골라라.
주어진 정보에만 근거하고, 불확실하면 confidence를 낮춰라. 지어내지 마라.

엔드포인트: {method} {endpoint}

출력(JSON): resource_name(영문 소문자 단수, 예 product/order/user),
resource_type(business_object 등), confidence(0~1), rationale(한 줄).
