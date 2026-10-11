"use strict";

const sections = Array.from(document.querySelectorAll("main > section"));
const links = Array.from(document.querySelectorAll("nav a"));
const form = document.getElementById("input-form");
const inputs = document.getElementById("diagnostic-inputs");
const accounts = document.getElementById("accounts");
const inputStatus = document.getElementById("input-status");
const clearButton = document.getElementById("clear-input");
let maxAccounts = 0;
let csrfToken = null;
let inputEnabled = false;
let expirationTimer = null;
let accountSequence = 0;

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
    ["login_id", "로그인 ID", "password"], ["password", "비밀번호", "password"],
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
    input.maxLength = type === "password" ? 4096 : 128;
    if (type === "text") {
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
  accounts.querySelectorAll("input[type=password]").forEach((input) => { input.value = ""; });
}

function showPreparation(preparation) {
  clearTimeout(expirationTimer);
  const isPrepared = preparation.status === "prepared";
  inputs.disabled = isPrepared || !inputEnabled;
  clearButton.disabled = !isPrepared;
  if (!isPrepared) return;
  const seconds = Math.max(0, preparation.expires_in_seconds);
  inputStatus.textContent = "입력 준비 완료 · 계정 " + preparation.account_count + "개 · 진단 미실행 · 최대 " + Math.ceil(seconds / 60) + "분간 보관";
  expirationTimer = setTimeout(() => {
    csrfToken = null;
    inputs.disabled = true;
    clearButton.disabled = true;
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
    const dataset = document.getElementById("dataset");
    dataset.replaceChildren(new Option("데이터셋 선택", ""));
    result.dataset_ids.forEach((id) => { dataset.append(new Option(id, id)); });
    result.accounts.forEach((account) => addAccount(account));
    inputs.disabled = !inputEnabled;
    inputStatus.textContent = inputEnabled ? "입력 가능 · 진단 실행은 아직 미연결" : result.issues.map((code) => inputMessages[code] || "입력 설정을 확인해야 한다.").join(" ");
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
    inputStatus.textContent = "입력 준비 취소 · 비밀값 참조와 임시 설정 폐기 완료";
  } catch {
    clearButton.disabled = false;
    inputStatus.textContent = "취소 확인 실패. 새로고침 후 준비 상태를 확인해야 한다.";
  }
});

document.getElementById("add-account").addEventListener("click", () => addAccount());
window.addEventListener("pagehide", clearCredentialFields);
window.addEventListener("hashchange", () => showSection(location.hash.slice(1)));
showSection(location.hash.slice(1));
checkWebStatus();
loadInputOptions();
