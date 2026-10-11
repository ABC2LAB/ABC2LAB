"use strict";

const sections = Array.from(document.querySelectorAll("main > section"));
const links = Array.from(document.querySelectorAll("nav a"));

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

window.addEventListener("hashchange", () => showSection(location.hash.slice(1)));
showSection(location.hash.slice(1));
checkWebStatus();
