"use strict";

const sections = Array.from(document.querySelectorAll("main > section"));
const links = Array.from(document.querySelectorAll("nav a"));
const form = document.getElementById("input-form");
const inputs = document.getElementById("diagnostic-inputs");
const accounts = document.getElementById("accounts");
const inputStatus = document.getElementById("input-status");
const clearButton = document.getElementById("clear-input");
const startButton = document.getElementById("start-diagnosis");
let maxAccounts = 0;
let csrfToken = null;
let inputEnabled = false;
let expirationTimer = null;
let accountSequence = 0;
let preparationId = null;
let canRun = false;
let isRunBusy = false;
let runTimer = null;
let runRequestSequence = 0;

const inputMessages = {
  TEMPLATE_UNAVAILABLE: "Collector TOML 연결이 필요하다.",
  TEMPLATE_INVALID: "Collector TOML의 공개 설정·형식을 확인해야 한다.",
  TEMPLATE_LOGIN_MISSING: "Collector TOML의 로그인 설정이 필요하다.",
  DATASETS_UNAVAILABLE: "datasets 읽기 전용 연결을 확인해야 한다.",
  DATASETS_EMPTY: "선택할 데이터셋이 없다. 디렉터리와 ground_truth.json 파일 위치를 확인해야 한다.",
  DATASET_INVALID: "선택한 데이터셋을 확인해야 한다.",
  TARGET_URL_INVALID: "대상 URL 형식을 확인해야 한다. 인증정보·query·fragment는 사용할 수 없다.",
  ACCOUNT_INVALID: "계정의 역할·별칭·로그인 ID·비밀번호와 별칭 중복을 확인해야 한다.",
  ACCOUNT_ALIAS_INVALID: "공개 계정 별칭은 로그인 ID와 다르게 지정해야 한다.",
  INPUT_SESSION_INVALID: "입력 세션이 만료됐다. 화면을 새로고침해야 한다.",
  INPUT_TOO_LARGE: "입력 크기 제한을 초과했다.",
  INPUT_PREPARATION_FAILED: "임시 설정 준비에 실패했다.",
  PREPARATION_UNAVAILABLE: "준비 입력이 없거나 이미 소비됐다. 준비 상태를 다시 확인해야 한다.",
  RUNNER_NOT_READY: "Runner·실모델 설정·정책·산출물 저장소 연결을 확인해야 한다.",
  RUNNER_UNAVAILABLE: "기존 pipeline.py 연결을 확인해야 한다.",
  RUN_STORAGE_UNAVAILABLE: "산출물 저장 폴더와 쓰기 권한을 확인해야 한다.",
  SEMANTIC_CONFIG_INVALID: "Semantic Analyzer의 Ollama provider·실제 모델 설정이 필요하다.",
  SCENARIO_CONFIG_INVALID: "Scenario Generator의 Ollama provider·실제 모델 설정이 필요하다.",
  POLICY_CONFIG_UNAVAILABLE: "읽기 전용 Safety Policy 원본 연결이 필요하다.",
  MATCHING_PROFILE_INVALID: "Reporter matching profile 설정을 확인해야 한다.",
  RUNNER_BUSY: "다른 진단이 실행 중이다. 이번 입력은 시작하지 않았다.",
  RUN_RECORD_LIMIT: "Web 실행 기록 한도에 도달했다. 서버를 종료한 뒤 다시 실행해야 한다.",
  RUNNER_START_FAILED: "Runner 프로세스 시작에 실패했다. 입력을 다시 준비해야 한다.",
};

class InputRequestError extends Error {}

function showSection(fragment) {
  const id = sections.some((section) => section.id === fragment) ? fragment : "input";
  sections.forEach((section) => { section.hidden = section.id !== id; });
  links.forEach((link) => {
    if (link.getAttribute("href") === "#" + id) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });
  if (["results", "approval", "evaluation"].includes(id)) window.abc2labResults?.refresh();
}

async function checkWebStatus() {
  const label = document.getElementById("web-status");
  try {
    const response = await fetch("/health", { cache: "no-store", mode: "same-origin" });
    if (!response.ok) throw new Error("Web unavailable");
    const result = await response.json();
    if (result.status !== "ok" || result.scope !== "web_only") throw new Error("Unexpected health");
    label.textContent = "Web 정상 · 진단 준비 미확인";
  } catch {
    label.textContent = "Web 응답 확인 실패";
  }
}

function addAccount(account = {}) {
  if (accounts.children.length >= maxAccounts) return;
  accountSequence += 1;
  const row = document.createElement("div");
  row.className = "account-row";
  const fields = document.createElement("div");
  fields.className = "account-fields";
  const specifications = [
    ["role", "역할", "text"], ["alias", "공개 별칭", "text"],
    ["login_id", "로그인 ID", "text"], ["password", "비밀번호", "password"],
  ];
  for (const [name, title, type] of specifications) {
    const group = document.createElement("div");
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.id = "account-" + accountSequence + "-" + name;
    input.dataset.field = name;
    input.type = type;
    input.autocomplete = "off";
    input.required = true;
    input.spellcheck = false;
    const isPublicAccountField = name === "role" || name === "alias";
    input.maxLength = isPublicAccountField ? 128 : 4096;
    if (isPublicAccountField) {
      input.pattern = "[a-z][a-z0-9_]*";
      input.value = typeof account[name] === "string" ? account[name] : "";
    }
    label.htmlFor = input.id;
    label.textContent = title;
    group.append(label, input);
    fields.append(group);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "secondary";
  remove.textContent = "이 계정 삭제";
  remove.addEventListener("click", () => {
    row.querySelectorAll("input").forEach((input) => { input.value = ""; });
    row.remove();
    document.getElementById("add-account").disabled = false;
  });
  row.append(fields, remove);
  accounts.append(row);
  document.getElementById("add-account").disabled = accounts.children.length >= maxAccounts;
}

function clearCredentialFields() {
  accounts.querySelectorAll('[data-field="login_id"], [data-field="password"]').forEach((input) => { input.value = ""; });
}

function showPreparation(preparation) {
  clearTimeout(expirationTimer);
  const isPrepared = preparation.status === "prepared";
  preparationId = isPrepared ? preparation.preparation_id : null;
  inputs.disabled = isPrepared || !inputEnabled || isRunBusy;
  clearButton.disabled = !isPrepared;
  startButton.disabled = !isPrepared || !canRun || isRunBusy || !csrfToken;
  if (!isPrepared) return;
  const seconds = Math.max(0, preparation.expires_in_seconds);
  inputStatus.textContent = "입력 준비 완료 · 계정 " + preparation.account_count + "개 · 진단 미실행 · 최대 " + Math.ceil(seconds / 60) + "분간 보관";
  expirationTimer = setTimeout(() => {
    csrfToken = null;
    inputs.disabled = true;
    clearButton.disabled = true;
    startButton.disabled = true;
    preparationId = null;
    inputStatus.textContent = "입력 세션이 만료됐다. 새로고침 후 비밀값을 다시 입력해야 한다.";
  }, seconds * 1000 + 100);
}

async function loadInputOptions() {
  try {
    const response = await fetch("/api/input-options", { cache: "no-store", mode: "same-origin" });
    if (!response.ok) throw new Error("Input options unavailable");
    const result = await response.json();
    if (!Number.isInteger(result.limits.max_accounts) || result.limits.max_accounts < 1) throw new Error("Invalid input limit");
    maxAccounts = result.limits.max_accounts;
    csrfToken = result.csrf_token;
    inputEnabled = result.input_enabled === true;
    canRun = result.diagnosis_enabled === true;
    isRunBusy = result.runner.is_busy === true;
    const dataset = document.getElementById("dataset");
    dataset.replaceChildren(new Option("데이터셋 선택", ""));
    result.dataset_ids.forEach((id) => { dataset.append(new Option(id, id)); });
    if (accounts.children.length === 0) result.accounts.forEach((account) => addAccount(account));
    inputs.disabled = !inputEnabled;
    const issues = result.issues.concat(result.runner.issues);
    inputStatus.textContent = issues.length ? issues.map((code) => inputMessages[code] || "입력 설정을 확인해야 한다.").join(" ") : "입력 가능 · 실제 모델 준비·대상 접속은 진단 시 확인한다.";
    showPreparation(result.preparation);
  } catch {
    inputs.disabled = true;
    inputStatus.textContent = "입력 설정 확인 실패. 연결 상태를 확인하고 새로고침해야 한다.";
  }
}

async function postInput(path, payload) {
  const response = await fetch(path, {
    method: "POST", mode: "same-origin", credentials: "same-origin", cache: "no-store",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new InputRequestError(inputMessages[result.error] || "입력 처리 실패. 설정·입력을 확인해야 한다.");
  return result;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    target_url: document.getElementById("target-url").value.trim(),
    dataset_id: document.getElementById("dataset").value,
    accounts: Array.from(accounts.children, (row) => Object.fromEntries(
      Array.from(row.querySelectorAll("input"), (input) => [input.dataset.field, input.value])
    )),
  };
  inputs.disabled = true;
  inputStatus.textContent = "입력 준비 중 · 진단 미실행";
  try {
    const result = await postInput("/api/input-preparations", payload);
    showPreparation(result);
  } catch (error) {
    inputs.disabled = !inputEnabled;
    inputStatus.textContent = error instanceof InputRequestError ? error.message : "Web 응답 확인 실패. 새로고침 후 준비 상태를 확인해야 한다.";
  } finally {
    clearCredentialFields();
    payload.accounts.forEach((account) => { account.login_id = ""; account.password = ""; });
  }
});

clearButton.addEventListener("click", async () => {
  clearButton.disabled = true;
  try {
    await postInput("/api/input-preparations/clear", {});
    clearTimeout(expirationTimer);
    clearCredentialFields();
    inputs.disabled = !inputEnabled;
    preparationId = null;
    startButton.disabled = true;
    inputStatus.textContent = "입력 준비 취소 · 비밀값 참조와 임시 설정 폐기 완료";
  } catch {
    clearButton.disabled = false;
    inputStatus.textContent = "취소 확인 실패. 새로고침 후 준비 상태를 확인해야 한다.";
  }
});

function fillList(id, values) {
  const target = document.getElementById(id);
  target.replaceChildren();
  values.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    target.append(item);
  });
}

function showRun(result) {
  window.abc2labResults?.setRun(result);
  const labels = {
    idle: "진단 시작 전", running: "Runner 실행 중 · 현재 단계·진행률 미제공",
    completed: "Runner completed · 취약점 없음 또는 모든 시나리오 실행을 의미하지 않는다.",
    partial: "Runner partial · 일부 처리 또는 세션 연결에 제한이 있다.",
    failed: "Runner failed · 실패 또는 미실행 단계를 확인해야 한다.",
    stopped: "Web 종료로 Runner 중단",
  };
  document.getElementById("run-status").textContent = labels[result.state] || "실행 상태 확인 불가";
  if (result.state === "idle") return;
  const issueMessages = {
    RUNNER_RESULT_UNAVAILABLE: "종료 요약을 확인할 수 없다. 정상 완료로 처리하지 않는다.",
    INPUT_CLEANUP_FAILED: "임시 입력 정리에 실패했다. 운영자가 확인해야 한다.",
    WEB_SHUTDOWN: "서버 종료로 중단됐다.",
  };
  document.getElementById("run-meta").textContent = result.run_id + " · iteration 0 · 경과 " + result.elapsed_seconds + "초 · 종료 코드 " + (result.exit_code === null ? "미확정" : result.exit_code) + (result.issue ? " · " + (issueMessages[result.issue] || "실행 확인 필요") : "");
  fillList("artifact-status", result.artifacts.map((item) => item.artifact_type + " · " + (item.is_present ? "파일 존재" : "미발행")));
  fillList("step-status", result.summary ? result.summary.steps.map((item) => item.step + " · " + item.status + " · 오류 " + item.error_count + "건") : [result.state === "running" ? "Runner 종료 후 확인 가능" : "유효한 종료 요약 없음"]);
}

async function refreshRun() {
  const sequence = ++runRequestSequence;
  clearTimeout(runTimer);
  try {
    const response = await fetch("/api/run-status", { cache: "no-store", mode: "same-origin" });
    if (!response.ok) throw new Error("Run unavailable");
    const result = await response.json();
    if (sequence !== runRequestSequence) return;
    showRun(result);
    if (["results", "approval", "evaluation"].includes(location.hash.slice(1))) await window.abc2labResults?.refresh();
    const wasBusy = isRunBusy;
    isRunBusy = result.is_busy === true;
    if (isRunBusy) {
      inputs.disabled = true;
      startButton.disabled = true;
      runTimer = setTimeout(refreshRun, 2000);
      if (result.state === "idle") {
        document.getElementById("run-status").textContent = "다른 브라우저의 진단이 실행 중이다. 해당 실행의 상세 정보는 표시하지 않는다.";
      }
    } else if (wasBusy) {
      await loadInputOptions();
    }
  } catch {
    if (sequence !== runRequestSequence) return;
    document.getElementById("run-status").textContent = "실행 상태 조회 실패 · 실패/성공으로 판정하지 않는다. 다시 확인해야 한다.";
  }
}

startButton.addEventListener("click", async () => {
  if (!preparationId || !csrfToken) return;
  runRequestSequence += 1;
  startButton.disabled = true;
  clearButton.disabled = true;
  const id = preparationId;
  inputStatus.textContent = "Runner 시작 요청 중";
  try {
    const result = await postInput("/api/runs", { preparation_id: id });
    clearTimeout(expirationTimer);
    preparationId = null;
    isRunBusy = true;
    inputs.disabled = true;
    inputStatus.textContent = "준비 입력 전달 완료 · 인증값은 자식 프로세스에만 전달했다.";
    showRun(result);
    location.hash = "progress";
    await refreshRun();
  } catch (error) {
    await loadInputOptions();
    inputStatus.textContent = error instanceof InputRequestError ? error.message : "실행 응답 확인 실패. 진행 화면에서 상태를 확인해야 한다. 자동 재요청하지 않는다.";
    await refreshRun();
  }
});

document.getElementById("refresh-run").addEventListener("click", refreshRun);

document.getElementById("add-account").addEventListener("click", () => addAccount());
window.addEventListener("pagehide", () => {
  clearCredentialFields();
  clearTimeout(runTimer);
});
window.addEventListener("hashchange", () => showSection(location.hash.slice(1)));
showSection(location.hash.slice(1));
checkWebStatus();
loadInputOptions().then(refreshRun);
