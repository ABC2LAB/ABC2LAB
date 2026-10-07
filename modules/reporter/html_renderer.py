"""Dependency-free local HTML rendering for diagnosis reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from modules.reporter.exceptions import ContractValidationError
from modules.reporter.models import ReportInputs
from modules.reporter.utils.atomic_writer import write_text_atomically
from modules.reporter.utils.paths import resolve_trusted_relative_path

STATUS_LABELS = {
    "confirmed": "취약점 확인",
    "suspected": "위반 의심",
    "not_confirmed": "미재현",
    "indeterminate": "판단 불가",
    "policy_blocked": "정책 차단",
    "approval_pending": "승인 대기",
}
SUMMARY_FIELDS = (
    ("candidate_count", "고유 후보"),
    ("confirmed_count", "취약점 확인"),
    ("not_confirmed_count", "미재현"),
    ("suspected_count", "위반 의심"),
    ("indeterminate_count", "판단 불가"),
    ("policy_blocked_count", "정책 차단"),
    ("approval_pending_count", "승인 대기"),
)


@dataclass(frozen=True)
class HtmlContent:
    target_url: Any
    summary: Mapping[str, Any]
    findings: Sequence[Any]
    limitations: Sequence[Any]
    errors: Sequence[Any]


def publish_diagnosis_html(
    inputs: ReportInputs,
    artifact: Mapping[str, Any],
) -> str:
    """Render and atomically publish the immutable local report."""
    relative_path = diagnosis_html_relative_path(inputs.request.iteration)
    output_path = resolve_trusted_relative_path(
        inputs.request.run_root,
        relative_path,
    )
    write_text_atomically(output_path, render_diagnosis_html(artifact))
    return relative_path


def remove_diagnosis_html(run_root: Path, relative_path: str) -> None:
    """Roll back the HTML written immediately before JSON publication failed."""
    output_path = resolve_trusted_relative_path(run_root, relative_path)
    output_path.unlink(missing_ok=True)


def diagnosis_html_relative_path(iteration: int) -> str:
    return f"reports/diagnosis_report-iteration-{iteration:03d}.html"


def render_diagnosis_html(artifact: Mapping[str, Any]) -> str:
    if artifact.get("artifact_type") != "diagnosis_report":
        raise ContractValidationError("HTML 입력이 diagnosis_report가 아님")
    data = artifact.get("data")
    if data is not None and not isinstance(data, Mapping):
        raise ContractValidationError("diagnosis_report data가 object가 아님")

    target_url = data.get("target_url", "확인할 수 없음") if data else "확인할 수 없음"
    summary = data.get("summary", {}) if data else {}
    findings = data.get("findings", []) if data else []
    limitations = data.get("limitations", []) if data else []
    errors = artifact.get("errors", [])
    if not isinstance(summary, Mapping):
        raise ContractValidationError("diagnosis_report summary가 object가 아님")
    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
        raise ContractValidationError("diagnosis_report findings가 array가 아님")
    if not isinstance(limitations, Sequence) or isinstance(
        limitations,
        (str, bytes),
    ):
        raise ContractValidationError("diagnosis_report limitations가 array가 아님")

    content = HtmlContent(
        target_url=target_url,
        summary=summary,
        findings=findings,
        limitations=limitations,
        errors=errors if isinstance(errors, Sequence) else (),
    )
    return _document(artifact, content)


def _document(
    artifact: Mapping[str, Any],
    content: HtmlContent,
) -> str:
    status = str(artifact.get("status", "failed"))
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
  <title>ABC2LAB 진단 리포트</title>
  <style>{_stylesheet()}</style>
</head>
<body>
  <main>
    <header>
      <p class="eyebrow">ABC2LAB · authorization &amp; business logic DAST</p>
      <h1>진단 리포트</h1>
      <dl class="metadata">
        <div><dt>대상</dt><dd>{_text(content.target_url)}</dd></div>
        <div><dt>실행</dt><dd>{_text(artifact.get("run_id"))}</dd></div>
        <div><dt>회차</dt><dd>{_text(artifact.get("iteration"))}</dd></div>
        <div><dt>생성 시각</dt><dd>{_text(artifact.get("created_at"))}</dd></div>
      </dl>
      <p class="artifact-status status-{_status_class(status)}">산출물 상태: {_text(status)}</p>
    </header>
    {_render_summary(content.summary)}
    <section>
      <h2>진단 항목</h2>
      {_render_findings(content.findings)}
    </section>
    <section>
      <h2>제한 사항</h2>
      {_render_list(content.limitations, "기록된 제한 사항 없음")}
    </section>
    <section>
      <h2>처리 오류</h2>
      {_render_errors(content.errors)}
    </section>
  </main>
</body>
</html>
"""


def _render_summary(summary: Mapping[str, Any]) -> str:
    cards = "".join(
        (
            '<article class="summary-card">'
            f"<strong>{_text(summary.get(field, 0))}</strong>"
            f"<span>{_text(label)}</span>"
            "</article>"
        )
        for field, label in SUMMARY_FIELDS
    )
    return f'<section><h2>요약</h2><div class="summary-grid">{cards}</div></section>'


def _render_findings(findings: Sequence[Any]) -> str:
    if not findings:
        return '<p class="empty">리포트 항목 없음</p>'
    rendered = []
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise ContractValidationError("diagnosis_report finding이 object가 아님")
        status = str(finding.get("status", "indeterminate"))
        rendered.append(
            '<article class="finding">'
            '<div class="finding-heading">'
            f'<span class="badge status-{_status_class(status)}">'
            f"{_text(STATUS_LABELS.get(status, status))}</span>"
            f"<h3>{_text(finding.get('title'))}</h3>"
            "</div>"
            f"<p>{_text(finding.get('description'))}</p>"
            '<dl class="finding-meta">'
            f"<div><dt>후보</dt><dd>{_text(finding.get('candidate_id'))}</dd></div>"
            f"<div><dt>시나리오</dt><dd>{_text(finding.get('scenario_id'))}</dd></div>"
            f"<div><dt>검증</dt><dd>{_text(finding.get('verification_id'))}</dd></div>"
            f"<div><dt>분류</dt><dd>{_text(finding.get('category'))}</dd></div>"
            f"<div><dt>유형</dt><dd>{_text(finding.get('vulnerability_type'))}</dd></div>"
            "</dl>"
            f"{_render_evidence(finding.get('evidence_refs', []))}"
            "</article>"
        )
    return "".join(rendered)


def _render_evidence(evidence_refs: Any) -> str:
    if not isinstance(evidence_refs, Sequence) or isinstance(
        evidence_refs,
        (str, bytes),
    ):
        raise ContractValidationError("finding evidence_refs가 array가 아님")
    if not evidence_refs:
        return '<p class="empty evidence">연결된 공유 근거 없음</p>'
    items = []
    for reference in evidence_refs:
        if not isinstance(reference, Mapping):
            raise ContractValidationError("EvidenceRef가 object가 아님")
        path = str(reference.get("path", ""))
        href = escape(f"../{path}", quote=True)
        items.append(
            "<li>"
            f'<a href="{href}">{_text(reference.get("evidence_id"))}</a>'
            f" · {_text(reference.get('kind'))}"
            f" · redacted={_text(reference.get('redacted'))}"
            "</li>"
        )
    return '<div class="evidence"><h4>근거</h4><ul>' + "".join(items) + "</ul></div>"


def _render_errors(errors: Sequence[Any]) -> str:
    if not errors:
        return '<p class="empty">기록된 처리 오류 없음</p>'
    items = []
    for error in errors:
        if not isinstance(error, Mapping):
            raise ContractValidationError("diagnosis_report ErrorItem이 object가 아님")
        items.append(
            "<li>"
            f"<strong>{_text(error.get('code'))}</strong>: "
            f"{_text(error.get('message'))}"
            f" (item={_text(error.get('item_ref'))}, "
            f"retryable={_text(error.get('retryable'))})"
            "</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _render_list(values: Sequence[Any], empty_message: str) -> str:
    if not values:
        return f'<p class="empty">{_text(empty_message)}</p>'
    return "<ul>" + "".join(f"<li>{_text(value)}</li>" for value in values) + "</ul>"


def _text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    return escape(str(value), quote=True)


def _status_class(status: str) -> str:
    return status if status in {*STATUS_LABELS, "completed", "partial", "failed"} else "neutral"


def _stylesheet() -> str:
    return """
:root { color-scheme: light; font-family: Inter, Pretendard, system-ui, sans-serif; color: #16202a; background: #eef2f5; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 18px; }
main { max-width: 1080px; margin: 0 auto; }
header, section { background: #fff; border: 1px solid #dbe2e8; border-radius: 14px; padding: 24px; margin-bottom: 18px; }
.eyebrow { color: #4f6475; font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
h1, h2, h3, h4, p { margin-top: 0; }
h1 { margin-bottom: 20px; font-size: 32px; }
h2 { font-size: 20px; }
h3 { margin: 0; font-size: 17px; }
h4 { margin-bottom: 8px; }
.metadata, .finding-meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 0; }
dt { color: #667786; font-size: 12px; font-weight: 700; }
dd { margin: 4px 0 0; overflow-wrap: anywhere; }
.artifact-status, .badge { display: inline-block; border-radius: 999px; padding: 5px 10px; font-size: 12px; font-weight: 700; }
.artifact-status { margin: 20px 0 0; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(125px, 1fr)); gap: 10px; }
.summary-card { padding: 16px; background: #f5f8fa; border-radius: 10px; }
.summary-card strong, .summary-card span { display: block; }
.summary-card strong { font-size: 25px; }
.summary-card span { margin-top: 4px; color: #5f707e; font-size: 13px; }
.finding { border-top: 1px solid #e5eaee; padding: 20px 0; }
.finding:first-of-type { border-top: 0; padding-top: 4px; }
.finding-heading { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.finding-meta { background: #f7f9fb; border-radius: 8px; padding: 12px; }
.evidence { margin-top: 14px; }
.empty { color: #6c7c89; }
a { color: #155eef; overflow-wrap: anywhere; }
li + li { margin-top: 6px; }
.status-confirmed, .status-failed { background: #fee4e2; color: #b42318; }
.status-suspected, .status-partial, .status-approval_pending { background: #fef0c7; color: #93370d; }
.status-not_confirmed, .status-completed { background: #dcfae6; color: #067647; }
.status-indeterminate, .status-policy_blocked, .status-neutral { background: #e9edf1; color: #344054; }
@media print { body { background: #fff; padding: 0; } header, section { break-inside: avoid; box-shadow: none; } }
"""
