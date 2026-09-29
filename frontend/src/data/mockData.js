/** @type {import('../types/models.js').ProjectSummary} */
export const project = {
  name: 'ABC Commerce',
  targetUrl: 'https://shop.abc2lab.dev',
  lastScan: '2026.09.28 14:32',
  scanDuration: '4m 18s',
  securityScore: 72,
};

/** @type {import('../types/models.js').SummaryMetric[]} */
export const summaryMetrics = [
  { id: 'pages', label: '탐색된 페이지', value: 128, helper: '전체 웹 페이지', tone: 'blue' },
  { id: 'endpoints', label: '수집된 API', value: 342, helper: '확인된 엔드포인트', tone: 'purple' },
  { id: 'parameters', label: '발견된 파라미터', value: '1,245', helper: '입력 파라미터', tone: 'green' },
  { id: 'critical', label: '취약점 후보', value: 18, helper: '검토 대기 항목', tone: 'red' },
];

/** @type {import('../types/models.js').SeverityCount[]} */
export const severityCounts = [
  { severity: 'critical', label: 'Critical', count: 2, color: '#f0445b' },
  { severity: 'high', label: 'High', count: 4, color: '#ff7849' },
  { severity: 'medium', label: 'Medium', count: 7, color: '#f5bd4f' },
  { severity: 'low', label: 'Low', count: 5, color: '#46c6b3' },
];

export const weeklyFindings = [
  { label: '09/22', found: 4, resolved: 1 },
  { label: '09/23', found: 7, resolved: 3 },
  { label: '09/24', found: 5, resolved: 4 },
  { label: '09/25', found: 9, resolved: 5 },
  { label: '09/26', found: 6, resolved: 3 },
  { label: '09/27', found: 8, resolved: 6 },
  { label: '09/28', found: 5, resolved: 4 },
];

/** @type {import('../types/models.js').Vulnerability[]} */
export const vulnerabilities = [
  {
    id: 'VULN-2026-001', title: '관리자 인증 우회 가능성', category: 'Broken Access Control',
    severity: 'critical', cvss: 9.8, status: 'open', endpoint: '/api/admin/users', method: 'GET',
    detectedAt: '2026.09.28 14:31',
    summary: '권한 검증이 누락된 관리자 API를 통해 인증되지 않은 사용자가 계정 목록에 접근할 수 있습니다.',
    evidence: 'GET /api/admin/users 요청에 인증 헤더가 없는 경우에도 HTTP 200과 사용자 데이터가 반환되었습니다.',
    impact: '사용자 개인정보 노출과 관리자 기능 오용으로 이어질 수 있으며, 추가 공격을 위한 계정 정보 수집이 가능합니다.',
    remediation: ['모든 관리자 경로에 서버 측 권한 검증을 적용합니다.', '기본 거부 방식의 접근 제어 정책을 사용합니다.', '비정상적인 관리자 API 접근을 감사 로그에 기록합니다.'],
    references: ['OWASP A01:2021 – Broken Access Control', 'CWE-862: Missing Authorization'],
  },
  {
    id: 'VULN-2026-002', title: '검색 파라미터 SQL Injection', category: 'Injection',
    severity: 'critical', cvss: 9.1, status: 'reviewing', endpoint: '/api/products/search?q=', method: 'GET',
    detectedAt: '2026.09.28 14:28', summary: '검색어가 쿼리에 안전하지 않게 결합되는 정황이 확인되었습니다.',
    evidence: "q=' OR 1=1-- 입력 시 응답 데이터 범위와 처리 시간이 유의미하게 변했습니다.",
    impact: '상품 및 사용자 데이터베이스의 기밀성·무결성이 침해될 수 있습니다.',
    remediation: ['Prepared Statement를 사용합니다.', '입력값 스키마 검증을 적용합니다.'], references: ['OWASP A03:2021 – Injection'],
  },
  {
    id: 'VULN-2026-003', title: '저장형 Cross-Site Scripting', category: 'XSS',
    severity: 'high', cvss: 8.2, status: 'open', endpoint: '/api/reviews', method: 'POST',
    detectedAt: '2026.09.28 14:22', summary: '상품 후기 본문에 저장된 스크립트가 상세 페이지에서 실행됩니다.',
    evidence: '후기 본문에 입력한 이벤트 핸들러가 상품 페이지 방문 시 실행되었습니다.',
    impact: '세션 탈취나 사용자 대신 악성 요청을 수행할 수 있습니다.',
    remediation: ['출력 컨텍스트에 맞는 인코딩을 적용합니다.', 'Content Security Policy를 구성합니다.'], references: ['CWE-79'],
  },
  {
    id: 'VULN-2026-004', title: '취약한 비밀번호 재설정 토큰', category: 'Authentication',
    severity: 'high', cvss: 7.7, status: 'open', endpoint: '/api/auth/reset-password', method: 'POST',
    detectedAt: '2026.09.28 14:18', summary: '재설정 토큰의 만료 정책이 충분하지 않습니다.', evidence: '발급 후 24시간이 지난 토큰이 여전히 유효합니다.',
    impact: '노출된 토큰을 이용한 계정 탈취 가능성이 있습니다.', remediation: ['토큰 수명을 15분 이내로 제한합니다.'], references: ['OWASP A07:2021'],
  },
  {
    id: 'VULN-2026-005', title: '보안 헤더 미설정', category: 'Security Misconfiguration',
    severity: 'medium', cvss: 5.3, status: 'open', endpoint: '/', method: 'GET',
    detectedAt: '2026.09.28 14:10', summary: 'CSP와 X-Content-Type-Options 헤더가 설정되지 않았습니다.', evidence: '루트 응답 헤더 검사에서 두 항목이 누락되었습니다.',
    impact: '콘텐츠 주입 공격에 대한 브라우저 보호가 약화됩니다.', remediation: ['권장 보안 헤더를 웹 서버에 설정합니다.'], references: ['OWASP Secure Headers Project'],
  },
  {
    id: 'VULN-2026-006', title: '상세 오류 메시지 노출', category: 'Security Misconfiguration',
    severity: 'low', cvss: 3.1, status: 'resolved', endpoint: '/api/orders/{id}', method: 'GET',
    detectedAt: '2026.09.27 19:42', summary: '내부 스택 트레이스가 오류 응답에 포함됩니다.', evidence: '잘못된 주문 ID 요청 시 프레임워크 경로가 노출되었습니다.',
    impact: '공격자가 내부 기술 스택을 식별할 수 있습니다.', remediation: ['운영 환경에서 일반화된 오류 응답을 사용합니다.'], references: ['CWE-209'],
  },
];

/** @type {import('../types/models.js').Activity[]} */
export const recentActivities = [
  { id: 'a1', title: '정기 보안 분석 완료', description: '248개 페이지와 84개 API를 분석했습니다.', time: '8분 전', type: 'scan' },
  { id: 'a2', title: 'Critical 취약점 발견', description: '관리자 API에서 접근 제어 누락을 확인했습니다.', time: '9분 전', type: 'finding' },
  { id: 'a3', title: '취약점 조치 완료', description: '상세 오류 메시지 노출 항목이 해결되었습니다.', time: '어제', type: 'resolved' },
];

/** @type {import('../types/models.js').GraphNode[]} */
export const graphNodes = [
  { id: 'guest', label: 'Guest', type: 'role', x: 90, y: 92 },
  { id: 'user', label: 'User', type: 'role', x: 90, y: 245 },
  { id: 'admin', label: 'Admin', type: 'role', x: 90, y: 398 },
  { id: 'login', label: '/login', type: 'page', x: 335, y: 68 },
  { id: 'product', label: '/product', type: 'page', x: 335, y: 178 },
  { id: 'order', label: '/order', type: 'page', x: 335, y: 288 },
  { id: 'admin-page', label: '/admin', type: 'page', x: 335, y: 398 },
  { id: 'login-api', label: 'POST /api/login', type: 'endpoint', x: 620, y: 68 },
  { id: 'product-api', label: 'GET /api/products', type: 'endpoint', x: 620, y: 178 },
  { id: 'order-api', label: 'POST /api/order', type: 'endpoint', x: 620, y: 288 },
  { id: 'user-api', label: 'GET /api/users', type: 'endpoint', x: 620, y: 398 },
  { id: 'user-resource', label: 'User', type: 'resource', x: 880, y: 68 },
  { id: 'product-resource', label: 'Product', type: 'resource', x: 880, y: 178 },
  { id: 'order-resource', label: 'Order', type: 'resource', x: 880, y: 288 },
  { id: 'data-resource', label: 'User Data', type: 'resource', x: 880, y: 398 },
];

/** @type {import('../types/models.js').GraphLink[]} */
export const graphLinks = [
  { source: 'guest', target: 'login', relation: '' }, { source: 'guest', target: 'product', relation: '' },
  { source: 'user', target: 'login', relation: '' }, { source: 'user', target: 'product', relation: '' }, { source: 'user', target: 'order', relation: '' },
  { source: 'admin', target: 'product', relation: '' }, { source: 'admin', target: 'order', relation: '' }, { source: 'admin', target: 'admin-page', relation: '' },
  { source: 'login', target: 'login-api', relation: '' }, { source: 'login', target: 'product-api', relation: '' },
  { source: 'product', target: 'login-api', relation: '' }, { source: 'product', target: 'product-api', relation: '' }, { source: 'product', target: 'order-api', relation: '' },
  { source: 'order', target: 'product-api', relation: '' }, { source: 'order', target: 'order-api', relation: '' }, { source: 'order', target: 'user-api', relation: '' },
  { source: 'admin-page', target: 'login-api', relation: '' }, { source: 'admin-page', target: 'user-api', relation: '' },
  { source: 'login-api', target: 'user-resource', relation: '' }, { source: 'product-api', target: 'product-resource', relation: '' },
  { source: 'order-api', target: 'order-resource', relation: '' }, { source: 'user-api', target: 'data-resource', relation: '' },
];

export const graphLegend = [
  { type: 'role', label: 'User/Role' }, { type: 'page', label: 'Page' }, { type: 'endpoint', label: 'Endpoint' },
  { type: 'resource', label: 'Resource' },
];
