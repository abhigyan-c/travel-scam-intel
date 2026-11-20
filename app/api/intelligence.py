"""Thin public REST adapters for the shared intelligence and research services."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from app.on_demand import request_location_research
from app.schemas.intelligence import (
    EvidencePage,
    HypothesisCard,
    HypothesisPage,
    IncidentMatches,
    LocationSummary,
    RelatedPage,
)
from app.schemas.public_api import (
    ErrorEnvelope,
    HypothesisQuery,
    IncidentMatchRequest,
    LocationQuery,
    PageQuery,
    RecentQuery,
    SummaryQuery,
)
from app.services.intelligence import (
    IntelligenceInputError,
    IntelligenceNotFound,
    IntelligenceService,
)

router = APIRouter(
    prefix="/v1",
    tags=["intelligence"],
    responses={
        404: {
            "model": ErrorEnvelope,
            "description": "Location or publicly visible hypothesis not found.",
        },
        422: {
            "model": ErrorEnvelope,
            "description": "Invalid request; submitted values are not reflected.",
        },
        503: {
            "model": ErrorEnvelope,
            "description": "Intelligence storage temporarily unavailable.",
        },
        500: {
            "model": ErrorEnvelope,
            "description": "Unexpected error; use the request ID for diagnosis.",
        },
    },
)


async def service(request: Request, response: Response) -> AsyncIterator[IntelligenceService]:
    response.headers["Cache-Control"] = "no-store"
    try:
        yield IntelligenceService(
            request.app.state.db_sessions,
            embedder=request.app.state.embedder,
        )
    except IntelligenceNotFound as exc:
        message = (
            "Location not found" if str(exc) == "location_not_found" else "Hypothesis not found"
        )
        raise HTTPException(404, message) from None
    except IntelligenceInputError:
        raise HTTPException(422, "Invalid intelligence query") from None
    except SQLAlchemyError:
        raise HTTPException(503, "Intelligence storage is temporarily unavailable") from None


Service = Annotated[IntelligenceService, Depends(service)]


async def research_if_needed(request, location_id, enabled):
    if enabled:
        try:
            await request_location_research(
                {
                    "sessions": request.app.state.db_sessions,
                    "redis": request.app.state.report_queue,
                    "settings": request.app.state.settings,
                },
                location_id,
            )
        except ValueError:
            raise IntelligenceNotFound("location_not_found") from None


@router.get(
    "/locations/{location_id}/hypotheses",
    response_model=HypothesisPage,
    summary="Read public location patterns",
    description="Returns available patterns with actual geographic scope and research state. "
    "Research is queued when needed unless research=false; no provider calls run inline.",
)
async def location_hypotheses(
    request: Request,
    location_id: UUID,
    intelligence: Service,
    filters: Annotated[LocationQuery, Query()],
):
    await research_if_needed(request, location_id, filters.research)
    return await intelligence.search_location_scams(
        location_id,
        **filters.model_dump(exclude={"research"}),
    )


@router.post(
    "/hypotheses/match",
    response_model=IncidentMatches,
    summary="Compare an incident with public patterns",
    description="Local semantic comparison; incident text is not stored or echoed. "
    "No research is triggered. Model failure returns status=unavailable, not no_match.",
)
async def match_incident(payload: IncidentMatchRequest, intelligence: Service):
    return await intelligence.match_incident(**payload.model_dump())


@router.get(
    "/hypotheses/{hypothesis_id}",
    response_model=HypothesisCard,
    summary="Read one publicly visible hypothesis",
)
async def hypothesis(
    hypothesis_id: UUID,
    intelligence: Service,
    filters: Annotated[HypothesisQuery, Query()],
):
    return await intelligence.get_hypothesis(hypothesis_id, **filters.model_dump())


@router.get(
    "/hypotheses/{hypothesis_id}/evidence",
    response_model=EvidencePage,
    summary="Read public evidence and provenance",
    description="Bounded public excerpts and labeled anonymous provenance; "
    "no private report text or media.",
)
async def hypothesis_evidence(
    hypothesis_id: UUID,
    intelligence: Service,
    page: Annotated[PageQuery, Query()],
):
    return await intelligence.get_hypothesis_evidence(hypothesis_id, **page.model_dump())


@router.get(
    "/hypotheses/{hypothesis_id}/related",
    response_model=RelatedPage,
    summary="Read visible related patterns",
    description="Preserves relation direction and actual location.",
)
async def related_hypotheses(
    hypothesis_id: UUID,
    intelligence: Service,
    page: Annotated[PageQuery, Query()],
):
    return await intelligence.get_related_hypotheses(hypothesis_id, **page.model_dump())


@router.get(
    "/locations/{location_id}/recent",
    response_model=HypothesisPage,
    summary="Read recently observed patterns",
    description="Uses consistent occurrence dates, not publication dates. "
    "Same filters and research behavior as location search.",
)
async def recent_patterns(
    request: Request,
    location_id: UUID,
    intelligence: Service,
    filters: Annotated[RecentQuery, Query()],
):
    await research_if_needed(request, location_id, filters.research)
    return await intelligence.get_recent_patterns(
        location_id,
        **filters.model_dump(exclude={"research"}),
    )


@router.get(
    "/locations/{location_id}/summary",
    response_model=LocationSummary,
    summary="Read location counts and research status",
    description="Separate exact, inherited and descendant counts. "
    "Poll with research=false for a read-only status check.",
)
async def location_summary(
    request: Request,
    location_id: UUID,
    intelligence: Service,
    filters: Annotated[SummaryQuery, Query()],
):
    await research_if_needed(request, location_id, filters.research)
    return await intelligence.get_location_summary(location_id)
