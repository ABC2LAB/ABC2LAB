"use strict";

(() => {
  const TABLE_ROW_LIMIT = 100;
  const GRAPH_PAGE_SIZE = 50;
  const GRAPH_CELL_SIZE = 110;
  const GRAPH_MAX_COLUMNS = 8;
  const GRAPH_PADDING = 55;
  const GRAPH_MIN_VIEW_WIDTH = 100;
  const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
  const nodeTypes = ["User", "Role", "Page", "Action", "Endpoint", "Parameter", "Resource"];
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
  let currentRunId = null;
  let requestSequence = 0;
  let graph_signature;
  let graph_key = null;
  let graph_state = create_graph_state();

  function create_graph_state() {
    return { node_type: "all", basis: "all", resource_scope: "all", search: "",
      is_connected_only: false, is_neighborhood: false, selected_id: null, camera: null,
      node_page: 0, edge_page: 0, related_page: 0, workflow_page: 0 };
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

  function filter_graph_nodes(data, state) {
    const connected_ids = new Set(data.relationships.flatMap((edge) => [edge.source_id, edge.target_id]));
    const nearby_ids = state.is_neighborhood ? graph_neighbors(data, state.selected_id) : null;
    const search = state.search.trim().toLowerCase();
    return data.nodes.filter((node) => (state.node_type === "all" || node.node_type === state.node_type)
      && (state.basis === "all" || node.basis === state.basis)
      && (state.resource_scope === "all" || (node.node_type === "Resource" && node.resource_scope === state.resource_scope))
      && (!search || node.node_id.toLowerCase().includes(search))
      && (!state.is_connected_only || connected_ids.has(node.node_id))
      && (!nearby_ids || nearby_ids.has(node.node_id)));
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

  function layout_graph_nodes(nodes) {
    const positions = new Map();
    const headings = [];
    let left = GRAPH_PADDING;
    let height = 160;
    nodeTypes.forEach((type) => {
      const group = nodes.filter((node) => node.node_type === type);
      if (!group.length) return;
      const columns = Math.min(GRAPH_MAX_COLUMNS, Math.ceil(Math.sqrt(group.length)));
      headings.push({ type, x: left, count: group.length });
      group.forEach((node, index) => {
        const position = { x: left + (index % columns) * GRAPH_CELL_SIZE,
          y: 85 + Math.floor(index / columns) * GRAPH_CELL_SIZE };
        positions.set(node.node_id, position);
        height = Math.max(height, position.y + GRAPH_PADDING);
      });
      left += columns * GRAPH_CELL_SIZE + GRAPH_PADDING;
    });
    return { positions, headings, width: Math.max(360, left), height };
  }

  function append_graph_edge(svg, edge, positions) {
    const source = positions.get(edge.source_id);
    const destination = positions.get(edge.target_id);
    const distance = Math.hypot(destination.x - source.x, destination.y - source.y) || 1;
    const offset_x = (destination.x - source.x) * 26 / distance;
    const offset_y = (destination.y - source.y) * 26 / distance;
    const path = edge.source_id === edge.target_id
      ? "M " + (source.x - 16) + " " + (source.y - 18) + " C " + (source.x - 70) + " " + (source.y - 65)
        + ", " + (source.x + 70) + " " + (source.y - 65) + ", " + (source.x + 16) + " " + (source.y - 18)
      : "M " + (source.x + offset_x) + " " + (source.y + offset_y) + " L "
        + (destination.x - offset_x) + " " + (destination.y - offset_y);
    const is_selected = edge.source_id === graph_state.selected_id || edge.target_id === graph_state.selected_id;
    const line = svgElement("path", { d: path, class: "graph-edge basis-" + edge.basis + (is_selected ? " is-selected" : ""),
      "marker-end": "url(#graph-arrow)" });
    line.append(svgElement("title", {}, edge.relationship_id + " · " + edge.relation_type + " · "
      + edge.basis + " · " + edge.source_id + " → " + edge.target_id));
    svg.append(line);
  }

  function append_graph_node(svg, options) {
    const { node, position, on_select } = options;
    const is_selected = node.node_id === graph_state.selected_id;
    const group = svgElement("g", { class: "graph-node node-" + node.node_type + (is_selected ? " is-selected" : ""),
      role: "button", tabindex: 0, "aria-label": node.node_type + " " + node.node_id,
      "aria-pressed": is_selected, "data-node-id": node.node_id });
    group.append(svgElement("title", {}, node.node_id + " · " + node.node_type + " · " + node.basis
      + (node.resource_scope ? " · " + node.resource_scope : "")));
    group.append(svgElement("circle", { cx: position.x, cy: position.y, r: 22 }));
    group.append(svgElement("text", { x: position.x, y: position.y + 4, "text-anchor": "middle", class: "node-symbol" }, node.node_type[0]));
    const short_id = node.node_id.length > 17 ? node.node_id.slice(0, 16) + "…" : node.node_id;
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
    const { nodes, edges, on_select } = options;
    if (!nodes.length) {
      target.append(element("p", "현재 조건에 맞는 노드 0개 · 원본 snapshot의 전체 건수와 구분한다.", "description"));
      return null;
    }
    const layout = layout_graph_nodes(nodes);
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
    edges.forEach((edge) => append_graph_edge(svg, edge, layout.positions));
    nodes.forEach((node) => append_graph_node(svg, { node, position: layout.positions.get(node.node_id), on_select }));
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

  function render_graph_explorer(target, data) {
    const toolbar = element("div", undefined, "graph-toolbar");
    const summary = element("p", undefined, "description");
    summary.id = "kg-display-counts";
    summary.setAttribute("role", "status");
    const help = element("p", "빈 영역 드래그·방향키로 이동, 휠·확대/축소 버튼으로 배율 변경. 노드 클릭 또는 Enter로 선택한다. 모든 배치는 화면 표현이며 새 관계를 추론하지 않는다.", "description");
    help.id = "kg-navigation-help";
    const scene = element("div");
    let camera = null;
    let synchronize_filters;
    function reset_pages() {
      graph_state.node_page = graph_state.edge_page = graph_state.related_page = graph_state.workflow_page = 0;
    }
    function on_filter_change() {
      reset_pages();
      graph_state.camera = null;
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
      synchronize_filters();
      render_scene();
    }
    const zoom_in = graph_button("확대", () => camera?.zoom(0.8));
    const zoom_out = graph_button("축소", () => camera?.zoom(1.25));
    const fit = graph_button("화면 맞춤", () => camera?.fit());
    const center = graph_button("선택 노드로 이동", () => camera?.center(graph_state.selected_id));
    const nearby = graph_button("주변 보기", () => {
      const selected_id = graph_state.selected_id;
      graph_state = create_graph_state();
      graph_state.selected_id = selected_id;
      graph_state.is_neighborhood = true;
      synchronize_filters();
      render_scene();
    });
    toolbar.append(zoom_in, zoom_out, fit, center, nearby, graph_button("전체 보기", show_all));
    function render_scene() {
      scene.replaceChildren();
      const nodes = filter_graph_nodes(data, graph_state);
      const visible_ids = new Set(nodes.map((node) => node.node_id));
      const edges = data.relationships.filter((edge) => visible_ids.has(edge.source_id) && visible_ids.has(edge.target_id));
      const connected_ids = new Set(data.relationships.flatMap((edge) => [edge.source_id, edge.target_id]));
      const isolated_count = data.nodes.filter((node) => !connected_ids.has(node.node_id)).length;
      summary.textContent = "전체 snapshot: 노드 " + data.nodes.length + "개 · 관계 " + data.relationships.length
        + "개 · 연결 없는 노드 " + isolated_count + "개. 현재 표시: 노드 " + nodes.length + "/" + data.nodes.length
        + "개 · 관계 " + edges.length + "/" + data.relationships.length + "개 · "
        + (graph_state.is_neighborhood ? "선택 노드의 1-hop 주변 보기" : "전체 범위 탐색") + ". 앞부분 자르기 없음.";
      const layout = element("div", undefined, "graph-explorer-layout");
      const drawing = element("div", undefined, "graph-drawing");
      const detail = element("aside", undefined, "graph-detail");
      detail.id = "kg-detail";
      detail.tabIndex = -1;
      detail.setAttribute("aria-label", "선택 노드 상세");
      layout.append(drawing, detail);
      scene.append(layout);
      camera = diagram(drawing, { nodes, edges, on_select: select_node });
      render_graph_detail(detail, { data, visible_ids, on_select: select_node });
      [zoom_in, zoom_out, fit].forEach((button) => { button.disabled = nodes.length === 0; });
      center.disabled = !visible_ids.has(graph_state.selected_id);
      nearby.disabled = graph_state.selected_id === null;
      nearby.setAttribute("aria-pressed", String(graph_state.is_neighborhood));
      const tables = element("div");
      tables.id = "kg-tables";
      scene.append(tables);
      append_graph_table(tables, { caption: "KG 노드", rows: nodes, page_key: "node_page", on_select: select_node,
        columns: [["node_id", "node_id"], ["종류", "node_type"], ["basis", "basis"], ["Resource 범위", "resource_scope"]] });
      append_graph_table(tables, { caption: "KG 관계", rows: edges, page_key: "edge_page",
        columns: [["관계 ID", "relationship_id"], ["source_id", "source_id"], ["target_id", "target_id"], ["관계", "relation_type"], ["basis", "basis"]] });
      append_graph_table(tables, { caption: "업무 흐름", rows: data.workflows, page_key: "workflow_page",
        columns: [["ID", "workflow_id"], ["이름", "name"], ["basis", "basis"], ["단계(명시된 order 순)",
          (row) => row.steps.map((step) => step.order + ": " + step.step_id + " · " + step.action).join(" → ")], ["의존 관계 수", "dependency_count"]] });
    }
    synchronize_filters = append_graph_filters(target, data, on_filter_change);
    target.append(toolbar, summary, help, scene,
      element("p", "basis: observed=관찰 · inferred=추론(점선) · verified=검증(굵은 선). 노드 basis 필터는 노드에 적용하며, 관계는 표시 노드 사이의 원본 관계를 유지한다. 주변 보기는 필터를 초기화하고 선택 노드와 직접 연결 노드를 표시한다.", "description"));
    render_scene();
  }

  function renderGraph(artifact) {
    const signature = JSON.stringify([currentRunId, artifact || null]);
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
    if (!data) return;
    target.append(element("p", data.graph_id + " · query revision " + data.graph_revision, "description"));
    if (data.snapshot_state !== "completed") {
      target.append(element("p", data.snapshot_state === "failed" ? "structure_snapshot 조회 실패 · 빈 그래프로 처리하지 않는다." : "structure_snapshot 결과 없음 · 빈 그래프로 처리하지 않는다.", "description"));
      return;
    }
    counts(target, { nodes: data.nodes.length, relationships: data.relationships.length, workflows: data.workflows.length });
    render_graph_explorer(target, data);
  }

  function processPanel(target, title, artifact, columns, rows) {
    target.append(element("h4", title), element("p", stateText(artifact), "description"));
    if (artifact?.view) table(target, title, columns, rows);
  }

  function renderDiagnosisOverview(target, byType) {
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
    for (const [label, artifact] of [["후보", candidates], ["검증", verification], ["Reporter", report]]) {
      section.append(element("p", label + " 산출물: " + stateText(artifact), "description"));
    }
    section.append(element("p", "후보는 candidate 수, 검증은 시나리오별 결과 수, 확정은 Reporter finding 수다. 서로 합산하거나 고유 취약점 수로 해석하지 않는다.", "description"),
      element("p", "검증 조건 통과(success)는 시나리오 조건을 통과했다는 뜻이며 확정 취약점이 아니다. 확정 여부와 사유는 Reporter 결과를 그대로 표시한다. 미확인은 0건이 아니며 부분 결과는 전체 진단을 대표하지 않는다.", "description"));
    target.append(section);
  }

  function renderProcess(byType) {
    const target = document.getElementById("process-view");
    target.replaceChildren();
    renderDiagnosisOverview(target, byType);
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
