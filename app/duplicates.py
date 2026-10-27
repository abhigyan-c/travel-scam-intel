"""Deterministic textual-copy signals, not a test of incident truth or independence."""

import math
import re
import unicodedata
from collections import Counter

VERSION = "duplicates-v1"
POLICY = {"min_quote_words": 20, "quote_overlap_review": 0.8, "embedding_similarity_review": 0.95}


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def identity(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        return None
    value = value.strip()
    return (
        None
        if value.casefold()
        in {
            "anonymous",
            "unknown",
            "guest",
            "[deleted]",
            "deleted",
            "u/[deleted]",
            "[removed]",
            "n/a",
            "none",
            "null",
        }
        else value
    )


def _shingles(text):
    words = re.findall(r"\w+", normalize(text))
    return Counter(tuple(words[index : index + 5]) for index in range(len(words) - 4))


def compare(left: dict, right: dict) -> dict:
    quote_left, quote_right = normalize(left["quote"]), normalize(right["quote"])
    one, two = _shingles(quote_left), _shingles(quote_right)
    overlap = (
        sum((one & two).values()) / min(sum(one.values()), sum(two.values())) if one and two else 0
    )
    same_claim = (
        bool(left["normalized_claim"]) and left["normalized_claim"] == right["normalized_claim"]
    )
    same_platform = bool(left["platform"]) and left["platform"] == right["platform"]
    same_author = same_platform and bool(left["author"]) and left["author"] == right["author"]
    same_thread = same_platform and bool(left["thread"]) and left["thread"] == right["thread"]
    same_url = bool(left["url"]) and left["url"] == right["url"]
    same_raw = bool(left["content_hash"]) and left["content_hash"] == right["content_hash"]
    same_clean = bool(left["clean_hash"]) and left["clean_hash"] == right["clean_hash"]
    same_document = left["document_id"] == right["document_id"]
    same_span = (
        same_document
        and same_clean
        and bool(left.get("quote_span"))
        and left.get("quote_span") == right.get("quote_span")
    )
    exact_quote = bool(quote_left) and quote_left == quote_right
    substantial = (
        min(len(re.findall(r"\w+", quote_left)), len(re.findall(r"\w+", quote_right)))
        >= POLICY["min_quote_words"]
    )
    crosspost = bool(left["url"] and right["cross_post_of"] == left["url"]) or bool(
        right["url"] and left["cross_post_of"] == right["url"]
    )
    conflict = any(
        left.get(key) is not None and right.get(key) is not None and left[key] != right[key]
        for key in (
            "location_id",
            "occurred_at",
            "stance",
            "entities",
            "raw_location",
            "occurrence_text",
        )
    )
    cosine = None
    if left["vector"] is not None and right["vector"] is not None:
        value = sum(a * b for a, b in zip(left["vector"], right["vector"], strict=True))
        if math.isfinite(value):
            cosine = max(-1.0, min(1.0, value))
    confirmed = (
        same_claim
        and exact_quote
        and not conflict
        and (
            same_span
            or (
                substantial
                and (
                    same_document or same_url or same_raw or same_clean or same_author or crosspost
                )
            )
        )
    )
    possible = same_claim and (
        same_author
        or same_thread
        or same_document
        or same_url
        or same_raw
        or same_clean
        or crosspost
    )
    possible = (
        possible
        or (substantial and overlap >= POLICY["quote_overlap_review"])
        or (cosine is not None and cosine >= POLICY["embedding_similarity_review"])
    )
    return {
        "classification": "duplicate" if confirmed else "suspected" if possible else "no_match",
        "signals": {
            "same_normalized_claim": same_claim,
            "same_document": same_document,
            "same_quote_span": bool(same_span),
            "same_url": same_url,
            "same_raw_hash": same_raw,
            "same_clean_hash": same_clean,
            "same_platform_author": bool(same_author),
            "same_platform_thread": bool(same_thread),
            "exact_quote": exact_quote,
            "substantial_quote": substantial,
            "quote_overlap": round(overlap, 6),
            "embedding_cosine": cosine,
            "cross_post_reference": crosspost,
            "conflicting_context": conflict,
        },
    }
