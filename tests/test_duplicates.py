from uuid import uuid4

import pytest

from app.db.duplicates import _assess
from app.duplicates import compare, identity, normalize

QUOTE = (
    "At the station a driver refused the meter and demanded a much higher fixed fare "
    "before taking the traveler to the hotel late that evening."
)


def item(**changes):
    return {
        "claim_id": str(uuid4()),
        "document_id": str(uuid4()),
        "normalized_claim": "driver refused meter and demanded inflated fare",
        "quote": QUOTE,
        "platform": "reddit",
        "author": None,
        "thread": None,
        "url": None,
        "cross_post_of": None,
        "content_hash": str(uuid4()),
        "clean_hash": None,
        "location_id": "city",
        "occurred_at": None,
        "stance": "supports",
        "vector": None,
        **changes,
    }


@pytest.mark.parametrize(
    "signal,value",
    [
        ("document_id", "same-doc"),
        ("url", "https://example.org/a"),
        ("content_hash", "same-content"),
        ("clean_hash", "same-clean"),
    ],
)
def test_matching_text_and_origin_confirm_copies(signal, value):
    assert (
        compare(item(**{signal: value}), item(**{signal: value}))["classification"] == "duplicate"
    )


def test_matching_summary_and_shared_quote_without_copy_context_need_review():
    result = compare(item(), item())
    assert result["classification"] == "suspected"
    assert result["signals"]["exact_quote"]


def test_same_article_can_contain_different_scam_claims():
    result = compare(
        item(document_id="same"), item(document_id="same", normalized_claim="ATM skimming")
    )
    assert result["classification"] != "duplicate"


@pytest.mark.parametrize("signal,value", [("author", "user-123"), ("thread", "thread-123")])
def test_identity_or_thread_alone_never_confirms_copy(signal, value):
    result = compare(
        item(**{signal: value}, quote="First short report"),
        item(**{signal: value}, quote="Another short report"),
    )
    assert result["classification"] == "suspected"


def test_author_requires_platform_and_a_substantial_exact_quote():
    assert compare(item(author="u123"), item(author="u123"))["classification"] == "duplicate"
    assert (
        compare(item(author="u123"), item(author="u123", platform="other"))["classification"]
        != "duplicate"
    )
    assert (
        compare(
            item(author="u123", quote="Meter refused"), item(author="u123", quote="Meter refused")
        )["classification"]
        != "duplicate"
    )


def test_explicit_crosspost_reference_with_copied_text_can_confirm_duplicate():
    result = compare(
        item(url="https://example.org/original"), item(cross_post_of="https://example.org/original")
    )
    assert result["classification"] == "duplicate"


@pytest.mark.parametrize(
    "key,value",
    [("location_id", "other-city"), ("occurred_at", "other-date"), ("stance", "contradicts")],
)
def test_conflicting_context_blocks_automatic_grouping(key, value):
    result = compare(
        item(author="user", occurred_at="first-date"),
        item(author="user", occurred_at="first-date", **{key: value})
        if key != "occurred_at"
        else item(author="user", occurred_at=value),
    )
    assert result["classification"] != "duplicate" and result["signals"]["conflicting_context"]


def test_embedding_similarity_alone_is_only_a_review_signal():
    result = compare(
        item(vector=[1.0, 0.0], quote="First account"),
        item(vector=[1.0, 0.0], quote="Independent account", normalized_claim="another summary"),
    )
    assert result["classification"] == "suspected" and result["signals"]["embedding_cosine"] == 1


@pytest.mark.parametrize(
    "value", [None, "", "anonymous", "[deleted]", "u/[deleted]", "unknown", {}]
)
def test_placeholder_authors_are_unknown(value):
    assert identity(value) is None


def test_complete_link_partition_does_not_join_conflicting_places_through_unknown_location():
    snapshots = sorted(
        [
            item(author="u", location_id="A"),
            item(author="u", location_id=None),
            item(author="u", location_id="B"),
        ],
        key=lambda row: row["claim_id"],
    )
    _, groups = _assess(snapshots)
    assert len(groups) == 1 and len(groups[0]) == 2
    assert not {"A", "B"} <= {row["location_id"] for row in groups[0]}


def test_normalization_retains_negation_numbers_and_punctuation():
    assert normalize("  Ｔaxi   FARE ") == "taxi fare"
    assert normalize("not charged 100") != normalize("charged 200")


def test_short_identical_anonymous_reports_are_not_automatically_cross_source_duplicates():
    result = compare(
        item(quote="Taxi overcharged", content_hash="same"),
        item(quote="Taxi overcharged", content_hash="same"),
    )
    assert result["classification"] == "suspected"


def test_different_named_actors_block_automatic_grouping():
    result = compare(
        item(document_id="same", entities=["business a"]),
        item(document_id="same", entities=["business b"]),
    )
    assert result["classification"] == "suspected" and result["signals"]["conflicting_context"]


def test_short_repeated_generic_wording_on_same_page_needs_an_exact_span():
    left = item(document_id="same", clean_hash="same", quote="Meter refused")
    right = item(document_id="same", clean_hash="same", quote="Meter refused")
    assert compare(left, right)["classification"] == "suspected"
    left["quote_span"] = right["quote_span"] = [10, 23]
    assert compare(left, right)["classification"] == "duplicate"
