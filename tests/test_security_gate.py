import json

import pytest

from deploy.security_gate import check_report


@pytest.mark.parametrize(
    ("severity", "fixed", "allowed"),
    [
        ("HIGH", "2.0", False),
        ("CRITICAL", "2.0", False),
        ("CRITICAL", "", True),
        ("MEDIUM", "2.0", True),
    ],
)
def test_patchable_severity_gate(tmp_path, severity, fixed, allowed):
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "SchemaVersion": 2,
                "Results": [
                    {
                        "Vulnerabilities": [
                            {
                                "VulnerabilityID": "CVE-test",
                                "PkgName": "test",
                                "Severity": severity,
                                "FixedVersion": fixed,
                            }
                        ]
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert check_report(report) is allowed


@pytest.mark.parametrize("data", [{}, {"SchemaVersion": 2, "Results": []}])
def test_missing_scan_fails_closed(tmp_path, data):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        check_report(report)
