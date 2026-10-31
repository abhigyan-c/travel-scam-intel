"""Bounded detection and a durable private review queue; never a publication action."""

from datetime import UTC, datetime

from sqlalchemy import String, case, cast, func, select

from app.db.duplicates import _hash, _vector
from app.embeddings import text_hash
from app.emergence import POLICY, VERSION, assess_pattern
from app.frequency import utc
from app.models import (
    Claim,
    EmergingAssessment,
    EmergingReview,
    EmergingReviewDecision,
    Evidence,
    Hypothesis,
    Location,
    Source,
    UserReport,
)
from app.services.intelligence import _Query
from app.services.public_evidence import current_hypothesis, eligible, join_evidence


async def _comparisons(session, query, hypothesis, rows, *, limit=100):
    first_seen = (
        join_evidence(
            select(
                func.min(
                    case(
                        (UserReport.id.is_not(None), UserReport.created_at), else_=Claim.created_at
                    )
                )
            )
        )
        .where(
            Evidence.hypothesis_id == Hypothesis.id,
            Evidence.stance == "supports",
            eligible(include_archived=True),
        )
        .correlate(Hypothesis)
        .scalar_subquery()
    )
    targets = (
        await session.execute(
            select(Hypothesis.id, Hypothesis.primary_location_id, first_seen.label("first_seen"))
            .where(Hypothesis.id != hypothesis.id, current_hypothesis())
            .order_by(Hypothesis.id)
            .limit(limit + 1)
        )
    ).all()
    if not targets:
        return [], True
    complete = len(targets) <= limit
    support = next((row for row in rows if row.Evidence.stance == "supports"), None)
    local = _vector(support.Claim) if support is not None else None
    if local is None:
        return [], False
    matches = []
    local_location = await session.get(Location, hypothesis.primary_location_id)
    for target in targets[:limit]:
        candidates = (
            await session.scalars(
                join_evidence(select(Claim))
                .join(Hypothesis, Hypothesis.id == Evidence.hypothesis_id)
                .where(
                    Evidence.hypothesis_id == target.id, Evidence.stance == "supports", eligible()
                )
                .order_by(Claim.id)
                .limit(1)
            )
        ).all()
        vector = _vector(candidates[0]) if candidates else None
        if vector is None or target.first_seen is None or target.first_seen > query.as_of:
            complete = False
            continue
        similarity = round(
            max(0.0, min(1.0, sum(a * b for a, b in zip(local, vector, strict=True)))), 6
        )
        if similarity < POLICY["similarity_threshold"]:
            continue
        other = await session.get(Location, target.primary_location_id)
        matches.append(
            {
                "hypothesis_id": str(target.id),
                "location_id": str(target.primary_location_id),
                "similarity": similarity,
                "first_observed_at": target.first_seen,
                "scope": await query.relation(
                    target.primary_location_id, hypothesis.primary_location_id
                ),
                "provisional": local_location.is_provisional or other.is_provisional,
            }
        )
    # The strongest five are review hints, not a count or exhaustive nearest-neighbor claim.
    if len(matches) > 5:
        complete = False
    return sorted(matches, key=lambda row: (-row["similarity"], row["hypothesis_id"]))[:5], complete


async def detect_emergence(session, hypothesis_id, *, as_of=None):
    """Caller commits; hypothesis lock serializes assessments and review revisions."""
    reference = utc(as_of) if as_of is not None else datetime.now(UTC)
    hypothesis = await session.scalar(
        select(Hypothesis).where(Hypothesis.id == hypothesis_id).with_for_update()
    )
    if hypothesis is None:
        raise LookupError("hypothesis_not_found")
    visible = await session.scalar(
        select(Hypothesis.id).where(Hypothesis.id == hypothesis_id, current_hypothesis())
    )
    query = _Query(session, reference)
    inputs = []
    if visible is None:
        signals = {
            "candidate": False,
            "reasons": [],
            "limitations": ["no_current_public_support"],
            "complete": False,
            "publication": "private review only; no public label or confidence change",
        }
    else:
        rows, metrics, grades = await query.cohort(hypothesis_id)
        if metrics.status != "current":
            signals = {
                "candidate": None,
                "reasons": [],
                "complete": False,
                "limitations": metrics.limitations,
                "publication": "private review only",
            }
        else:
            report_key = case(
                (UserReport.id.is_not(None), "r:" + cast(UserReport.id, String)),
                else_="s:" + cast(Source.id, String),
            )
            history = (
                await session.execute(
                    join_evidence(
                        select(
                            report_key.label("key"),
                            func.min(
                                case(
                                    (UserReport.id.is_not(None), UserReport.created_at),
                                    else_=Claim.created_at,
                                )
                            ).label("first_seen"),
                        )
                    )
                    .join(Hypothesis, Hypothesis.id == Evidence.hypothesis_id)
                    .where(
                        Evidence.hypothesis_id == hypothesis_id,
                        Evidence.stance == "supports",
                        eligible(include_archived=True),
                    )
                    .where(
                        report_key.in_(
                            [
                                f"r:{row.UserReport.id}" if row.UserReport else f"s:{row.Source.id}"
                                for row in rows
                            ]
                        )
                    )
                    .group_by(report_key)
                )
            ).all()
            # Historical links prevent edited source versions from looking newly observed.
            earliest = {row.key: row.first_seen for row in history}
            first_observed = await session.scalar(
                join_evidence(
                    select(
                        func.min(
                            case(
                                (UserReport.id.is_not(None), UserReport.created_at),
                                else_=Claim.created_at,
                            )
                        )
                    )
                )
                .join(Hypothesis, Hypothesis.id == Evidence.hypothesis_id)
                .where(
                    Evidence.hypothesis_id == hypothesis_id,
                    Evidence.stance == "supports",
                    eligible(include_archived=True),
                )
            )
            reports = {}
            for row in rows:
                grade = grades[row.Evidence.id]
                inputs.append(
                    [
                        str(row.Evidence.id),
                        str(row.Source.id),
                        str(row.UserReport.id) if row.UserReport else None,
                        row.Evidence.stance.value,
                        text_hash(row.Claim.claim_text),
                        text_hash(row.Claim.structured_data["evidence_quote"]),
                        row.Document.clean_text_hash,
                        grade["weight"],
                        grade["source_quality"],
                        grade["components"]["independence_score"],
                    ]
                )
                if row.Evidence.stance != "supports":
                    continue
                report = row.UserReport
                key = f"r:{report.id}" if report else f"s:{row.Source.id}"
                observed = earliest.get(key, report.created_at if report else row.Claim.created_at)
                credit = grade["components"]["independence_score"]
                if key in reports:
                    reports[key]["first_seen"] = min(reports[key]["first_seen"], observed)
                    reports[key]["credit"] = max(reports[key]["credit"], credit)
                else:
                    reports[key] = {"first_seen": observed, "credit": credit}
            comparisons, coverage = await _comparisons(session, query, hypothesis, rows)
            signals = assess_pattern(
                list(reports.values()),
                as_of=reference,
                confidence={"score": metrics.confidence_score, "label": metrics.confidence_label},
                comparisons=comparisons,
                similarity_complete=coverage,
                limitations=metrics.limitations,
                first_observed_at=first_observed,
            )
    location = await session.get(Location, hypothesis.primary_location_id)
    signals["location"] = {"id": str(location.id), "provisional": location.is_provisional}
    if location.is_provisional:
        signals["limitations"] = sorted(set(signals["limitations"]) | {"provisional_location"})
        signals["complete"] = False
    signals["input_fingerprint"] = _hash(inputs)
    material = _hash(signals)
    fingerprint = _hash(
        {
            "material": material,
            "reference": reference.isoformat()
            if as_of is not None
            else reference.date().isoformat(),
        }
    )
    assessment = await session.scalar(
        select(EmergingAssessment).where(
            EmergingAssessment.hypothesis_id == hypothesis_id,
            EmergingAssessment.version == VERSION,
            EmergingAssessment.input_hash == fingerprint,
        )
    )
    if assessment is None:
        assessment = EmergingAssessment(
            hypothesis_id=hypothesis_id,
            version=VERSION,
            input_hash=fingerprint,
            material_hash=material,
            as_of=reference,
            candidate=signals["candidate"],
            signals=signals,
        )
        session.add(assessment)
        await session.flush()
    review = await session.get(EmergingReview, hypothesis_id)
    old = await session.get(EmergingAssessment, review.assessment_id) if review else None
    if review is None and assessment.candidate is True:
        review = EmergingReview(
            hypothesis_id=hypothesis_id, assessment_id=assessment.id, revision=1, status="pending"
        )
        session.add(review)
    elif review is not None and old.material_hash != material and reference >= old.as_of:
        # Clock-only reassessments with unchanged material signals preserve decisions.
        review.assessment_id, review.revision = assessment.id, review.revision + 1
        review.status = (
            "pending"
            if assessment.candidate is True
            else "withdrawn"
            if assessment.candidate is False
            else "needs_assessment"
        )
    elif review is not None and old.material_hash == material and reference >= old.as_of:
        # Advance the reference without reopening a materially identical operator decision.
        review.assessment_id = assessment.id
    await session.flush()
    return assessment, review


async def decide_emergence(session, hypothesis_id, *, revision, action, reason):
    if action not in ("acknowledged", "dismissed") or not reason.strip() or len(reason) > 500:
        raise ValueError("invalid_review_decision")
    await session.scalar(
        select(Hypothesis.id).where(Hypothesis.id == hypothesis_id).with_for_update()
    )
    review = await session.get(EmergingReview, hypothesis_id, populate_existing=True)
    if review is None:
        raise LookupError("review_not_found")
    if review.revision != revision or review.status != "pending":
        raise ValueError("review_changed")
    visible = await session.scalar(
        select(Hypothesis.id).where(Hypothesis.id == hypothesis_id, current_hypothesis())
    )
    if visible is None:
        raise ValueError("public_support_changed_rescan_required")
    review.status, review.revision = action, review.revision + 1
    session.add(
        EmergingReviewDecision(
            hypothesis_id=hypothesis_id,
            assessment_id=review.assessment_id,
            revision=review.revision,
            action=action,
            reason=reason,
        )
    )
    await session.flush()
    return review
