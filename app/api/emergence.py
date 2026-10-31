"""Private emerging-candidate review; acknowledgements never publish labels."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.locations import Session
from app.api.report_media import require_admin
from app.db.emergence import decide_emergence, detect_emergence
from app.models import EmergingAssessment, EmergingReview, Hypothesis
from app.schemas.public_api import ErrorEnvelope
from app.services.public_evidence import current_hypothesis

router = APIRouter(
    prefix="/v1/admin",
    tags=["emerging-review"],
    dependencies=[Depends(require_admin)],
    responses={
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=1, strict=True)
    action: Literal["acknowledged", "dismissed"]
    reason: str = Field(min_length=1, max_length=500)


def _read(assessment, review):
    return {
        "hypothesis_id": assessment.hypothesis_id,
        "assessment_id": assessment.id,
        "version": assessment.version,
        "as_of": assessment.as_of,
        "candidate": assessment.candidate,
        "signals": assessment.signals,
        "review": {
            "revision": review.revision,
            "status": review.status,
            "assessment_id": review.assessment_id,
        }
        if review
        else None,
    }


@router.post(
    "/hypotheses/{hypothesis_id}/emergence-assessments",
    summary="Assess an emerging candidate for private review",
    description="Bounded eligible-evidence and cached-vector assessment; "
    "no model calls or publication.",
)
async def assess(hypothesis_id: UUID, session: Session, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        async with session.begin():
            assessment, review = await detect_emergence(session, hypothesis_id)
    except LookupError:
        raise HTTPException(404, "Hypothesis not found") from None
    return _read(assessment, review)


@router.get("/emerging-patterns", summary="Read the private emerging-candidate queue")
async def queue(
    session: Session,
    response: Response,
    status: Literal[
        "pending", "acknowledged", "dismissed", "withdrawn", "needs_assessment"
    ] = "pending",
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
):
    response.headers["Cache-Control"] = "no-store"
    query = (
        select(EmergingReview, EmergingAssessment, current_hypothesis().label("currently_public"))
        .join(EmergingAssessment, EmergingReview.assessment_id == EmergingAssessment.id)
        .join(Hypothesis, Hypothesis.id == EmergingReview.hypothesis_id)
        .where(EmergingReview.status == status)
    )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.execute(
            query.order_by(EmergingReview.updated_at.desc(), EmergingReview.hypothesis_id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "items": [
            {
                **_read(row.EmergingAssessment, row.EmergingReview),
                "currently_public": row.currently_public,
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


@router.post(
    "/emerging-patterns/{hypothesis_id}/decision",
    summary="Acknowledge or dismiss a candidate",
    description="Revision-checked private audit decision. "
    "Does not change hypothesis visibility or confidence.",
)
async def decide(
    hypothesis_id: UUID, payload: ReviewDecision, session: Session, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    try:
        async with session.begin():
            review = await decide_emergence(session, hypothesis_id, **payload.model_dump())
            assessment = await session.get(EmergingAssessment, review.assessment_id)
    except LookupError:
        raise HTTPException(404, "Review not found") from None
    except ValueError:
        raise HTTPException(
            409, "Review or public support changed; rescan and reread before deciding"
        ) from None
    return _read(assessment, review)
