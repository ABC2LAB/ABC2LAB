"use strict";

(() => {
  const TABLE_ROW_LIMIT = 100;
  const GRAPH_PAGE_SIZE = 50;
  const GRAPH_CELL_SIZE = 150;
  const GRAPH_MAX_COLUMNS = 8;
  const GRAPH_PADDING = 55;
  const GRAPH_MIN_VIEW_WIDTH = 100;
  const WORKFLOW_PAGE_SIZE = 10;
  const WORKFLOW_ROW_HEIGHT = 100;
  const WORKFLOW_DIAGRAM_WIDTH = 640;
  const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
  const nodeTypes = ["User", "Role", "Page", "Action", "Endpoint", "Parameter", "Resource"];
  const access_node_types = new Set(["User", "Role", "Resource"]);
  const graph_view_labels = { access: "접근 관계", candidate: "후보 중심", workflow: "Workflow", all: "전체 원본" };
  const countLabels = {
    roles: "역할", accounts: "계정", pages: "페이지", actions: "행동", requests: "관찰 요청",
    normalized_requests: "정규화 요청", nodes: "노드", relationships: "관계", workflows: "업무 흐름",
    candidate_count: "후보", confirmed_count: "확인", not_confirmed_count: "미재현",
    suspected_count: "의심", indeterminate_count: "판단불가", policy_blocked_count: "정책 차단",
    approval_pending_count: "승인 대기",
    verification_success_count: "검증 조건 통과", verification_failure_count: "검증 미재현",
    verification_indeterminate_count: "검증 판단불가", verification_blocked_count: "검증 미실행·차단",
    reporter_confirmed_count: "Reporter 확정",
    policy_blocked: "정책 차단", approval_pending: "승인 대기", indeterminate: "판단불가",
  };
  const issueLabels = {
    ARTIFACT_INVALID: "표시 계약·JSON 형식 오류", ARTIFACT_UNAVAILABLE: "파일 조회 또는 JSON 확인 불가",
    ARTIFACT_TOO_LARGE: "Web 파일 크기 제한 초과", ARTIFACT_VIEW_LIMIT: "Web 항목 수 제한 초과",
    ARTIFACT_IDENTITY_MISMATCH: "버전·실행·작성자·회차 불일치", ARTIFACT_HASH_MISMATCH: "종료 통지의 파일 해시와 불일치",
    ARTIFACT_REFERENCE_MISMATCH: "공개 입력 참조·시나리오 해시 불일치", GRAPH_REFERENCE_INVALID: "그래프 ID 참조 오류",
    UNSAFE_EVIDENCE_REFERENCE: "마스킹되지 않은 근거 참조",
  };
  const evaluation_metric_definitions = {
    candidate_precision: { group: "detection", label: "후보 정밀도", numerator: "정답 일치", denominator: "생성 후보",
      description: "생성한 후보 중 정답 양성과 일치한 비율" },
    candidate_recall: { group: "detection", label: "후보 재현율",
      description: "정답 취약점 중 후보로 찾아낸 비율" },
    confirmed_precision: { group: "detection", label: "확정 정밀도", numerator: "정답 일치", denominator: "확정 후보",
      description: "Reporter 확정 후보 중 정답 양성과 일치한 비율" },
    confirmed_recall: { group: "detection", label: "확정 재현율",
      description: "정답 취약점 중 Reporter가 확정한 비율" },
    user_recall: { group: "structure", label: "사용자 재현율", description: "정답 User와 일치한 사용자 비율" },
    role_recall: { group: "structure", label: "역할 재현율", description: "정답 Role과 일치한 역할 비율" },
    page_recall: { group: "structure", label: "페이지 재현율", description: "정답 경로와 일치한 수집 페이지 비율" },
    action_recall: { group: "structure", label: "행동 재현율", description: "정답 Action과 일치한 행동 비율" },
    endpoint_recall: { group: "structure", label: "엔드포인트 재현율", description: "정답 HTTP 메서드·경로와 일치한 비율" },
    parameter_recall: { group: "structure", label: "파라미터 재현율", description: "정답 엔드포인트·이름·위치와 일치한 비율" },
    resource_recall: { group: "structure", label: "자원 재현율", description: "정답 Resource와 일치한 자원 비율" },
    relationship_recall: { group: "structure", label: "관계 재현율", description: "정답 양 끝 노드·관계 종류·방향과 일치한 비율" },
    workflow_recall: { group: "workflow", label: "전체 흐름 재현율", numerator: "일치 흐름", denominator: "정답 흐름" },
    workflow_step_recall: { group: "workflow", label: "단계 구성 재현율", numerator: "일치 단계", denominator: "정답 단계",
      description: "선택한 관찰 흐름에서 순서와 무관하게 일치한 단계 비율 · 반복 횟수 포함" },
    workflow_ordered_step_recall: { group: "workflow", label: "단계 순서 재현율", numerator: "일치 단계", denominator: "정답 단계",
      description: "같은 관찰 흐름에서 순서까지 일치한 단계 비율 · 최장 공통 부분수열(LCS) 기준" },
  };
  let currentRunId = null;
  let requestSequence = 0;
  let graph_signature;
  let graph_key = null;
  let graph_state = create_graph_state();
  const disclosure_states = new Map();
  let disclosure_run_id;
  const badge_states = new Set(["completed", "partial", "failed", "missing", "invalid", "success", "failure", "blocked",
    "indeterminate", "confirmed", "suspected", "not_confirmed", "policy_blocked", "approval_pending", "require_approval",
    "allow", "block", "not_executed", "error", "observed", "inferred", "verified"]);

  function create_graph_state() {
    return { view_mode: "access", candidate_id: "", expanded_ids: [],
      node_type: "all", basis: "all", resource_scope: "all", search: "",
      is_connected_only: false, is_neighborhood: false, selected_id: null, camera: null,
      node_page: 0, edge_page: 0, related_page: 0, workflow_page: 0,
      workflow: { workflow_id: "", selected_step_id: null, step_page: 0, dependency_page: 0 } };
  }

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

  function status_badge(value) {
    const badge = element("span", value, "status-pill");
    badge.dataset.state = badge_states.has(value) ? value : "missing";
    return badge;
  }

  function empty_state(target, message) {
    const section = element("div", undefined, "empty-state");
    const symbol = element("span", "◎", "empty-symbol");
    symbol.setAttribute("aria-hidden", "true");
    section.append(symbol, element("p", message));
    target.append(section);
  }

  function disclosure(key, title, is_open = false) {
    const wrapper = element("details", undefined, "data-disclosure");
    wrapper.dataset.panel = key;
    wrapper.open = disclosure_states.get(key) ?? is_open;
    const summary = element("summary", title);
    const body = element("div", undefined, "disclosure-body");
    wrapper.append(summary, body);
    wrapper.addEventListener("toggle", () => {
      if (wrapper.isConnected) disclosure_states.set(key, wrapper.open);
    });
    return { wrapper, summary, body };
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
      empty_state(target, caption + " · 공개 결과 0건");
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
      columns.forEach(([, value]) => {
        const content = typeof value === "function" ? value(row) : row[value];
        const cell = element("td");
        if (badge_states.has(content)) cell.append(status_badge(content));
        else cell.textContent = display(content);
        line.append(cell);
      });
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
      item.dataset.metric = name;
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

  function graph_button(label, action) {
    const button = element("button", label, "secondary");
    button.type = "button";
    button.addEventListener("click", action);
    return button;
  }

  function graph_neighbors(data, node_id) {
    const ids = new Set([node_id]);
    data.relationships.forEach((edge) => {
      if (edge.source_id === node_id) ids.add(edge.target_id);
      if (edge.target_id === node_id) ids.add(edge.source_id);
    });
    return ids;
  }

  function graph_scope_ids(data, state, candidates) {
    let ids;
    if (state.is_neighborhood) ids = graph_neighbors(data, state.selected_id);
    else if (state.view_mode === "all") ids = new Set(data.nodes.map((node) => node.node_id));
    else if (state.view_mode === "access") ids = new Set(data.nodes.filter((node) => access_node_types.has(node.node_type)).map((node) => node.node_id));
    else {
      const candidate = candidates.find((row) => row.candidate_id === state.candidate_id);
      const resources = new Set(candidate?.resource_ids || []);
      ids = new Set(data.nodes.filter((node) => node.node_type === "Resource" && resources.has(node.node_id)).map((node) => node.node_id));
      const seeds = new Set(ids);
      data.relationships.forEach((edge) => {
        if (seeds.has(edge.source_id)) ids.add(edge.target_id);
        if (seeds.has(edge.target_id)) ids.add(edge.source_id);
      });
    }
    const neighbors_by_id = new Map(data.nodes.map((node) => [node.node_id, new Set()]));
    data.relationships.forEach((edge) => {
      neighbors_by_id.get(edge.source_id)?.add(edge.target_id);
      neighbors_by_id.get(edge.target_id)?.add(edge.source_id);
    });
    state.expanded_ids.forEach((id) => {
      if (ids.has(id)) neighbors_by_id.get(id)?.forEach((neighbor) => ids.add(neighbor));
    });
    return ids;
  }

  function filter_graph_nodes(data, state, candidates) {
    const connected_ids = new Set(data.relationships.flatMap((edge) => [edge.source_id, edge.target_id]));
    const scoped_ids = graph_scope_ids(data, state, candidates);
    const search = state.search.trim().toLowerCase();
    return data.nodes.filter((node) => scoped_ids.has(node.node_id)
      && (state.node_type === "all" || node.node_type === state.node_type)
      && (state.basis === "all" || node.basis === state.basis)
      && (state.resource_scope === "all" || (node.node_type === "Resource" && node.resource_scope === state.resource_scope))
      && (!search || node.node_id.toLowerCase().includes(search))
      && (!state.is_connected_only || connected_ids.has(node.node_id)));
  }

  function append_graph_table(target, options) {
    const { caption, columns, rows, page_key } = options;
    const section = element("div", undefined, "graph-table");
    const body = element("div");
    const navigation = element("div", undefined, "graph-pagination");
    navigation.setAttribute("role", "group");
    navigation.setAttribute("aria-label", caption + " 페이지 이동");
    function render_page() {
      body.replaceChildren();
      navigation.replaceChildren();
      const page_count = Math.max(1, Math.ceil(rows.length / GRAPH_PAGE_SIZE));
      graph_state[page_key] = Math.min(graph_state[page_key], page_count - 1);
      const page = graph_state[page_key];
      const start = page * GRAPH_PAGE_SIZE;
      const page_rows = rows.slice(start, start + GRAPH_PAGE_SIZE);
      table(body, caption, columns, page_rows);
      const description = body.querySelector("p");
      description.textContent = caption + " · 조건에 맞는 전체 " + rows.length + "건 · "
        + (rows.length ? start + 1 : 0) + "–" + Math.min(start + GRAPH_PAGE_SIZE, rows.length) + "건 표시";
      [["처음", 0], ["이전", page - 1], ["다음", page + 1], ["마지막", page_count - 1]].forEach(([label, next_page]) => {
        const button = graph_button(label, () => {
          graph_state[page_key] = next_page;
          render_page();
          const focused = Array.from(navigation.querySelectorAll("button")).find((value) => value.textContent === label);
          if (!focused.disabled) focused.focus();
          else {
            const status = navigation.querySelector('[role="status"]');
            status.tabIndex = -1;
            status.focus();
          }
        });
        button.disabled = next_page < 0 || next_page >= page_count || next_page === page;
        navigation.append(button);
      });
      const page_label = element("span", (page + 1) + " / " + page_count + " 페이지", "description");
      page_label.setAttribute("role", "status");
      navigation.append(page_label);
      if (options.on_select) {
        body.querySelectorAll("tbody tr").forEach((line, index) => {
          const cell = element("td");
          const button = graph_button("노드 선택", () => options.on_select(page_rows[index].node_id, true));
          button.setAttribute("aria-label", "노드 " + page_rows[index].node_id + " 선택");
          cell.append(button);
          line.append(cell);
        });
        body.querySelector("thead tr")?.append(element("th", "탐색"));
      }
    }
    section.append(body, navigation);
    target.append(section);
    render_page();
  }

  function layout_graph_nodes(nodes, edges) {
    const positions = new Map();
    const headings = [];
    const ranks = new Map();
    const neighbors = new Map(nodes.map((node) => [node.node_id, []]));
    edges.forEach((edge) => {
      neighbors.get(edge.source_id).push(edge.target_id);
      neighbors.get(edge.target_id).push(edge.source_id);
    });
    const groups = nodeTypes.map((type) => nodes.filter((node) => node.node_type === type)
      .sort((left, right) => left.node_id.localeCompare(right.node_id)));
    const columns_for = (count) => count <= GRAPH_MAX_COLUMNS ? 1 : Math.min(GRAPH_MAX_COLUMNS, Math.ceil(Math.sqrt(count)));
    const max_rows = Math.max(1, ...groups.map((group) => Math.ceil(group.length / columns_for(group.length))));
    groups.forEach((group) => group.forEach((node, index) => ranks.set(node.node_id, index / Math.max(1, group.length - 1))));
    // 원본 관계로 배치 순서만 정한다. 관계의 의미·방향·유무는 바꾸지 않는다.
    const scores = new Map(nodes.map((node) => {
      const values = neighbors.get(node.node_id);
      return [node.node_id, values.length ? values.reduce((sum, id) => sum + ranks.get(id), 0) / values.length : ranks.get(node.node_id)];
    }));
    let left = GRAPH_PADDING;
    let height = 160;
    groups.forEach((group, group_index) => {
      if (!group.length) return;
      const type = nodeTypes[group_index];
      group.sort((first, second) => scores.get(first.node_id) - scores.get(second.node_id)
        || first.node_id.localeCompare(second.node_id));
      const columns = columns_for(group.length);
      const top = 85 + (max_rows - Math.ceil(group.length / columns)) * GRAPH_CELL_SIZE / 2;
      headings.push({ type, x: left, count: group.length });
      group.forEach((node, index) => {
        const position = { x: left + (index % columns) * GRAPH_CELL_SIZE,
          y: top + Math.floor(index / columns) * GRAPH_CELL_SIZE };
        positions.set(node.node_id, position);
        height = Math.max(height, position.y + GRAPH_PADDING);
      });
      left += columns * GRAPH_CELL_SIZE + GRAPH_PADDING;
    });
    return { positions, headings, width: Math.max(360, left), height };
  }

  function append_graph_edge(svg, options) {
    const { edge, positions, parallel_index, is_context, is_muted } = options;
    const source = positions.get(edge.source_id);
    const destination = positions.get(edge.target_id);
    const distance = Math.hypot(destination.x - source.x, destination.y - source.y) || 1;
    const offset_x = (destination.x - source.x) * 26 / distance;
    const offset_y = (destination.y - source.y) * 26 / distance;
    const bend = 22 + parallel_index * 30;
    const direction = edge.source_id < edge.target_id ? 1 : -1;
    const control = { x: (source.x + destination.x) / 2 - (destination.y - source.y) / distance * bend * direction,
      y: (source.y + destination.y) / 2 + (destination.x - source.x) / distance * bend * direction };
    const is_loop = edge.source_id === edge.target_id;
    const path = is_loop
      ? "M " + (source.x - 16) + " " + (source.y - 18) + " C " + (source.x - 70 - bend) + " " + (source.y - 65 - bend)
        + ", " + (source.x + 70 + bend) + " " + (source.y - 65 - bend) + ", " + (source.x + 16) + " " + (source.y - 18)
      : "M " + (source.x + offset_x) + " " + (source.y + offset_y) + " Q " + control.x + " " + control.y + " "
        + (destination.x - offset_x) + " " + (destination.y - offset_y);
    const line = svgElement("path", { d: path, class: "graph-edge basis-" + edge.basis + (is_context ? " is-selected" : "") + (is_muted ? " is-muted" : ""),
      "data-edge-id": edge.relationship_id, "marker-end": "url(#graph-arrow)" });
    line.append(svgElement("title", {}, edge.relationship_id + " · " + edge.relation_type + " · "
      + edge.basis + " · " + edge.source_id + " → " + edge.target_id));
    svg.append(line);
    if (graph_state.view_mode !== "all" || is_context) {
      const label = svgElement("text", { x: is_loop ? source.x : (source.x + destination.x + 2 * control.x) / 4,
        y: is_loop ? source.y - 65 - bend : (source.y + destination.y + 2 * control.y) / 4 - 7,
        "text-anchor": "middle", class: "graph-edge-label" + (is_muted ? " is-muted" : ""),
        "data-edge-id": edge.relationship_id }, edge.relation_type);
      svg.append(label);
    }
  }

  function append_graph_node(svg, options) {
    const { node, position, on_select, is_muted, is_candidate } = options;
    const is_selected = node.node_id === graph_state.selected_id;
    const group = svgElement("g", { class: "graph-node node-" + node.node_type + (is_selected ? " is-selected" : "")
      + (is_muted ? " is-muted" : "") + (is_candidate ? " is-candidate" : ""),
      role: "button", tabindex: 0, "aria-label": node.node_type + " " + node.node_id,
      "aria-pressed": is_selected, "data-node-id": node.node_id });
    group.append(svgElement("title", {}, node.node_id + " · " + node.node_type + " · " + node.basis
      + (node.resource_scope ? " · " + node.resource_scope : "") + (is_candidate ? " · 선택 후보 관련 자원(확정 아님)" : "")));
    group.append(svgElement("circle", { cx: position.x, cy: position.y, r: 22 }));
    const symbols = { User: "U", Role: "R", Page: "Pg", Action: "A", Endpoint: "E", Parameter: "P", Resource: "Rs" };
    group.append(svgElement("text", { x: position.x, y: position.y + 4, "text-anchor": "middle", class: "node-symbol" }, symbols[node.node_type]));
    const short_id = node.node_id.length > 22 ? node.node_id.slice(0, 10) + "…" + node.node_id.slice(-9) : node.node_id;
    group.append(svgElement("text", { x: position.x, y: position.y + 39, "text-anchor": "middle", class: "node-label" }, short_id));
    group.addEventListener("click", () => on_select(node.node_id, false));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        on_select(node.node_id, false);
      }
    });
    svg.append(group);
  }

  function attach_graph_camera(svg, layout) {
    const original = { x: -GRAPH_PADDING, y: -GRAPH_PADDING,
      width: layout.width + GRAPH_PADDING * 2, height: layout.height + GRAPH_PADDING * 2 };
    if (!graph_state.camera) graph_state.camera = { ...original };
    function apply_camera() {
      const camera = graph_state.camera;
      svg.setAttribute("viewBox", [camera.x, camera.y, camera.width, camera.height].join(" "));
    }
    function zoom(factor, anchor) {
      const camera = graph_state.camera;
      const width = Math.max(GRAPH_MIN_VIEW_WIDTH, Math.min(original.width * 4, camera.width * factor));
      const ratio = width / camera.width;
      const center = anchor || { x: camera.x + camera.width / 2, y: camera.y + camera.height / 2 };
      graph_state.camera = { x: center.x - (center.x - camera.x) * ratio,
        y: center.y - (center.y - camera.y) * ratio, width, height: camera.height * ratio };
      apply_camera();
    }
    let drag = null;
    svg.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.target.closest(".graph-node")) return;
      const matrix = svg.getScreenCTM()?.inverse();
      if (!matrix) return;
      drag = { pointer_id: event.pointerId, point: new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix),
        matrix, camera: { ...graph_state.camera } };
      svg.setPointerCapture(event.pointerId);
      svg.classList.add("is-dragging");
    });
    svg.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.pointer_id) return;
      const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(drag.matrix);
      graph_state.camera = { ...drag.camera, x: drag.camera.x - (point.x - drag.point.x),
        y: drag.camera.y - (point.y - drag.point.y) };
      apply_camera();
    });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach((name) => svg.addEventListener(name, () => {
      drag = null;
      svg.classList.remove("is-dragging");
    }));
    svg.addEventListener("wheel", (event) => {
      const matrix = svg.getScreenCTM()?.inverse();
      if (!matrix) return;
      event.preventDefault();
      zoom(event.deltaY < 0 ? 0.8 : 1.25, new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix));
    }, { passive: false });
    svg.addEventListener("keydown", (event) => {
      if (event.target.closest(".graph-node")) return;
      const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
      if (!directions[event.key]) return;
      event.preventDefault();
      const [x, y] = directions[event.key];
      graph_state.camera.x += x * graph_state.camera.width / 10;
      graph_state.camera.y += y * graph_state.camera.height / 10;
      apply_camera();
    });
    apply_camera();
    return { zoom, fit: () => { graph_state.camera = { ...original }; apply_camera(); },
      center: (node_id) => {
        const position = layout.positions.get(node_id);
        if (!position) return;
        const width = Math.min(original.width, 550);
        const height = width * svg.clientHeight / Math.max(1, svg.clientWidth);
        graph_state.camera = { x: position.x - width / 2, y: position.y - height / 2, width, height };
        apply_camera();
      } };
  }

  function diagram(target, options) {
    const { nodes, edges, on_select, candidate_ids } = options;
    if (!nodes.length) {
      target.append(element("p", "현재 조건에 맞는 노드 0개 · 원본 snapshot의 전체 건수와 구분한다.", "description"));
      return null;
    }
    const layout = layout_graph_nodes(nodes, edges);
    const canvas = element("div", undefined, "graph-canvas");
    canvas.setAttribute("role", "region");
    canvas.setAttribute("aria-label", "검증 전 Knowledge Graph 탐색 화면");
    const svg = svgElement("svg", { role: "group", tabindex: 0, "aria-label": "실제 Knowledge Graph · 방향키로 이동",
      "aria-describedby": "kg-navigation-help" });
    svg.append(svgElement("title", {}, "structure_snapshot · 검증 전 그래프"));
    const definitions = svgElement("defs", {});
    const marker = svgElement("marker", { id: "graph-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5,
      markerWidth: 6, markerHeight: 6, orient: "auto" });
    marker.append(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "graph-arrow" }));
    definitions.append(marker);
    svg.append(definitions);
    layout.headings.forEach((heading) => svg.append(svgElement("text", { x: heading.x, y: 25,
      class: "graph-heading" }, heading.type + " · " + heading.count)));
    const has_selection = layout.positions.has(graph_state.selected_id);
    const context_ids = new Set(has_selection ? [graph_state.selected_id] : []);
    const parallel_counts = new Map();
    edges.forEach((edge) => {
      const is_context = has_selection && (edge.source_id === graph_state.selected_id || edge.target_id === graph_state.selected_id);
      if (is_context) { context_ids.add(edge.source_id); context_ids.add(edge.target_id); }
      const key = JSON.stringify([edge.source_id, edge.target_id].sort());
      const parallel_index = parallel_counts.get(key) || 0;
      parallel_counts.set(key, parallel_index + 1);
      append_graph_edge(svg, { edge, positions: layout.positions, parallel_index, is_context, is_muted: has_selection && !is_context });
    });
    nodes.forEach((node) => append_graph_node(svg, { node, position: layout.positions.get(node.node_id), on_select,
      is_muted: has_selection && !context_ids.has(node.node_id), is_candidate: node.node_type === "Resource" && candidate_ids.has(node.node_id) }));
    canvas.append(svg);
    target.append(canvas);
    return attach_graph_camera(svg, layout);
  }

  function append_graph_filters(target, data, on_change) {
    const controls = element("div", undefined, "graph-filters");
    const type_counts = new Map(nodeTypes.map((type) => [type, data.nodes.filter((node) => node.node_type === type).length]));
    const inputs = new Map();
    const fields = [
      ["node_type", "노드 종류", [["all", "전체 종류"], ...nodeTypes.map((type) => [type, type + " (" + type_counts.get(type) + ")"])]],
      ["basis", "노드 basis", [["all", "전체 근거"], ["observed", "observed · 관찰"], ["inferred", "inferred · 추론"], ["verified", "verified · 검증"]]],
      ["resource_scope", "Resource 범위", [["all", "전체 범위"], ["type", "type · 자원 종류"], ["instance", "instance · 자원 인스턴스"]]],
    ];
    fields.forEach(([key, title, choices]) => {
      const field = element("div");
      const label = element("label", title);
      const select = element("select");
      select.id = "kg-" + key;
      label.htmlFor = select.id;
      choices.forEach(([value, text]) => {
        const option = element("option", text);
        option.value = value;
        select.append(option);
      });
      select.value = graph_state[key];
      select.addEventListener("change", () => { graph_state[key] = select.value; on_change(); });
      inputs.set(key, select);
      field.append(label, select);
      controls.append(field);
    });
    const search_field = element("div");
    const search_label = element("label", "node_id 검색");
    const search = element("input");
    search.id = "kg-search";
    search.type = "search";
    search.maxLength = 8192;
    search.autocomplete = "off";
    search.value = graph_state.search;
    search_label.htmlFor = search.id;
    search.addEventListener("input", () => { graph_state.search = search.value; on_change(); });
    inputs.set("search", search);
    search_field.append(search_label, search);
    controls.append(search_field);
    const connected_label = element("label", undefined, "graph-checkbox");
    const connected = element("input");
    connected.type = "checkbox";
    connected.checked = graph_state.is_connected_only;
    connected.addEventListener("change", () => { graph_state.is_connected_only = connected.checked; on_change(); });
    inputs.set("is_connected_only", connected);
    connected_label.append(connected, document.createTextNode("연결된 노드만 보기"));
    controls.append(connected_label);
    target.append(controls);
    return () => inputs.forEach((input, key) => {
      if (input.type === "checkbox") input.checked = graph_state[key];
      else input.value = graph_state[key];
    });
  }

  function render_graph_detail(target, options) {
    const { data, visible_ids, on_select } = options;
    const node = data.nodes.find((value) => value.node_id === graph_state.selected_id);
    target.append(element("h4", "선택 노드"));
    if (!node) {
      target.append(element("p", "그래프 또는 노드 표에서 노드를 선택하면 원본 연결 관계를 확인할 수 있다.", "description"));
      return;
    }
    const details = element("dl", undefined, "graph-node-details");
    [["node_id", node.node_id], ["종류", node.node_type], ["basis", node.basis],
      ["Resource 범위", node.resource_scope || "해당 없음"], ["화면 표시", visible_ids.has(node.node_id) ? "표시 중" : "현재 필터에서 제외됨"]]
      .forEach(([label, value]) => details.append(element("dt", label), element("dd", value)));
    target.append(details);
    const edges = data.relationships.filter((edge) => edge.source_id === node.node_id || edge.target_id === node.node_id);
    const neighbors = graph_neighbors(data, node.node_id);
    neighbors.delete(node.node_id);
    target.append(element("p", "원본 연결 관계 " + edges.length + "개 · 다른 연결 노드 " + neighbors.size + "개", "description"));
    if (!edges.length) target.append(element("p", "원본 snapshot에서 연결 관계가 없는 노드다. 누락이나 오류로 처리하지 않는다.", "description"));
    const neighbor_select = element("select");
    neighbor_select.id = "kg-neighbor";
    const label = element("label", "연결 노드로 이동");
    label.htmlFor = neighbor_select.id;
    const placeholder = element("option", "연결 노드 선택");
    placeholder.value = "";
    neighbor_select.append(placeholder);
    data.nodes.filter((value) => neighbors.has(value.node_id)).forEach((value) => {
      const option = element("option", value.node_type + " · " + value.node_id);
      option.value = value.node_id;
      neighbor_select.append(option);
    });
    neighbor_select.disabled = neighbors.size === 0;
    neighbor_select.addEventListener("change", () => { if (neighbor_select.value) on_select(neighbor_select.value, true); });
    target.append(label, neighbor_select);
    append_graph_table(target, { caption: "선택 노드의 원본 관계", rows: edges, page_key: "related_page",
      columns: [["관계", "relation_type"], ["source_id", "source_id"], ["target_id", "target_id"], ["basis", "basis"]] });
  }

  function workflow_short_text(value, limit = 32) {
    const characters = Array.from(value);
    return characters.length > limit ? characters.slice(0, limit - 1).join("") + "…" : value;
  }

  function append_workflow_pages(target, options) {
    const { id, label, page, total, on_change } = options;
    const page_count = Math.max(1, Math.ceil(total / WORKFLOW_PAGE_SIZE));
    const navigation = element("div", undefined, "graph-pagination workflow-pagination");
    navigation.id = id;
    navigation.setAttribute("role", "group");
    navigation.setAttribute("aria-label", label + " 페이지 이동");
    [["처음", 0], ["이전", page - 1], ["다음", page + 1], ["마지막", page_count - 1]].forEach(([label, next_page]) => {
      const button = graph_button(label, () => {
        on_change(next_page);
        const next_navigation = document.getElementById(id);
        const focused = Array.from(next_navigation.querySelectorAll("button")).find((item) => item.textContent === label);
        if (!focused.disabled) focused.focus({ preventScroll: true });
        else next_navigation.querySelector('[role="status"]').focus({ preventScroll: true });
      });
      button.disabled = next_page < 0 || next_page >= page_count || next_page === page;
      navigation.append(button);
    });
    const status = element("span", (page + 1) + " / " + page_count + " 페이지", "description");
    status.setAttribute("role", "status");
    status.tabIndex = -1;
    navigation.append(status);
    target.append(navigation);
  }

  function append_workflow_edge(svg, options) {
    const { dependency, index, positions, selected_step_id } = options;
    const before = positions.get(dependency.before_step_id);
    const after = positions.get(dependency.after_step_id);
    const bend = 410 + (index % 6) * 30;
    const path = before === after
      ? "M 324 " + (before - 12) + " C " + bend + " " + (before - 65) + ", " + bend + " " + (before + 65) + ", 324 " + (after + 12)
      : "M 324 " + before + " C " + bend + " " + before + ", " + bend + " " + after + ", 324 " + after;
    const is_selected = dependency.before_step_id === selected_step_id || dependency.after_step_id === selected_step_id;
    const edge = svgElement("path", { d: path, class: "workflow-dependency basis-" + dependency.basis + (is_selected ? " is-selected" : ""),
      "data-dependency-index": index, "data-before-step-id": dependency.before_step_id,
      "data-after-step-id": dependency.after_step_id, "marker-end": "url(#workflow-arrow-" + dependency.basis + ")" });
    edge.append(svgElement("title", {}, "관계 " + (index + 1) + " · " + dependency.before_step_id + " → " + dependency.after_step_id
      + " · " + dependency.condition + " · " + dependency.basis));
    svg.append(edge);
    const label = svgElement("text", { x: bend - 32, y: before === after ? before - 25 : (before + after) / 2 - 7,
      class: "workflow-edge-label", "data-dependency-index": index }, "#" + (index + 1) + " " + workflow_short_text(dependency.condition, 20));
    label.append(svgElement("title", {}, dependency.condition));
    svg.append(label);
  }

  function append_workflow_step(svg, options) {
    const { step, y, selected_step_id, on_select } = options;
    const group = svgElement("g", { class: "workflow-step" + (step.step_id === selected_step_id ? " is-selected" : ""),
      role: "button", tabindex: 0, "aria-label": "단계 순서 " + step.order + " · " + step.action,
      "aria-pressed": step.step_id === selected_step_id, "data-step-id": step.step_id });
    group.append(svgElement("title", {}, step.step_id + " · order " + step.order + " · " + step.action));
    group.append(svgElement("rect", { x: 24, y: y - 35, width: 300, height: 70, rx: 10 }));
    group.append(svgElement("text", { x: 40, y: y - 12, class: "workflow-order" }, "순서 " + step.order));
    group.append(svgElement("text", { x: 40, y: y + 15, class: "workflow-action" }, workflow_short_text(step.action)));
    group.addEventListener("click", () => on_select(step.step_id));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        on_select(step.step_id);
      }
    });
    svg.append(group);
  }

  function render_workflow_diagram(target, options) {
    const { workflow, steps, on_select } = options;
    const canvas = element("div", undefined, "workflow-canvas");
    canvas.tabIndex = 0;
    canvas.setAttribute("role", "region");
    canvas.setAttribute("aria-label", "Workflow 도식 · 가로·세로 스크롤 가능");
    const height = steps.length * WORKFLOW_ROW_HEIGHT + 30;
    const svg = svgElement("svg", { viewBox: "0 0 " + WORKFLOW_DIAGRAM_WIDTH + " " + height,
      width: WORKFLOW_DIAGRAM_WIDTH, height, class: "workflow-svg", role: "group", "aria-label": "Workflow 단계와 원본 의존관계" });
    const definitions = svgElement("defs", {});
    ["observed", "inferred", "verified"].forEach((basis) => {
      const marker = svgElement("marker", { id: "workflow-arrow-" + basis, markerWidth: 8, markerHeight: 8, refX: 7, refY: 4,
        orient: "auto", markerUnits: "strokeWidth", viewBox: "0 0 8 8" });
      marker.append(svgElement("path", { d: "M 0 0 L 8 4 L 0 8 z", class: "workflow-arrow basis-" + basis }));
      definitions.append(marker);
    });
    svg.append(definitions);
    const positions = new Map(steps.map((step, index) => [step.step_id, 50 + index * WORKFLOW_ROW_HEIGHT]));
    let visible_dependencies = 0;
    workflow.dependencies.forEach((dependency, index) => {
      if (!positions.has(dependency.before_step_id) || !positions.has(dependency.after_step_id)) return;
      append_workflow_edge(svg, { dependency, index, positions, selected_step_id: graph_state.workflow.selected_step_id });
      visible_dependencies += 1;
    });
    steps.forEach((step) => append_workflow_step(svg, { step, y: positions.get(step.step_id),
      selected_step_id: graph_state.workflow.selected_step_id, on_select }));
    canvas.append(svg);
    target.append(element("p", "전체 의존관계 " + workflow.dependencies.length + "개 중 현재 페이지 양 끝 단계가 보이는 " + visible_dependencies
      + "개를 도식화합니다. 나머지는 아래 원본 목록에서 탐색하세요. 페이지 사이 지름길을 만들지 않습니다.", "field-help"), canvas);
  }

  function render_workflow_step_detail(target, workflow, on_select) {
    const detail = element("aside", undefined, "workflow-step-detail");
    detail.id = "kg-workflow-step-detail";
    detail.tabIndex = -1;
    detail.setAttribute("aria-label", "Workflow 선택 단계 상세");
    detail.append(element("h5", "선택 단계 상세"));
    const step = workflow.steps.find((item) => item.step_id === graph_state.workflow.selected_step_id);
    if (!step) empty_state(detail, "단계를 클릭하거나 Tab·Enter로 선택하면 원본 정보를 확인할 수 있습니다.");
    else {
      const fields = element("dl");
      for (const [label, value] of [["action", step.action], ["step_id", step.step_id], ["원본 order", step.order],
        ["들어오는 관계", workflow.dependencies.filter((row) => row.after_step_id === step.step_id).length],
        ["나가는 관계", workflow.dependencies.filter((row) => row.before_step_id === step.step_id).length]]) {
        fields.append(element("dt", label), element("dd", value));
      }
      detail.append(fields);
      const ordered_steps = [...workflow.steps].sort((left, right) => left.order - right.order);
      if (Math.floor(ordered_steps.findIndex((item) => item.step_id === step.step_id) / WORKFLOW_PAGE_SIZE) !== graph_state.workflow.step_page) {
        detail.append(element("p", "선택 단계는 현재 도식 페이지 밖에 있습니다.", "field-help"),
          graph_button("선택 단계로 이동", () => on_select(step.step_id)));
      }
    }
    detail.append(element("p", "WorkflowStep 상세입니다. action 이름으로 KG Action 노드를 추측해 연결하지 않습니다.", "field-help"));
    target.append(detail);
  }

  function render_workflow_dependencies(target, options) {
    const { workflow, on_select, on_page } = options;
    const state = graph_state.workflow;
    state.dependency_page = Math.min(state.dependency_page, Math.max(0, Math.ceil(workflow.dependencies.length / WORKFLOW_PAGE_SIZE) - 1));
    const start = state.dependency_page * WORKFLOW_PAGE_SIZE;
    target.append(element("h4", "원본 의존관계"), element("p", "전체 " + workflow.dependencies.length + "개 · "
      + (workflow.dependencies.length ? start + 1 : 0) + "–" + Math.min(start + WORKFLOW_PAGE_SIZE, workflow.dependencies.length)
      + "개 표시. 다른 페이지의 단계도 출발·도착 버튼으로 이동할 수 있습니다. 조건은 실행 코드가 아닌 원문 설명입니다.", "description"));
    if (!workflow.dependencies.length) empty_state(target, "의존관계 0개 · 단계가 나란히 있어도 새 관계를 만들지 않습니다.");
    const list = element("div", undefined, "workflow-dependency-list");
    workflow.dependencies.slice(start, start + WORKFLOW_PAGE_SIZE).forEach((dependency, offset) => {
      const row = element("article", undefined, "workflow-dependency-card");
      row.dataset.dependencyIndex = start + offset;
      row.append(element("span", "관계 #" + (start + offset + 1), "workflow-relation-index"), status_badge(dependency.basis));
      const ends = element("div", undefined, "workflow-dependency-ends");
      for (const [label, step_id] of [["출발", dependency.before_step_id], ["도착", dependency.after_step_id]]) {
        const step = workflow.steps.find((item) => item.step_id === step_id);
        const button = graph_button(label + " · 순서 " + step.order + " · " + workflow_short_text(step.action), () => on_select(step_id));
        button.title = step_id + " · " + step.action;
        button.dataset.stepId = step_id;
        button.setAttribute("aria-label", label + " 단계 " + step_id + " 보기");
        ends.append(button);
        if (label === "출발") ends.append(element("span", "→", "workflow-direction"));
      }
      row.append(ends, element("p", dependency.condition || "조건 설명 없음", "workflow-condition"));
      list.append(row);
    });
    target.append(list);
    append_workflow_pages(target, { id: "kg-workflow-dependency-pages", label: "Workflow 의존관계",
      page: state.dependency_page, total: workflow.dependencies.length, on_change: on_page });
  }

  function render_selected_workflow(target, workflow, options) {
    const { on_select, on_step_page, on_dependency_page } = options;
    const state = graph_state.workflow;
    const steps = [...workflow.steps].sort((left, right) => left.order - right.order);
    state.step_page = Math.min(state.step_page, Math.max(0, Math.ceil(steps.length / WORKFLOW_PAGE_SIZE) - 1));
    const start = state.step_page * WORKFLOW_PAGE_SIZE;
    const summary = element("div", undefined, "workflow-summary");
    summary.append(element("h4", workflow.name), element("code", workflow.workflow_id),
      element("span", "흐름 전체의 근거", "field-help"), status_badge(workflow.basis));
    target.append(summary);
    const status = element("p", "전체 단계 " + steps.length + "개 · " + (steps.length ? start + 1 : 0) + "–"
      + Math.min(start + WORKFLOW_PAGE_SIZE, steps.length) + "개 표시 · 전체 의존관계 " + workflow.dependencies.length + "개", "description");
    status.id = "kg-workflow-counts";
    status.setAttribute("role", "status");
    target.append(status, element("p", "세로 배치는 원본 order 순서입니다. 화살표는 원본 dependencies만 표시합니다. observed=관찰(파랑), inferred=추론(주황 점선), verified=검증(초록 굵은 선). 업무 정책의 필수 선행조건이나 취약점 확정을 의미하지 않습니다.", "field-help"));
    if (steps.length) {
      append_workflow_pages(target, { id: "kg-workflow-step-pages", label: "Workflow 단계", page: state.step_page,
        total: steps.length, on_change: on_step_page });
      const layout = element("div", undefined, "workflow-layout");
      const drawing = element("div", undefined, "workflow-drawing");
      render_workflow_diagram(drawing, { workflow, steps: steps.slice(start, start + WORKFLOW_PAGE_SIZE), on_select });
      layout.append(drawing);
      render_workflow_step_detail(layout, workflow, on_select);
      target.append(layout);
    } else empty_state(target, "이 Workflow의 단계 0개 · 누락 단계를 추정해서 채우지 않습니다.");
    render_workflow_dependencies(target, { workflow, on_select, on_page: on_dependency_page });
  }

  function render_workflow_explorer(target, data) {
    const state = graph_state.workflow;
    const field = element("div", undefined, "workflow-choice");
    const label = element("label", "Workflow 선택");
    label.htmlFor = "kg-workflow";
    const select = element("select");
    select.id = label.htmlFor;
    const placeholder = element("option", "흐름을 선택하세요 · 다른 Workflow를 합치지 않습니다");
    placeholder.value = "";
    select.append(placeholder);
    data.workflows.forEach((workflow) => {
      const option = element("option", workflow.name + " · " + workflow.workflow_id + " · " + workflow.steps.length + "단계");
      option.value = workflow.workflow_id;
      select.append(option);
    });
    select.value = state.workflow_id;
    select.disabled = data.workflows.length === 0;
    field.append(label, select, element("p", "snapshot의 Workflow 전체 " + data.workflows.length + "개 · 검증 전 query 시점의 흐름입니다.", "description"));
    const body = element("div");
    body.id = "kg-workflow-body";
    function draw() {
      body.replaceChildren();
      const workflow = data.workflows.find((item) => item.workflow_id === state.workflow_id);
      if (!workflow) {
        empty_state(body, !data.workflows.length ? "공개 Workflow 0개 · 미발행/실패와 구분하며 업무 흐름이 없다는 판정은 아닙니다."
          : state.workflow_id ? "선택한 Workflow가 현재 snapshot에 없습니다. 다시 선택하세요." : "Workflow를 선택하면 단계·순서·원본 의존관계를 표시합니다.");
        return;
      }
      render_selected_workflow(body, workflow, { on_select: (step_id) => {
        state.selected_step_id = step_id;
        const steps = [...workflow.steps].sort((left, right) => left.order - right.order);
        state.step_page = Math.floor(steps.findIndex((step) => step.step_id === step_id) / WORKFLOW_PAGE_SIZE);
        draw();
        const selected = Array.from(body.querySelectorAll(".workflow-step")).find((step) => step.dataset.stepId === step_id);
        selected?.focus({ preventScroll: true });
        selected?.scrollIntoView({ block: "nearest", inline: "nearest" });
      }, on_step_page: (page) => { state.step_page = page; draw(); },
      on_dependency_page: (page) => { state.dependency_page = page; draw(); } });
    }
    select.addEventListener("change", () => {
      state.workflow_id = select.value;
      state.selected_step_id = null;
      state.step_page = state.dependency_page = 0;
      draw();
    });
    target.append(field, body);
    draw();
  }

  function append_graph_views(target, options) {
    const { candidates, on_mode_change, on_candidate_change } = options;
    const controls = element("div", undefined, "graph-view-controls");
    const buttons = element("div", undefined, "graph-view-buttons");
    buttons.setAttribute("role", "group");
    buttons.setAttribute("aria-label", "KG 보기");
    Object.entries(graph_view_labels).forEach(([mode, label]) => {
      const button = graph_button(label, () => on_mode_change(mode));
      button.dataset.view = mode;
      buttons.append(button);
    });
    const field = element("div", undefined, "graph-candidate-field");
    const label = element("label", "검증 후보 선택");
    label.htmlFor = "kg-candidate";
    const select = element("select");
    select.id = label.htmlFor;
    const placeholder = element("option", "후보를 선택하세요 · 확정 취약점 아님");
    placeholder.value = "";
    select.append(placeholder);
    candidates.forEach((row) => {
      const option = element("option", row.candidate_id + " · " + row.vulnerability_type);
      option.value = row.candidate_id;
      select.append(option);
    });
    select.addEventListener("change", () => on_candidate_change(select.value));
    field.append(label, select);
    controls.append(buttons, field);
    target.append(controls);
    return () => {
      buttons.querySelectorAll("button").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.view === graph_state.view_mode)));
      field.hidden = graph_state.view_mode !== "candidate";
      select.disabled = candidates.length === 0;
      select.value = graph_state.candidate_id;
    };
  }

  function graph_view_description(data, candidates, artifact) {
    if (graph_state.view_mode === "workflow") return "Workflow별 원본 단계와 의존관계입니다. 관찰 순서를 필수 업무 규칙으로 승격하지 않으며, 서로 다른 흐름이나 KG Action 노드를 임의로 연결하지 않습니다.";
    if (graph_state.is_neighborhood) return "선택 노드의 실제 1-hop 관계입니다. 연결 펼치기로 다음 노드를 확인할 수 있습니다.";
    if (graph_state.view_mode === "all") return "전체 원본입니다. 연결 없는 Action도 포함하며 선택 노드의 관계명을 표시합니다.";
    if (graph_state.view_mode === "access") return "User·Role·Resource와 그 사이의 원본 관계입니다. Endpoint·Action 등은 노드 선택 후 연결 펼치기로 확인하세요. 숨김은 분석 누락이나 취약점 없음 판정이 아닙니다.";
    if (!artifact?.view) return "후보 공개 결과 확인 불가 · " + stateText(artifact) + ". 원본 그래프와 구분하며 후보가 0개라고 판단하지 않습니다.";
    if (!candidates.length) return "후보 공개 결과 0건 · 원본 그래프에 취약점이 없다는 뜻은 아닙니다.";
    const candidate = candidates.find((row) => row.candidate_id === graph_state.candidate_id);
    if (!candidate) return "검증 후보를 선택하면 Resource와 실제 1-hop 관계를 표시합니다. 후보는 확정 취약점이 아닙니다.";
    const resources = new Set(data.nodes.filter((node) => node.node_type === "Resource").map((node) => node.node_id));
    const matched = candidate.resource_ids.filter((id) => resources.has(id)).length;
    return (artifact.state === "partial" ? "partial · 부분 후보 결과입니다. " : "")
      + "선택 후보 " + candidate.candidate_id + " · Resource ID 일치 " + matched + "/" + candidate.resource_ids.length
      + "개 · 불일치 " + (candidate.resource_ids.length - matched) + "개. 실제 node_id만 연결하며 계정 ID나 자원 이름으로 추정하지 않습니다. 후보는 확정 취약점이 아닙니다.";
  }

  function render_graph_explorer(target, options) {
    const { data, candidates, candidate_artifact } = options;
    const toolbar = element("div", undefined, "graph-toolbar");
    const summary = element("p", undefined, "description");
    summary.id = "kg-display-counts";
    summary.setAttribute("role", "status");
    const help = element("p", "빈 영역 드래그·방향키로 이동, 휠·확대/축소 버튼으로 배율 변경. 노드 클릭 또는 Enter로 선택한다. 모든 배치는 화면 표현이며 새 관계를 추론하지 않는다.", "description");
    help.id = "kg-navigation-help";
    const basis_help = element("p", "basis: observed=관찰 · inferred=추론(점선) · verified=검증(굵은 선). 노드 basis 필터는 노드에 적용하며, 관계는 표시 노드 사이의 원본 관계를 유지한다. 주변 보기는 필터를 초기화하고 선택 노드와 직접 연결 노드를 표시한다.", "description");
    const scene = element("div");
    const view_description = element("p", undefined, "graph-view-description");
    view_description.id = "kg-view-description";
    view_description.setAttribute("role", "status");
    let camera = null;
    let synchronize_filters;
    const synchronize_views = append_graph_views(target, {
      candidates,
      on_mode_change: (mode) => {
        const candidate_id = graph_state.candidate_id;
        const workflow = graph_state.workflow;
        graph_state = create_graph_state();
        graph_state.view_mode = mode;
        graph_state.candidate_id = candidate_id;
        graph_state.workflow = workflow;
        synchronize_filters();
        on_filter_change();
      },
      on_candidate_change: (candidate_id) => {
        graph_state.candidate_id = candidate_id;
        graph_state.expanded_ids = [];
        graph_state.selected_id = null;
        graph_state.is_neighborhood = false;
        on_filter_change();
      },
    });
    function reset_pages() {
      graph_state.node_page = graph_state.edge_page = graph_state.related_page = graph_state.workflow_page = 0;
    }
    function on_filter_change() {
      reset_pages();
      graph_state.camera = null;
      synchronize_views();
      render_scene();
    }
    function select_node(node_id, can_center) {
      graph_state.selected_id = node_id;
      graph_state.related_page = 0;
      if (graph_state.is_neighborhood) graph_state.camera = null;
      render_scene();
      if (can_center) {
        camera?.center(node_id);
        scene.querySelector("#kg-detail").focus({ preventScroll: true });
      }
      else scene.querySelectorAll(".graph-node").forEach((node) => {
        if (node.dataset.nodeId === node_id) node.focus({ preventScroll: true });
      });
    }
    function show_all() {
      graph_state = create_graph_state();
      graph_state.view_mode = "all";
      synchronize_filters();
      synchronize_views();
      render_scene();
    }
    const zoom_in = graph_button("확대", () => camera?.zoom(0.8));
    const zoom_out = graph_button("축소", () => camera?.zoom(1.25));
    const fit = graph_button("화면 맞춤", () => camera?.fit());
    const center = graph_button("선택 노드로 이동", () => camera?.center(graph_state.selected_id));
    const nearby = graph_button("주변 보기", () => {
      const selected_id = graph_state.selected_id;
      graph_state = create_graph_state();
      graph_state.view_mode = "all";
      graph_state.selected_id = selected_id;
      graph_state.is_neighborhood = true;
      synchronize_filters();
      synchronize_views();
      render_scene();
    });
    const expand = graph_button("연결 펼치기", () => {
      if (!graph_state.expanded_ids.includes(graph_state.selected_id)) graph_state.expanded_ids.push(graph_state.selected_id);
      graph_state.node_type = graph_state.basis = graph_state.resource_scope = "all";
      graph_state.search = "";
      synchronize_filters();
      on_filter_change();
    });
    toolbar.append(zoom_in, zoom_out, fit, center, expand, nearby, graph_button("전체 보기", show_all));
    function render_scene() {
      scene.replaceChildren();
      const is_workflow = graph_state.view_mode === "workflow";
      [toolbar, summary, help, basis_help, target.querySelector(".graph-filters")].forEach((item) => { item.hidden = is_workflow; });
      view_description.textContent = graph_view_description(data, candidates, candidate_artifact);
      if (is_workflow) {
        camera = null;
        render_workflow_explorer(scene, data);
        return;
      }
      const nodes = filter_graph_nodes(data, graph_state, candidates);
      const visible_ids = new Set(nodes.map((node) => node.node_id));
      const edges = data.relationships.filter((edge) => visible_ids.has(edge.source_id) && visible_ids.has(edge.target_id));
      const connected_ids = new Set(data.relationships.flatMap((edge) => [edge.source_id, edge.target_id]));
      const isolated_count = data.nodes.filter((node) => !connected_ids.has(node.node_id)).length;
      summary.textContent = "전체 snapshot: 노드 " + data.nodes.length + "개 · 관계 " + data.relationships.length
        + "개 · 연결 없는 노드 " + isolated_count + "개. 현재 표시: 노드 " + nodes.length + "/" + data.nodes.length
        + "개 · 관계 " + edges.length + "/" + data.relationships.length + "개 · "
        + (graph_state.is_neighborhood ? "선택 노드의 1-hop 주변 보기" : graph_view_labels[graph_state.view_mode])
        + " · 숨김: 노드 " + (data.nodes.length - nodes.length) + "개 · 관계 " + (data.relationships.length - edges.length) + "개. 앞부분 자르기 없음.";
      view_description.textContent = graph_view_description(data, candidates, candidate_artifact);
      const legend = element("div", undefined, "graph-legend");
      legend.setAttribute("aria-label", "현재 표시 노드 종류");
      nodeTypes.filter((type) => nodes.some((node) => node.node_type === type)).forEach((type) => {
        legend.append(element("span", type, "graph-legend-item legend-" + type));
      });
      scene.append(legend);
      const layout = element("div", undefined, "graph-explorer-layout");
      const drawing = element("div", undefined, "graph-drawing");
      const detail = element("aside", undefined, "graph-detail");
      detail.id = "kg-detail";
      detail.tabIndex = -1;
      detail.setAttribute("aria-label", "선택 노드 상세");
      layout.append(drawing, detail);
      scene.append(layout);
      const selected_candidate = candidates.find((row) => row.candidate_id === graph_state.candidate_id);
      const candidate_ids = new Set(graph_state.view_mode === "candidate" ? selected_candidate?.resource_ids || [] : []);
      camera = diagram(drawing, { nodes, edges, on_select: select_node, candidate_ids });
      render_graph_detail(detail, { data, visible_ids, on_select: select_node });
      [zoom_in, zoom_out, fit].forEach((button) => { button.disabled = nodes.length === 0; });
      center.disabled = !visible_ids.has(graph_state.selected_id);
      expand.disabled = !visible_ids.has(graph_state.selected_id)
        || !data.relationships.some((edge) => (edge.source_id === graph_state.selected_id && !visible_ids.has(edge.target_id))
          || (edge.target_id === graph_state.selected_id && !visible_ids.has(edge.source_id)));
      nearby.disabled = graph_state.selected_id === null;
      nearby.setAttribute("aria-pressed", String(graph_state.is_neighborhood));
      const tables = element("div");
      tables.id = "kg-tables";
      const raw_data = disclosure("kg-data", "노드·관계·업무 흐름 데이터");
      raw_data.body.append(tables);
      scene.append(raw_data.wrapper);
      append_graph_table(tables, { caption: "KG 노드", rows: nodes, page_key: "node_page", on_select: select_node,
        columns: [["node_id", "node_id"], ["종류", "node_type"], ["basis", "basis"], ["Resource 범위", "resource_scope"]] });
      append_graph_table(tables, { caption: "KG 관계", rows: edges, page_key: "edge_page",
        columns: [["관계 ID", "relationship_id"], ["source_id", "source_id"], ["target_id", "target_id"], ["관계", "relation_type"], ["basis", "basis"]] });
      append_graph_table(tables, { caption: "업무 흐름", rows: data.workflows, page_key: "workflow_page",
        columns: [["ID", "workflow_id"], ["이름", "name"], ["basis", "basis"], ["단계(명시된 order 순)",
          (row) => row.steps.map((step) => step.order + ": " + step.step_id + " · " + step.action).join(" → ")], ["의존 관계 수", "dependency_count"]] });
    }
    synchronize_filters = append_graph_filters(target, data, on_filter_change);
    synchronize_views();
    target.append(view_description, toolbar, summary, help, scene, basis_help);
    render_scene();
  }

  function renderGraph(artifact, candidate_artifact) {
    const candidates = candidate_artifact?.view?.candidates || [];
    const signature = JSON.stringify([currentRunId, artifact || null, candidate_artifact?.state || null,
      candidate_artifact?.error_count ?? 0, candidate_artifact?.issue || null,
      candidates.map((row) => [row.candidate_id, row.resource_ids, row.vulnerability_type])]);
    // 같은 불변 snapshot의 polling은 검색 입력·선택·페이지·카메라를 지우지 않는다.
    if (signature === graph_signature) return;
    graph_signature = signature;
    document.getElementById("graph-state").textContent = stateText(artifact);
    const target = document.getElementById("graph-view");
    target.replaceChildren();
    const data = artifact?.view;
    const next_key = data ? JSON.stringify([currentRunId, data.graph_id, data.graph_revision]) : null;
    if (next_key !== graph_key) graph_state = create_graph_state();
    graph_key = next_key;
    if (!data) {
      empty_state(target, "표시할 KG snapshot이 없습니다. 미발행·실패·조회 상태는 위 안내에서 확인하세요.");
      return;
    }
    target.append(element("p", data.graph_id + " · query revision " + data.graph_revision, "description"));
    if (data.snapshot_state !== "completed") {
      target.append(element("p", data.snapshot_state === "failed" ? "structure_snapshot 조회 실패 · 빈 그래프로 처리하지 않는다." : "structure_snapshot 결과 없음 · 빈 그래프로 처리하지 않는다.", "description"));
      return;
    }
    counts(target, { nodes: data.nodes.length, relationships: data.relationships.length, workflows: data.workflows.length });
    render_graph_explorer(target, { data, candidates, candidate_artifact });
  }

  function processPanel(target, title, artifact, columns, rows) {
    const panel = disclosure(artifact?.artifact_type || title, title);
    panel.summary.append(status_badge(artifact?.state || "missing"));
    panel.body.append(element("p", stateText(artifact), "description"));
    if (artifact?.view) table(panel.body, title, columns, rows);
    target.append(panel.wrapper);
  }

  function renderDiagnosisOverview(target, byType) {
    target.replaceChildren();
    const section = element("section");
    section.id = "diagnosis-overview";
    section.append(element("h4", "탐지·검증·확정 현황"));
    const candidates = byType.get("vulnerability_candidates");
    const verification = byType.get("verification_results");
    const report = byType.get("diagnosis_report");
    const resultCount = (result) => verification?.view
      ? verification.view.results.filter((row) => row.result === result).length : "미확인";
    counts(section, {
      candidate_count: candidates?.view ? candidates.view.candidates.length : "미확인",
      verification_success_count: resultCount("success"),
      verification_failure_count: resultCount("failure"),
      verification_indeterminate_count: resultCount("indeterminate"),
      verification_blocked_count: resultCount("blocked"),
      reporter_confirmed_count: report?.view ? report.view.summary.confirmed_count : "미확인",
    });
    const sources = element("div", undefined, "overview-sources");
    for (const [label, artifact] of [["후보", candidates], ["검증", verification], ["Reporter", report]]) {
      sources.append(element("p", label + " 산출물: " + stateText(artifact), "description"));
    }
    section.append(sources);
    section.append(element("p", "후보는 candidate 수, 검증은 시나리오별 결과 수, 확정은 Reporter finding 수다. 서로 합산하거나 고유 취약점 수로 해석하지 않는다.", "description"),
      element("p", "검증 조건 통과(success)는 시나리오 조건을 통과했다는 뜻이며 확정 취약점이 아니다. 확정 여부와 사유는 Reporter 결과를 그대로 표시한다. 미확인은 0건이 아니며 부분 결과는 전체 진단을 대표하지 않는다.", "description"));
    target.append(section);
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
      table(target, "확정 외 리포트 상태(확인된 취약점 목록에서 제외)", [["리포트 ID", "finding_id"], ["후보 ID", "candidate_id"], ["시나리오 ID", "scenario_id"], ["상태", "status"],
        ["Verifier 결과", (row) => verifierArtifact?.view ? results.find((result) => result.verification_id === row.verification_id
          && result.candidate_id === row.candidate_id && result.scenario_id === row.scenario_id)?.result ?? "연결된 검증 결과 없음" : "공개 결과 확인 불가"],
        ["Reporter 설명", "description"]], report.view.other_findings);
    }
  }

  function renderApproval(byType) {
    const artifact = byType.get("safety_decisions");
    document.getElementById("approval-state").textContent = stateText(artifact);
    const target = document.getElementById("approval-view");
    target.replaceChildren();
    if (!artifact?.view) return;
    const scenarios = new Map((byType.get("test_scenarios")?.view?.scenarios || []).map((row) => [row.scenario_id, row]));
    const pending = artifact.view.decisions.filter((row) => row.decision === "require_approval");
    const cards = element("div", undefined, "approval-grid");
    pending.slice(0, TABLE_ROW_LIMIT).forEach((row) => {
      const card = element("article", undefined, "approval-card");
      card.append(status_badge(row.decision), element("h4", row.scenario_id), element("p", row.reason),
        element("div", "후보: " + (scenarios.get(row.scenario_id)?.candidate_id || "공개 시나리오 확인 불가"), "finding-meta"),
        element("div", "판정: " + row.decision_id + " · " + row.reason_codes.join(" · "), "finding-meta"));
      cards.append(card);
    });
    target.append(cards);
    table(target, "현재 Policy 산출물의 승인 대기", [["판정 ID", "decision_id"], ["시나리오 ID", "scenario_id"], ["후보 ID", (row) => scenarios.get(row.scenario_id)?.candidate_id || "공개 시나리오 확인 불가"], ["사유 코드", "reason_codes"], ["사유", "reason"]], artifact.view.decisions.filter((row) => row.decision === "require_approval"));
  }

  function renderReport(artifact) {
    document.getElementById("report-state").textContent = stateText(artifact);
    const target = document.getElementById("report-view");
    target.replaceChildren();
    if (!artifact?.view) return;
    const view = artifact.view;
    target.append(element("p", "Reporter confirmed_count: " + view.summary.confirmed_count + "건. 분류별 집계·확정 외 상태는 진단 과정에서 확인한다.", "description"));
    const cards = element("div", undefined, "finding-grid");
    view.confirmed_findings.slice(0, TABLE_ROW_LIMIT).forEach((row) => {
      const card = element("article", undefined, "finding-card");
      card.append(status_badge(row.status), element("h4", row.title), element("p", row.description),
        element("div", row.category + " · " + row.vulnerability_type, "finding-meta"),
        element("div", row.finding_id + " · " + row.candidate_id, "finding-meta"));
      cards.append(card);
    });
    target.append(cards);
    if (!view.confirmed_findings.length) empty_state(target, "확인된 취약점 · 공개 결과 0건. 미검증·의심·판단불가 항목은 진단 과정에서 확인하세요.");
    else target.append(element("p", "확정 항목 카드 최대 " + TABLE_ROW_LIMIT + "건 표시 · 전체 " + view.confirmed_findings.length + "건", "field-help"));
    const raw_data = disclosure("report-data", "확정 항목의 원본 ID·근거 참조");
    table(raw_data.body, "확인된 취약점", [["리포트 ID", "finding_id"], ["제목", "title"], ["분류·유형", (row) => row.category + " · " + row.vulnerability_type], ["후보/시나리오/검증 ID", (row) => row.candidate_id + " / " + display(row.scenario_id) + " / " + display(row.verification_id)], ["설명", "description"], ["마스킹된 근거 참조(본문·파일 검증 미제공)", (row) => row.evidence_refs.map((ref) => ref.evidence_id + " · " + ref.kind + " · SHA-256 " + ref.sha256).join("\n") || "근거 참조 없음"]], view.confirmed_findings);
    target.append(raw_data.wrapper);
    target.append(element("h4", "Reporter가 기록한 진단 한계"));
    const limitations = element("ul", undefined, "status-list");
    view.limitations.forEach((value) => limitations.append(element("li", value)));
    if (!view.limitations.length) limitations.append(element("li", "이 산출물에 기록된 추가 한계 없음 · 전체 검증 완료를 의미하지 않는다."));
    target.append(limitations);
  }

  function render_evaluation_metric(metric_id, metric, definition, class_name) {
    const item = element("article", undefined, class_name);
    const has_value = metric && metric.value !== null;
    item.dataset.metric = metric_id;
    item.dataset.state = has_value ? "measured" : metric ? "unmeasured" : "missing";
    const copy = element("div", undefined, "evaluation-metric-copy");
    copy.append(element("h5", definition.label));
    if (definition.label !== metric_id) copy.append(element("code", metric_id, "evaluation-metric-id"));
    if (definition.description) copy.append(element("p", definition.description, "evaluation-metric-description"));
    const reading = element("div", undefined, "evaluation-reading");
    reading.append(element("p", has_value ? (metric.value * 100).toFixed(2) + "%" : metric ? "미측정" : "미제공", "evaluation-value"));
    if (metric) {
      const numerator = definition.numerator || "일치";
      reading.append(element("p", numerator + " " + metric.numerator + " / " + (definition.denominator || "정답") + " " + metric.denominator, "evaluation-metric-counts"));
    }
    if (has_value) {
      const meter = element("meter");
      meter.min = 0;
      meter.max = 1;
      meter.value = metric.value;
      meter.setAttribute("aria-label", definition.label + " · Reporter 원본 비율");
      reading.append(meter);
    } else {
      reading.append(element("p", metric ? "계산 불가 또는 미측정 · 0점이 아님" : "산출물에 해당 지표 없음", "evaluation-status-note"));
    }
    item.append(copy, reading);
    return item;
  }

  function describe_workflow_metric(matching_profile) {
    if (matching_profile === "default-v1") return "이름과 전체 액션열이 정확히 일치한 정답 흐름 비율";
    if (matching_profile === "default-v2") return "정답 액션열 전체가 한 관찰 흐름에 순서대로 포함된 비율 · 중간의 다른 단계 허용";
    return "Reporter의 매칭 프로필에 따라 전체 흐름이 일치한 비율";
  }

  function render_evaluation_group(target, view, group) {
    const section = element("section", undefined, "evaluation-metric-group");
    section.dataset.group = group.id;
    const heading = element("h4", group.label);
    heading.id = "evaluation-group-" + group.id;
    section.setAttribute("aria-labelledby", heading.id);
    section.append(heading, element("p", group.description, "description"));
    const items = element("div", undefined, group.id === "detection" ? "evaluation-cards" : "evaluation-metric-list");
    for (const [metric_id, definition] of Object.entries(evaluation_metric_definitions)) {
      if (definition.group !== group.id) continue;
      const metric = view.metrics.find((row) => row.metric_id === metric_id);
      if (!metric && group.id === "structure") continue;
      const description = metric_id === "workflow_recall" ? describe_workflow_metric(view.matching_profile) : definition.description;
      items.append(render_evaluation_metric(metric_id, metric, { ...definition, description },
        group.id === "detection" ? "evaluation-card" : "evaluation-metric-row"));
    }
    if (!items.children.length) empty_state(items, "산출물에 웹 구조 지표가 제공되지 않았습니다. 0점을 의미하지 않습니다.");
    section.append(items);
    target.append(section);
  }

  function render_evaluation_metrics(target, view) {
    for (const group of [
      { id: "detection", label: "취약점 탐지", description: "정밀도: 나온 후보 중 정답 비율 · 재현율: 정답 취약점 중 찾아낸 비율. 검증 조건 통과와 Reporter 확정은 다릅니다." },
      { id: "structure", label: "웹 구조", description: "정답에 기록된 웹 요소·관계를 얼마나 발견했는지 나타냅니다. 취약점 탐지 정확도와는 다른 지표입니다." },
      { id: "workflow", label: "Workflow", description: "전체 흐름과 단계 구성·순서를 구분합니다. 관찰 단계 커버리지이며 업무 완주·정책 준수·LLM 생성 품질 점수가 아닙니다. 부분 지표는 default-v2에서 제공합니다." },
    ]) render_evaluation_group(target, view, group);
    const other_metrics = view.metrics.filter((metric) => !Object.hasOwn(evaluation_metric_definitions, metric.metric_id));
    if (!other_metrics.length) return;
    const other = element("section", undefined, "evaluation-metric-group");
    other.dataset.group = "other";
    other.setAttribute("aria-labelledby", "evaluation-group-other");
    const heading = element("h4", "기타 지표");
    heading.id = "evaluation-group-other";
    other.append(heading, element("p", "표시 설명이 등록되지 않은 지표입니다. ID와 원본 분자·분모를 그대로 제공합니다.", "description"));
    const items = element("div", undefined, "evaluation-metric-list");
    other_metrics.slice(0, TABLE_ROW_LIMIT).forEach((metric) => items.append(render_evaluation_metric(metric.metric_id, metric,
      { label: metric.metric_id, numerator: "분자", denominator: "분모" }, "evaluation-metric-row")));
    other.append(items, element("p", "전체 " + other_metrics.length + "건 · 최대 " + TABLE_ROW_LIMIT + "건 표시", "field-help"));
    target.append(other);
  }

  function render_evaluation_context(target, view) {
    const context = element("dl", undefined, "evaluation-context");
    for (const [label, value] of [["데이터셋", view.dataset.dataset_id], ["정답 버전", view.dataset.dataset_version],
      ["매칭 프로필", view.matching_profile], ["KG revision", view.source_graph_revision]]) {
      const item = element("div");
      item.append(element("dt", label), element("dd", display(value)));
      context.append(item);
    }
    target.append(context);
  }

  function renderEvaluation(artifact) {
    document.getElementById("evaluation-state").textContent = stateText(artifact);
    const target = document.getElementById("evaluation-view");
    target.replaceChildren();
    if (!artifact?.view) return;
    const view = artifact.view;
    render_evaluation_context(target, view);
    target.append(element("p", "백분율은 Reporter value를 소수 둘째 자리로 반올림한 표시값입니다. 분자/분모에서 다시 계산하지 않으며, 미측정(null)·미제공·조회 실패를 0점으로 바꾸지 않습니다.", "description"));
    render_evaluation_metrics(target, view);
    const raw_data = disclosure("evaluation-data", "원본 지표·평가 기준 펼치기");
    table(raw_data.body, "평가 기준", [["데이터셋", "dataset_id"], ["버전", "dataset_version"],
      ["정답 SHA-256(원문 비열람)", "sha256"], ["KG revision", () => view.source_graph_revision],
      ["매칭 프로필", () => view.matching_profile]], [view.dataset]);
    const ratioText = (row) => row.value === null ? "미측정 / 계산 불가" : String(row.value) + " (" + (row.value * 100).toFixed(2) + "%)";
    table(raw_data.body, "Reporter 성능 지표", [["지표 ID", "metric_id"], ["비율(Reporter 원본 값)", ratioText],
      ["분자", "numerator"], ["분모", "denominator"]], view.metrics);
    target.append(raw_data.wrapper);
    target.append(element("h4", "정답과의 일치·오탐·누락"));
    target.append(element("p", "TP: 정답 양성과 일치 · FP: 정답 양성으로 매칭되지 않은 예측 · FN: 찾아내지 못한 정답 양성. 후보와 확정 단계는 따로 집계합니다.", "description"));
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
    if (disclosure_run_id !== currentRunId) {
      disclosure_states.clear();
      disclosure_run_id = currentRunId;
    }
    const byType = new Map(payload.artifacts.map((artifact) => [artifact.artifact_type, artifact]));
    renderDiagnosisOverview(document.getElementById("overview-view"), byType);
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
    renderGraph(byType.get("graph_query_result"), byType.get("vulnerability_candidates"));
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
