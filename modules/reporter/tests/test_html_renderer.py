from copy import deepcopy

import pytest

from modules.reporter.exceptions import ContractValidationError
from modules.reporter.html_renderer import (
    diagnosis_html_relative_path,
    render_diagnosis_html,
)
from modules.reporter.utils.validation import load_json


def test_render_diagnosis_html_escapes_untrusted_text(fixture_root) -> None:
    artifact = load_json(
        fixture_root
        / "runs/run_demo_001/artifacts/iteration-000/reporter/diagnosis_report.json"
    )
    changed = deepcopy(artifact)
    changed["data"]["findings"][0]["title"] = '<script>alert("x")</script>'
    changed["data"]["findings"][0]["description"] = '<img src=x onerror="x">'

    document = render_diagnosis_html(changed)

    assert "<script>" not in document
    assert "<img src=x" not in document
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in document
    assert "default-src 'none'" in document


def test_render_diagnosis_html_contains_summary_and_evidence(fixture_root) -> None:
    artifact = load_json(
        fixture_root
        / "runs/run_demo_001/artifacts/iteration-000/reporter/diagnosis_report.json"
    )

    document = render_diagnosis_html(artifact)

    assert "진단 리포트" in document
    assert "고유 후보" in document
    assert "취약점 확인" in document
    assert "../evidence/" in document
    assert "<script" not in document


def test_render_failed_diagnosis_html_preserves_errors(fixture_root) -> None:
    artifact = load_json(fixture_root / "status/diagnosis_report.failed.json")

    document = render_diagnosis_html(artifact)

    assert "처리 오류" in document
    assert artifact["errors"][0]["code"] in document
    assert "확인할 수 없음" in document


def test_render_rejects_wrong_artifact_type() -> None:
    with pytest.raises(ContractValidationError, match="diagnosis_report"):
        render_diagnosis_html({"artifact_type": "evaluation_results"})


def test_html_path_is_iteration_scoped() -> None:
    assert (
        diagnosis_html_relative_path(12)
        == "reports/diagnosis_report-iteration-012.html"
    )
