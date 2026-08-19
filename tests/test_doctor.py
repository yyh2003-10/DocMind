"""系统环境诊断 (doctor) 模块测试。"""

from __future__ import annotations

from doc2mind.core.doctor import DoctorReport, run_diagnostics


def test_doctor_report_structure() -> None:
    report = run_diagnostics(check_network=False)
    assert isinstance(report, DoctorReport)
    assert report.overall_status in ("ok", "warning", "error")
    assert 0 <= report.score <= 100
    assert len(report.checks) >= 5

    categories = {c.category for c in report.checks}
    assert "python" in categories
    assert "storage" in categories
    assert "embedder" in categories
    assert "hardware" in categories


def test_doctor_report_to_dict() -> None:
    report = run_diagnostics(check_network=False)
    d = report.to_dict()
    assert "overall_status" in d
    assert "score" in d
    assert "checks" in d
    assert isinstance(d["checks"], list)
    for c in d["checks"]:
        assert "name" in c
        assert "status" in c
        assert "message" in c
