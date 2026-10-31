"""Read-only public projections reuse the versioned pure evidence/scoring policies."""

from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import and_, func, or_, select

from app.confidence import score_confidence
from app.db.duplicates import _assess, _url, _vector
from app.db.independence import _references
from app.discovery import usable_url
from app.duplicates import identity, normalize
from app.evidence_weight import grade_evidence
from app.frequency import calculate_frequency
from app.independence import score_evidence
from app.models import Claim, Document, Evidence, Hypothesis, ReportAttachment, Source, UserReport
from app.reports import redact_pii
from app.schemas.intelligence import PublicMetrics

MAX_COHORT = 200


def public_text(value, limit):
    cleaned, flags = redact_pii(str(value))
    return ("[Content withheld]" if "threat_language" in flags else cleaned)[:limit]


def public_url(value):
    value = _url(value)
    if not value or not usable_url(value) or redact_pii(value)[1]:
        return None
    parts = urlsplit(value)
    if parts.query:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def eligible(*, include_archived=False):
    held_media = (
        select(ReportAttachment.id)
        .where(ReportAttachment.report_id == UserReport.id, ReportAttachment.status != "accepted")
        .correlate(UserReport)
        .exists()
    )
    metadata = Claim.structured_data
    return and_(
        True if include_archived else Evidence.archived_at.is_(None),
        Claim.location_id == Hypothesis.primary_location_id,
        metadata["publication_status"].astext.in_(["unreviewed", "approved"]),
        func.jsonb_typeof(metadata["sensitive"]) == "boolean",
        metadata["sensitive"].astext == "false",
        metadata["entities"] == [],
        Document.processing_status == "processed",
        Document.language == "en",
        Document.clean_text_hash
        == func.encode(func.sha256(func.convert_to(Document.clean_text, "UTF8")), "hex"),
        metadata["extraction"]["clean_text_hash"].astext == Document.clean_text_hash,
        func.jsonb_typeof(metadata["evidence_quote"]) == "string",
        func.length(metadata["evidence_quote"].astext).between(1, 3000),
        func.strpos(Document.clean_text, metadata["evidence_quote"].astext) > 0,
        or_(
            and_(UserReport.id.is_(None), Source.source_type != "user_report"),
            and_(
                UserReport.id.is_not(None),
                UserReport.moderation_status.in_(["processed", "accepted"]),
                ~held_media,
            ),
        ),
    )


def join_evidence(query):
    return (
        query.select_from(Evidence)
        .join(Claim, Evidence.claim_id == Claim.id)
        .join(Document, Claim.document_id == Document.id)
        .join(Source, Document.source_id == Source.id)
        .outerjoin(UserReport, UserReport.document_id == Document.id)
    )


def support_exists():
    return (
        join_evidence(select(Evidence.id))
        .where(Evidence.hypothesis_id == Hypothesis.id, Evidence.stance == "supports", eligible())
        .correlate(Hypothesis)
        .exists()
    )


def current_hypothesis():
    return and_(
        Hypothesis.status.in_(["candidate", "active", "disputed"]),
        Hypothesis.merged_into_id.is_(None),
        support_exists(),
    )


def project_metrics(rows, as_of):
    """Only eligible rows participate: private records cannot alter public credit/metrics."""
    duplicate_inputs, independent_inputs, weights, frequency_inputs = [], [], {}, []
    limitations = set()
    for row in rows:
        e, c, d, s, report, group = (
            row.Evidence,
            row.Claim,
            row.Document,
            row.Source,
            row.UserReport,
            row.DuplicateGroup,
        )
        data = c.structured_data
        if (
            not isinstance(s.source_metadata, dict)
            or (group is not None and not isinstance(group.detection_metadata, dict))
            or len(c.claim_text) > 4000
        ):
            raise ValueError("invalid_public_evidence_metadata")
        for field in ("location", "occurred_at"):
            value = data.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 200):
                raise ValueError("invalid_public_evidence_context")
        cross_post = s.source_metadata.get("cross_post_of")
        if cross_post is not None and not isinstance(cross_post, str):
            raise ValueError("invalid_public_evidence_reference")
        published = None if report else d.published_at or s.published_at
        published_iso = published.isoformat() if published else None
        author = None if report else identity(s.author_identifier)
        platform = "user_report" if report else normalize(s.platform)
        quote = data["evidence_quote"]
        duplicate_inputs.append(
            {
                "evidence_id": str(e.id),
                "claim_id": str(c.id),
                "document_id": str(d.id),
                "source_id": str(s.id),
                "normalized_claim": normalize(c.claim_text),
                "entities": None,
                "raw_location": normalize(data.get("location")) or None,
                "occurrence_text": normalize(data.get("occurred_at")) or None,
                "quote": quote,
                "quote_span": [row.quote_position, row.quote_position + len(quote)],
                "location_id": str(c.location_id),
                "occurred_at": c.occurred_at.isoformat() if c.occurred_at else None,
                "stance": e.stance.value,
                "content_hash": d.content_hash,
                "clean_hash": d.clean_text_hash,
                "url": _url(s.canonical_url),
                "platform": platform,
                "author": author,
                "thread": identity(s.source_metadata.get("thread_id")) if not report else None,
                "cross_post_of": _url(s.source_metadata.get("cross_post_of"))
                if not report
                else None,
                "vector": _vector(c),
            }
        )
        try:
            references, invalid = _references(
                s.source_metadata.get("reference_urls") if not report else None
            )
        except ValueError:
            references, invalid = [], 1
        if invalid:
            limitations.add("reference_metadata_incomplete")
        prior = duplicate_inputs[-1]
        manual = group is not None and group.detection_metadata.get("detector") != "claims-copy"
        if manual:
            limitations.add("manual_group_preserved")
        independent_inputs.append(
            {
                "evidence_id": str(e.id),
                "claim_id": str(c.id),
                "source_id": str(s.id),
                "group_id": str(group.id) if manual else None,
                "author": author,
                "platform": platform,
                "thread": prior["thread"],
                "urls": sorted({url for url in (prior["url"], _url(row.document_url)) if url}),
                "reference_urls": references,
                "invalid_reference_count": invalid,
                "cross_post_of": prior["cross_post_of"],
                "published_at": published_iso,
                "publication_basis": "document"
                if d.published_at and not report
                else "source"
                if published
                else "unknown",
                "created_at": c.created_at.isoformat(),
            }
        )
        first = data.get("first_hand")
        weights[str(e.id)] = {
            "source_type": s.source_type.value,
            "report_id": str(report.id) if report else None,
            "report_status": report.moderation_status if report else None,
            "provenance": report.provenance_class.value
            if report
            else "FIRST_HAND"
            if first is True
            else "SECOND_HAND"
            if first is False
            else "UNKNOWN",
            "known_author": bool(author),
            "published_at": published_iso,
            "official_verified": s.source_metadata.get("official_advisory_verified") is True,
            "publication_status": data["publication_status"],
            "sensitive": False,
            "invalid_sensitivity": False,
            "named_actors": False,
            "stance": e.stance.value,
        }
    pairs, groups = _assess(duplicate_inputs)
    indexed = {item["evidence_id"]: item for item in independent_inputs}
    for group in groups:
        for member in group:
            item = indexed[member["evidence_id"]]
            if item["group_id"] is None:
                item["group_id"] = "public-copy:" + group[0]["evidence_id"]
    if any(pair["classification"] == "suspected" for pair in pairs.values()):
        limitations.add("possible_correlated_reporting")
    independence = score_evidence(independent_inputs, pairs)
    grades, confidence_inputs = {}, []
    for row in rows:
        e, c, d, s, report = row.Evidence, row.Claim, row.Document, row.Source, row.UserReport
        key = str(e.id)
        grade = grade_evidence({**weights[key], "independence": independence[key]["score"]})
        grade["discounted"] = (
            independence[key]["score"] < independence[key]["components"]["base_score"]
        )
        grades[e.id] = grade
        confidence_inputs.append(
            {
                "evidence_id": key,
                "weight": grade["weight"],
                "stance": e.stance.value,
                "semantic_match_score": e.semantic_match_score,
                "location_agreement": "exact",
                "source_type": grade["components"]["effective_source_type"],
                "platform": indexed[key]["platform"],
            }
        )
        occurrence = report.occurred_at if report and report.occurred_at else c.occurred_at
        reporting = report.created_at if report else d.published_at or s.published_at
        frequency_inputs.append(
            {
                "evidence_id": key,
                "claim_id": str(c.id),
                "document_id": str(d.id),
                "report_key": f"report:{report.id}" if report else f"source:{s.id}",
                "eligible": True,
                "stance": e.stance.value,
                "location_agreement": "exact",
                "independence": independence[key]["score"],
                "occurred_at": occurrence.isoformat() if occurrence else None,
                "occurrence_basis": "report" if report and report.occurred_at else "claim",
                "reported_at": reporting.isoformat() if reporting else None,
                "reporting_basis": "submission" if report else indexed[key]["publication_basis"],
            }
        )
    confidence = score_confidence(
        confidence_inputs, complete=not limitations, review_reasons=sorted(limitations)
    )
    frequency = calculate_frequency(frequency_inputs, as_of=as_of)
    limitations.update(confidence["components"]["review_reasons"])
    limitations.update(frequency["date_review_reasons"])
    return PublicMetrics(
        status="current",
        as_of=as_of,
        confidence_score=confidence["score"],
        confidence_label=confidence["components"]["label"],
        support_weight=confidence["components"]["support_weight"],
        contradiction_weight=confidence["components"]["contradiction_weight"],
        recency_score=frequency["recency_score"],
        prevalence_score=frequency["prevalence_score"],
        all_time=frequency["all_time"],
        occurrence_windows=frequency["occurrence_windows"],
        reporting_windows=frequency["reporting_windows"],
        dated_reports=frequency["date_coverage"]["occurrence_dated_reports"],
        undated_or_invalid_reports=frequency["date_coverage"][
            "occurrence_undated_or_invalid_reports"
        ],
        trend=frequency["trend"]["classification"],
        complete=not limitations,
        limitations=sorted(limitations),
    ), grades
