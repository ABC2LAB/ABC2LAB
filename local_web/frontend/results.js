"use strict";

(() => {
  const TABLE_ROW_LIMIT = 100;
  const GRAPH_NODE_LIMIT = 80;
  const GRAPH_EDGE_LIMIT = 240;
  const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
  const nodeTypes = ["User", "Role", "Page", "Action", "Endpoint", "Parameter", "Resource"];
  const countLabels = {
    roles: "역할", accounts: "계정", pages: "페이지", actions: "행동", requests: "관찰 요청",
    normalized_requests: "정규화 요청", nodes: "노드", relationships: "관계", workflows: "업무 흐름",
    candidate_count: "후보", confirmed_count: "확인", not_confirmed_count: "미재현",
    suspected_count: "의심", indeterminate_count: "판단불가", policy_blocked_count: "정책 차단",
    approval_pending_count: "승인 대기",
    policy_blocked: "정책 차단", approval_pending: "승인 대기", indeterminate: "판단불가",
  };
  const issueLabels = {
    ARTIFACT_INVALID: "표시 계약·JSON 형식 오류", ARTIFACT_UNAVAILABLE: "파일 조회 또는 JSON 확인 불가",
    ARTIFACT_TOO_LARGE: "Web 파일 크기 제한 초과", ARTIFACT_VIEW_LIMIT: "Web 항목 수 제한 초과",
    ARTIFACT_IDENTITY_MISMATCH: "버전·실행·작성자·회차 불일치", ARTIFACT_HASH_MISMATCH: "종료 통지의 파일 해시와 불일치",
    ARTIFACT_REFERENCE_MISMATCH: "공개 입력 참조·시나리오 해시 불일치", GRAPH_REFERENCE_INVALID: "그래프 ID 참조 오류",
    UNSAFE_EVIDENCE_REFERENCE: "마스킹되지 않은 근거 참조",
  };
  let currentRunId = null;
  let requestSequence = 0;

  function element(tag, content, className) {
    const result = document.createElement(tag);
    if (content !== undefined) result.textContent = String(content);
    if (className) result.className = className;
    return result;
  }

  function display(value) {
    if (value === null || value === undefined) return "미제공";
    return Array.isArray(value) ? value.join(" · ") || "없음" : String(value);
  }

  function stateText(artifact) {
    if (!artifact) return "진단 시작 전";
    if (artifact.state === "missing") return "미발행 · 처리 중/미실행 여부는 진행 화면에서 확인";
    if (artifact.state === "invalid") return "조회 불가 · " + (issueLabels[artifact.issue] || "산출물 확인 필요");
    if (artifact.state === "failed") return "failed · 사용할 결과 없음 · 오류 " + artifact.error_count + "건";
    return artifact.state + " · 작업 오류 " + artifact.error_count + "건" + (artifact.state === "partial" ? " · 부분 결과이며 미처리 범위가 있음" : "");
  }

  function table(target, caption, columns, rows) {
    if (!rows.length) {
      target.append(element("p", caption + " · 공개 결과 0건", "description"));
      return;
    }
    target.append(element("p", caption + " · 전체 " + rows.length + "건 · 표는 최대 " + TABLE_ROW_LIMIT + "건 표시", "description"));
    const wrapper = element("div", undefined, "table-scroll");
    wrapper.tabIndex = 0;
    wrapper.setAttribute("aria-label", caption + " 표 · 가로 스크롤 가능");
    const grid = element("table");
    grid.append(element("caption", caption));
    const head = element("thead");
    const heading = element("tr");
    columns.forEach(([title]) => {
      const cell = element("th", title);
      cell.scope = "col";
      heading.append(cell);
    });
    head.append(heading);
    const body = element("tbody");
    rows.slice(0, TABLE_ROW_LIMIT).forEach((row) => {
      const line = element("tr");
      columns.forEach(([, value]) => line.append(element("td", display(typeof value === "function" ? value(row) : row[value]))));
      body.append(line);
    });
    grid.append(head, body);
    wrapper.append(grid);
    target.append(wrapper);
  }

  function counts(target, values) {
    const grid = element("dl", undefined, "count-grid");
    Object.entries(values).forEach(([name, value]) => {
      const item = element("div");
      item.append(element("dt", countLabels[name] || name), element("dd", value));
      grid.append(item);
    });
    target.append(grid);
  }

  function svgElement(tag, attributes, content) {
    const node = document.createElementNS(SVG_NAMESPACE, tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function diagram(target, data) {
    const nodes = data.nodes.slice(0, GRAPH_NODE_LIMIT);
    const positions = new Map();
    const types = nodeTypes.filter((type) => nodes.some((node) => node.node_type === type));
    let height = 140;
    types.forEach((type, column) => {
      nodes.filter((node) => node.node_type === type).forEach((node, row) => {
        const position = { x: 90 + column * 180, y: 85 + row * 80 };
        positions.set(node.node_id, position);
        height = Math.max(height, position.y + 70);
      });
    });
    const width = Math.max(360, types.length * 180);
    const edges = data.relationships.filter((edge) => positions.has(edge.source_id) && positions.has(edge.target_id)).slice(0, GRAPH_EDGE_LIMIT);
    target.append(element("p", "그래프 도식: 노드 " + nodes.length + "/" + data.nodes.length + "개 · 관계 " + edges.length + "/" + data.relationships.length + "개. 표시 제한: " + GRAPH_NODE_LIMIT + "노드·" + GRAPH_EDGE_LIMIT + "관계. 전체 건수는 산출물 기준이며 아래 표도 표시 한도가 있다.", "description"));
    if (!nodes.length) {
      target.append(element("p", "유효한 snapshot의 노드 0개", "description"));
      return;
    }
    const canvas = element("div", undefined, "graph-canvas");
    canvas.tabIndex = 0;
    canvas.setAttribute("aria-label", "검증 전 Knowledge Graph · 가로 스크롤 가능");
    const svg = svgElement("svg", { viewBox: "0 0 " + width + " " + height, role: "img", "aria-label": "검증 전 실제 Knowledge Graph. 동일 노드·관계는 아래 표에서 확인할 수 있다." });
    svg.append(svgElement("title", {}, "structure_snapshot · 검증 전 그래프"));
    const definitions = svgElement("defs", {});
    const marker = svgElement("marker", { id: "graph-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto" });
    marker.append(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "graph-arrow" }));
    definitions.append(marker);
    svg.append(definitions);
    types.forEach((type, column) => svg.append(svgElement("text", { x: 90 + column * 180, y: 25, "text-anchor": "middle", class: "graph-heading" }, type)));
    edges.forEach((edge) => {
      const source = positions.get(edge.source_id);
      const destination = positions.get(edge.target_id);
      const distance = Math.hypot(destination.x - source.x, destination.y - source.y) || 1;
      const offsetX = (destination.x - source.x) * 26 / distance;
      const offsetY = (destination.y - source.y) * 26 / distance;
      const path = source === destination
        ? "M " + (source.x - 16) + " " + (source.y - 18) + " C " + (source.x - 70) + " " + (source.y - 65) + ", " + (source.x + 70) + " " + (source.y - 65) + ", " + (source.x + 16) + " " + (source.y - 18)
        : "M " + (source.x + offsetX) + " " + (source.y + offsetY) + " L " + (destination.x - offsetX) + " " + (destination.y - offsetY);
      const line = svgElement("path", { d: path, class: "graph-edge basis-" + edge.basis, "marker-end": "url(#graph-arrow)" });
      line.append(svgElement("title", {}, edge.relationship_id + " · " + edge.relation_type + " · " + edge.basis + " · " + edge.source_id + " → " + edge.target_id));
      svg.append(line);
    });
    nodes.forEach((node) => {
      const position = positions.get(node.node_id);
      const group = svgElement("g", { class: "graph-node node-" + node.node_type });
      group.append(svgElement("title", {}, node.node_id + " · " + node.node_type + " · " + node.basis + (node.resource_scope ? " · " + node.resource_scope : "")));
      group.append(svgElement("circle", { cx: position.x, cy: position.y, r: 22 }));
      group.append(svgElement("text", { x: position.x, y: position.y + 4, "text-anchor": "middle", class: "node-symbol" }, node.node_type[0]));
      const shortId = node.node_id.length > 21 ? node.node_id.slice(0, 20) + "…" : node.node_id;
      group.append(svgElement("text", { x: position.x, y: position.y + 39, "text-anchor": "middle", class: "node-label" }, shortId));
      svg.append(group);
    });
    canvas.append(svg);
    target.append(canvas, element("p", "basis: observed=관찰 · inferred=추론(점선) · verified=검증(굵은 선). 노드의 종류와 Resource type/instance는 표에서 확인한다.", "description"));
  }

  function renderGraph(artifact) {
    document.getElementById("graph-state").textContent = stateText(artifact);
    const target = document.getElementById("graph-view");
    target.replaceChildren();
    if (!artifact?.view) return;
    const data = artifact.view;
    target.append(element("p", data.graph_id + " · query revision " + data.graph_revision, "description"));
    if (data.snapshot_state !== "completed") {
      target.append(element("p", data.snapshot_state === "failed" ? "structure_snapshot 조회 실패 · 빈 그래프로 처리하지 않는다." : "structure_snapshot 결과 없음 · 빈 그래프로 처리하지 않는다.", "description"));
      return;
    }
    counts(target, { nodes: data.nodes.length, relationships: data.relationships.length, workflows: data.workflows.length });
    diagram(target, data);
    table(target, "KG 노드", [["node_id", "node_id"], ["종류", "node_type"], ["basis", "basis"], ["Resource 범위", "resource_scope"]], data.nodes);
    table(target, "KG 관계", [["관계 ID", "relationship_id"], ["source_id", "source_id"], ["target_id", "target_id"], ["관계", "relation_type"], ["basis", "basis"]], data.relationships);
    table(target, "업무 흐름", [["ID", "workflow_id"], ["이름", "name"], ["basis", "basis"], ["단계(명시된 order 순)", (row) => row.steps.map((step) => step.order + ": " + step.step_id + " · " + step.action).join(" → ")], ["의존 관계 수", "dependency_count"]], data.workflows);
  }

  function processPanel(target, title, artifact, columns, rows) {
    target.append(element("h4", title), element("p", stateText(artifact), "description"));
    if (artifact?.view) table(target, title, columns, rows);
  }

  function renderProcess(byType) {
    const target = document.getElementById("process-view");
    target.replaceChildren();
    const candidateArtifact = byType.get("vulnerability_candidates");
    const scenarioArtifact = byType.get("test_scenarios");
    const policyArtifact = byType.get("safety_decisions");
    const verifierArtifact = byType.get("verification_results");
    const scenarios = scenarioArtifact?.view?.scenarios || [];
    const decisions = policyArtifact?.view?.decisions || [];
    const results = verifierArtifact?.view?.results || [];
    const linked = (artifact, values) => artifact?.view ? values.join(" · ") || "연결된 항목 없음" : "공개 결과 확인 불가";
    processPanel(target, "검증 후보(확정 취약점 아님)", candidateArtifact,
      [["후보 ID", "candidate_id"], ["유형", "vulnerability_type"], ["실행 계정·역할", (row) => row.actor_account_id + " · " + row.actor_role_id], ["Resource ID", "resource_ids"], ["가설", "hypothesis"], ["기대 조건·근거", (row) => row.expected_behavior + " · " + row.expected_basis], ["시나리오", (row) => linked(scenarioArtifact, scenarios.filter((scenario) => scenario.candidate_id === row.candidate_id).map((scenario) => scenario.scenario_id))]], candidateArtifact?.view?.candidates || []);
    processPanel(target, "재현 시나리오", scenarioArtifact,
      [["시나리오 ID", "scenario_id"], ["후보 ID", "candidate_id"], ["Resource ID", "resource_ids"], ["계획 단계", (row) => row.steps.map((step) => step.order + ": " + step.step_id + " " + step.method + " · " + step.account_id + " · 상태 변경 " + step.state_change).join(" → ")], ["사전조건/판정조건 수", (row) => row.precondition_count + "/" + row.assertion_count], ["Policy", (row) => linked(policyArtifact, decisions.filter((decision) => decision.scenario_id === row.scenario_id).map((decision) => decision.decision_id + " " + decision.decision))], ["검증", (row) => linked(verifierArtifact, results.filter((result) => result.scenario_id === row.scenario_id && result.candidate_id === row.candidate_id).map((result) => result.verification_id + " " + result.result))]], scenarios);
    processPanel(target, "Safety Policy 판정", policyArtifact,
      [["판정 ID", "decision_id"], ["시나리오 ID", "scenario_id"], ["판정", "decision"], ["사유 코드", "reason_codes"], ["사유", "reason"]], decisions);
    processPanel(target, "실제 검증 결과", verifierArtifact,
      [["검증 ID", "verification_id"], ["후보/시나리오", (row) => row.candidate_id + " / " + row.scenario_id], ["Policy ID·판정", (row) => row.decision_id + " · " + row.policy_decision], ["실행 상태", "execution_status"], ["결과", "result"], ["사유", "reason"], ["실제 단계·HTTP 응답", (row) => row.steps.map((step) => step.step_id + " " + step.status + " · " + display(step.response_status)).join(" → ") || "실행 단계 없음"], ["오류 수", "error_count"]], results);
    const report = byType.get("diagnosis_report");
    target.append(element("h4", "Reporter 분류 요약 · 확정 외 상태 분리"), element("p", stateText(report), "description"));
    if (report?.view) {
      counts(target, report.view.summary);
      table(target, "확정 외 리포트 상태(확인된 취약점 목록에서 제외)", [["리포트 ID", "finding_id"], ["후보 ID", "candidate_id"], ["시나리오 ID", "scenario_id"], ["상태", "status"]], report.view.other_findings);
    }
  }

  function renderApproval(byType) {
    const artifact = byType.get("safety_decisions");
    document.getElementById("approval-state").textContent = stateText(artifact);
    const target = document.getElementById("approval-view");
    target.replaceChildren();
    if (!artifact?.view) return;
    const scenarios = new Map((byType.get("test_scenarios")?.view?.scenarios || []).map((row) => [row.scenario_id, row]));
    table(target, "현재 Policy 산출물의 승인 대기", [["판정 ID", "decision_id"], ["시나리오 ID", "scenario_id"], ["후보 ID", (row) => scenarios.get(row.scenario_id)?.candidate_id || "공개 시나리오 확인 불가"], ["사유 코드", "reason_codes"], ["사유", "reason"]], artifact.view.decisions.filter((row) => row.decision === "require_approval"));
  }

  function renderReport(artifact) {
    document.getElementById("report-state").textContent = stateText(artifact);
    const target = document.getElementById("report-view");
    target.replaceChildren();
    if (!artifact?.view) return;
    const view = artifact.view;
    target.append(element("p", "Reporter confirmed_count: " + view.summary.confirmed_count + "건. 분류별 집계·확정 외 상태는 진단 과정에서 확인한다.", "description"));
    table(target, "확인된 취약점", [["리포트 ID", "finding_id"], ["제목", "title"], ["분류·유형", (row) => row.category + " · " + row.vulnerability_type], ["후보/시나리오/검증 ID", (row) => row.candidate_id + " / " + display(row.scenario_id) + " / " + display(row.verification_id)], ["설명", "description"], ["마스킹된 근거 참조(본문·파일 검증 미제공)", (row) => row.evidence_refs.map((ref) => ref.evidence_id + " · " + ref.kind + " · SHA-256 " + ref.sha256).join("\n") || "근거 참조 없음"]], view.confirmed_findings);
    target.append(element("h4", "Reporter가 기록한 진단 한계"));
    const limitations = element("ul", undefined, "status-list");
    view.limitations.forEach((value) => limitations.append(element("li", value)));
    if (!view.limitations.length) limitations.append(element("li", "이 산출물에 기록된 추가 한계 없음 · 전체 검증 완료를 의미하지 않는다."));
    target.append(limitations);
  }

  function renderEvaluation(artifact) {
    document.getElementById("evaluation-state").textContent = stateText(artifact);
    const target = document.getElementById("evaluation-view");
    target.replaceChildren();
    if (!artifact?.view) return;
    const view = artifact.view;
    target.append(element("h4", "평가 조건"));
    table(target, "평가 기준", [["데이터셋", "dataset_id"], ["버전", "dataset_version"],
      ["정답 SHA-256(원문 비열람)", "sha256"], ["KG revision", () => view.source_graph_revision],
      ["매칭 프로필", () => view.matching_profile]], [view.dataset]);
    const ratioText = (row) => row.value === null ? "미측정 / 계산 불가" : String(row.value) + " (" + (row.value * 100).toFixed(2) + "%)";
    target.append(element("h4", "성능 지표"));
    table(target, "Reporter 성능 지표", [["지표 ID", "metric_id"], ["비율(Reporter 원본 값)", ratioText],
      ["분자", "numerator"], ["분모", "denominator"]], view.metrics);
    target.append(element("p", "null은 미측정 또는 계산 불가이며 0점이 아니다. 비율은 Reporter 값 그대로 표시하고 분자/분모에서 다시 계산하지 않는다.", "description"));
    table(target, "후보·검증 확정 TP/FP/FN", [["구분", "stage"], ["TP", "tp"], ["FP", "fp"], ["FN", "fn"]],
      [{ stage: "후보 생성", ...view.candidate_counts }, { stage: "검증 확정", ...view.confirmed_counts }]);
    target.append(element("h4", "미검증 집계"));
    counts(target, view.unverified_counts);
    target.append(element("p", "정책 차단·승인 대기·판단불가는 확정 TP나 취약점 없음으로 합치지 않는다. 각 집계는 Reporter 산출물 기준이다.", "description"));
    const measured = (value) => value === null ? "미측정" : String(value);
    table(target, "사용 모델·생성 조건", [["모듈", "module"], ["모델", "model_id"], ["모델 버전", "model_version"],
      ["프롬프트 버전", "prompt_version"], ["temperature", (row) => measured(row.temperature)],
      ["seed", (row) => measured(row.seed)]], Object.entries(view.models).map(([module, model]) => ({ module, ...model })));
    const runtimeNames = { duration_ms: "실행 시간(ms)", llm_calls: "LLM 호출 수", input_tokens: "입력 토큰",
      output_tokens: "출력 토큰", peak_memory_mb: "최대 메모리(MB)" };
    table(target, "실행 측정값", [["항목", "label"], ["값", (row) => measured(row.value)]],
      Object.entries(view.run_metrics).map(([name, value]) => ({ label: runtimeNames[name], value })));
    target.append(element("h4", "평가 조건·한계"));
    const notes = element("ul", undefined, "status-list");
    view.notes.forEach((note) => notes.append(element("li", note)));
    if (!view.notes.length) notes.append(element("li", "기록된 추가 설명 없음 · 전체 검증 완료를 의미하지 않는다."));
    target.append(notes);
  }

  function render(payload) {
    const byType = new Map(payload.artifacts.map((artifact) => [artifact.artifact_type, artifact]));
    for (const [prefix, name] of [["crawl", "crawl_result"], ["semantic", "semantic_analysis"]]) {
      const artifact = byType.get(name);
      document.getElementById(prefix + "-state").textContent = stateText(artifact);
      const target = document.getElementById(prefix + "-view");
      target.replaceChildren();
      if (artifact?.view) {
        counts(target, artifact.view.counts);
        if (artifact.view.node_types) counts(target, artifact.view.node_types);
      }
    }
    renderGraph(byType.get("graph_query_result"));
    renderProcess(byType);
    renderApproval(byType);
    renderReport(byType.get("diagnosis_report"));
    renderEvaluation(byType.get("evaluation_results"));
  }

  function setRun(run) {
    const nextId = run.run_id || null;
    if (nextId !== currentRunId) {
      currentRunId = nextId;
      requestSequence += 1;
      render({ artifacts: [] });
      document.getElementById("results-status").textContent = nextId ? nextId + " · 결과 조회 대기" : "소유 진단 없음 · 다른 브라우저 결과는 표시하지 않는다.";
    }
  }

  async function refresh() {
    const sequence = ++requestSequence;
    try {
      const response = await fetch("/api/run-results", { cache: "no-store", mode: "same-origin", credentials: "same-origin" });
      if (!response.ok) throw new Error("Results unavailable");
      const payload = await response.json();
      if (sequence !== requestSequence || (currentRunId && payload.run_id !== currentRunId)) return;
      currentRunId = payload.run_id || null;
      render(payload);
      document.getElementById("results-status").textContent = payload.state === "idle"
        ? "소유 진단 없음 · 다른 브라우저 결과는 표시하지 않는다."
        : payload.run_id + " · " + payload.state + (payload.state === "running" ? " · 원자 발행 파일의 부분 조회. 종료 통지·해시는 아직 미확정." : " · 공개 산출물 조회. Web은 진단을 재판정하지 않는다.");
    } catch {
      if (sequence !== requestSequence) return;
      render({ artifacts: [] });
      for (const id of ["results-status", "approval-state", "evaluation-state"]) document.getElementById(id).textContent = "결과 조회 실패 · 0건 또는 진단 성공으로 처리하지 않는다.";
    }
  }

  window.abc2labResults = { setRun, refresh };
  document.getElementById("refresh-results").addEventListener("click", refresh);
  document.getElementById("refresh-evaluation").addEventListener("click", refresh);
  if (["results", "approval", "evaluation"].includes(location.hash.slice(1))) refresh();
})();
