from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.reports import InvalidReport, prepare_text
from app.schemas.report import ReportCreate

TEXT = "I was in Fiji when a driver demanded extra money after I agreed to a fixed fare."


@pytest.mark.parametrize(
    "suffix,flag",
    [
        ("Email me at traveler@example.com", "email_redacted"),
        ("Call +1 212 555 0199", "phone_redacted"),
        ("Card 4111111111111111", "long_number_redacted"),
    ],
)
def test_obvious_pii_is_redacted_and_flagged(suffix, flag):
    result, flags = prepare_text(TEXT + " " + suffix)
    assert flag in flags and "[REDACTED]" in result
    assert "traveler@example.com" not in result and "4111111111111111" not in result


def test_occurrence_timestamp_is_not_mistaken_for_contact_information():
    text = TEXT + " On 2026-01-02T10:00:00+00:00."
    assert prepare_text(text) == (text, [])


@pytest.mark.parametrize(
    "text", [" " * 25, "https://example.com/very-long-path", "scam " * 20, TEXT + "\x00"]
)
def test_obvious_junk_is_rejected(text):
    with pytest.raises(InvalidReport):
        prepare_text(text)


def test_threats_are_held_for_review_without_assessing_report_truth():
    _, flags = prepare_text(TEXT + " I will hurt the driver.")
    assert "threat_language" in flags


@pytest.mark.parametrize(
    "changes",
    [
        {"provenance_class": "VERIFIED"},
        {"occurred_at": "2026-01-02T10:00:00"},
        {"moderation_status": "accepted"},
        {"report_text": "x" * 8001},
        {"location_id": "invalid"},
        {"parent_id": "00000000-0000-0000-0000-000000000000"},
    ],
)
def test_intake_contract_rejects_invalid_or_privileged_fields(changes):
    with pytest.raises(ValidationError):
        ReportCreate.model_validate(
            {"report_text": TEXT, "provenance_class": "FIRST_HAND", **changes}
        )


def test_explicit_provenance_and_aware_occurrence_are_preserved():
    report = ReportCreate(
        report_text=TEXT,
        provenance_class="SECOND_HAND",
        occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    assert report.provenance_class.value == "SECOND_HAND"
