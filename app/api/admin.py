"""Private operator console. No changes to the public MCP surface."""

import hashlib
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import String, func, select, union_all
from sqlalchemy.exc import DBAPIError

from app.api.locations import Session
from app.api.report_media import require_admin
from app.db.jobs import dispatch_pending
from app.explorer import ui_html
from app.models import (
    AdminAction,
    Claim,
    Document,
    Evidence,
    Hypothesis,
    HypothesisLifecycleEvent,
    HypothesisResolution,
    Location,
    LocationResearchState,
    ReportModerationEvent,
    ResearchJob,
    ResearchRun,
    Source,
    UserReport,
)
from app.schemas.admin import AdminCommand
from app.services.admin import AdminConflict, AdminNotFound, command
from app.services.public_evidence import public_url

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])
page_router = APIRouter()
Kind = Literal["hypotheses", "resolutions", "reports", "jobs", "locations", "documents"]
MODELS = dict(
    hypotheses=Hypothesis,
    resolutions=HypothesisResolution,
    reports=UserReport,
    jobs=ResearchJob,
    locations=Location,
    documents=Document,
)


@router.get("/operations")
async def operational_snapshot(request: Request, session: Session, response: Response):
    from app.services.operations import operations

    response.headers["Cache-Control"] = "no-store"
    return await operations(session, request.app.state.telemetry)


@page_router.get("/admin", include_in_schema=False)
async def dashboard():
    # An empty login shell only. All private reads and writes require Authorization.
    html = ui_html("admin")
    import base64

    script = html.split('<script type="module">', 1)[1].split("</script>", 1)[0]
    style = html.split("<style>", 1)[1].split("</style>", 1)[0]

    def digest(value):
        return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()

    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; "
            f"script-src 'sha256-{digest(script)}'; style-src 'sha256-{digest(style)}'; "
            "connect-src 'self'; img-src blob:; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        },
    )


@router.get("/overview")
async def overview(session: Session, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {
        "hypotheses": dict(
            (
                await session.execute(
                    select(Hypothesis.status, func.count()).group_by(Hypothesis.status)
                )
            ).all()
        ),
        "jobs": dict(
            (
                await session.execute(
                    select(ResearchJob.status, func.count()).group_by(ResearchJob.status)
                )
            ).all()
        ),
        "reports": dict(
            (
                await session.execute(
                    select(UserReport.moderation_status, func.count()).group_by(
                        UserReport.moderation_status
                    )
                )
            ).all()
        ),
    }


def brief(row, kind):
    common = {"id": row.id}
    if kind == "hypotheses":
        return {
            **common,
            "title": row.title[:250],
            "status": row.status,
            "location_id": row.primary_location_id,
            "updated_at": row.updated_at,
            "merged_into_id": row.merged_into_id,
        }
    if kind == "resolutions":
        return {
            **common,
            "claim_id": row.claim_id,
            "decision": row.decision,
            "target_id": row.target_id,
            "needs_review": row.needs_review,
            "error": row.error,
            "created_at": row.created_at,
        }
    if kind == "reports":
        return {
            **common,
            "status": row.processing_status,
            "moderation_status": row.moderation_status,
            "moderation_revision": row.moderation_revision,
            "location_id": row.location_id,
            "created_at": row.created_at,
        }
    if kind == "jobs":
        return {
            **common,
            "kind": row.kind,
            "status": row.status,
            "attempts": row.attempts,
            "error_code": row.error_code,
            "report_run_id": row.report_run_id,
            "updated_at": row.updated_at,
            "references": {
                k: v
                for k, v in row.payload.items()
                if k in {"phase", "location_id", "hypothesis_id", "document_id", "report_id"}
            },
        }
    if kind == "documents":
        return {
            **common,
            "title": (row.title or "Untitled")[:250],
            "status": row.processing_status,
            "language": row.language,
            "updated_at": row.updated_at,
            "url": public_url(row.url),
        }
    return {
        **common,
        "name": row.name,
        "type": row.type,
        "country_code": row.country_code,
        "provisional": row.is_provisional,
    }


@router.get("/items/{kind}")
async def items(
    kind: Kind,
    session: Session,
    response: Response,
    status: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    response.headers["Cache-Control"] = "no-store"
    model = MODELS[kind]
    query = select(model)
    if kind == "resolutions":
        applied = (
            select(HypothesisLifecycleEvent.id)
            .where(HypothesisLifecycleEvent.resolution_id == HypothesisResolution.id)
            .exists()
        )
        if status != "all":
            query = query.where(HypothesisResolution.needs_review, ~applied)
    elif status:
        field = (
            UserReport.moderation_status if kind == "reports" else getattr(model, "status", None)
        )
        if field is None:
            field = getattr(model, "processing_status", None)
        if field is not None:
            # Compare as text so an unknown filter cannot leak DB enum errors.
            query = query.where(field.cast(String) == status)
    if q:
        field = (
            Location.name
            if kind == "locations"
            else Hypothesis.title
            if kind == "hypotheses"
            else None
        )
        if field is not None:
            query = query.where(
                field.ilike(
                    "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
                    escape="\\",
                )
            )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(
        await session.scalars(
            query.order_by(model.created_at.desc(), model.id).limit(limit).offset(offset)
        )
    )
    return {
        "items": [brief(row, kind) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(rows) < total,
    }


@router.get("/items/{kind}/{identity}")
async def inspect_item(kind: Kind, identity: UUID, session: Session, response: Response):
    response.headers["Cache-Control"] = "no-store"
    model = MODELS[kind]
    row = await session.scalar(
        select(model).where(model.id == identity).execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Record not found")
    data = brief(row, kind)
    if kind == "hypotheses":
        data.update(
            description=row.canonical_description[:4000],
            category=row.category,
            confidence_score=row.confidence_score,
            embedding_metadata_present=row.embedding_input_hash is not None,
        )
    elif kind == "resolutions":
        claim = await session.get(Claim, row.claim_id)
        data.update(
            claim_text=claim.claim_text[:4000],
            location_id=claim.location_id,
            publication_status=claim.structured_data.get("publication_status"),
            sensitive=claim.structured_data.get("sensitive"),
            evidence_quote=str(claim.structured_data.get("evidence_quote", ""))[:3000],
            version=row.version,
            confidence=row.confidence,
            review_reasons=row.details.get("review_reasons", [])[:20],
            comparison_reason=str((row.details.get("decision") or {}).get("reason", ""))[:1000],
            candidates=[
                {
                    key: str(value)[:4000]
                    for key, value in candidate.items()
                    if key in {"id", "location_id", "title", "description", "category", "status"}
                }
                for candidate in row.details.get("context", {}).get("candidates", [])[:10]
            ],
        )
    elif kind == "reports":
        data.update(
            report_text=row.report_text[:8000],
            provenance_class=row.provenance_class,
            occurred_at=row.occurred_at,
            flags=row.processing_details.get("flags", []),
            text_complete=row.processing_details.get(
                "text_complete", row.processing_details.get("complete")
            )
            is True,
        )
    elif kind == "jobs":
        data.update(usage=row.result.get("usage", {}), complete=row.result.get("complete"))
    elif kind == "documents":
        data.update(
            clean_text=(row.clean_text or "")[:8000],
            clean_text_truncated=len(row.clean_text or "") > 8000,
            clean_text_hash=row.clean_text_hash,
        )
    elif kind == "locations":
        state = await session.get(LocationResearchState, identity)
        root = (
            await session.get(ResearchRun, state.current_run_id)
            if state and state.current_run_id
            else None
        )
        cursor = state.progress.get("continuation", {}) if state else {}
        data["research"] = {
            "status": state.status if state else "never_researched",
            "updated_at": state.updated_at if state else None,
            "priority_override": state.priority_override if state else 0,
            "active_job_id": state.active_job_id if state else None,
            "root_id": state.current_run_id if state else None,
            "error_code": state.error_code if state else None,
            "caps": cursor.get("caps"),
            "used": cursor.get("used"),
            "next_retry_at": state.next_retry_at if state else None,
        }
        data["root"] = (
            {
                "status": root.status,
                "coverage": root.coverage,
                "stop_reasons": root.stop_reasons,
                "finished_at": root.finished_at,
            }
            if root
            else None
        )
    return data


@router.get("/hypotheses/{identity}/evidence")
async def inspect_evidence(
    identity: UUID,
    session: Session,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    response.headers["Cache-Control"] = "no-store"
    query = (
        select(Evidence, Claim, Document, Source)
        .join(Claim, Evidence.claim_id == Claim.id)
        .join(Document, Claim.document_id == Document.id)
        .join(Source, Document.source_id == Source.id)
        .where(Evidence.hypothesis_id == identity)
    )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await session.execute(query.order_by(Evidence.id).limit(limit).offset(offset))).all()
    return {
        "items": [
            dict(
                id=e.id,
                claim_id=c.id,
                document_id=d.id,
                stance=e.stance,
                archived=e.archived_at is not None,
                superseded_by_id=e.superseded_by_id,
                claim_text=c.claim_text[:4000],
                evidence_quote=str(c.structured_data.get("evidence_quote", ""))[:3000],
                publication_status=c.structured_data.get("publication_status"),
                sensitive=c.structured_data.get("sensitive"),
                actual_location_id=c.location_id,
                source_type=s.source_type,
                source_url=public_url(s.canonical_url),
                occurred_at=c.occurred_at,
                source_quality=e.source_quality_score,
            )
            for e, c, d, s in rows
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(rows) < total,
    }


@router.post("/commands")
async def execute(
    payload: AdminCommand,
    session: Session,
    request: Request,
    response: Response,
    key: Annotated[
        str, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$")
    ],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        async with session.begin():
            event, replayed = await command(
                session, payload, key, embedder=request.app.state.embedder
            )
            receipt = {
                "action_id": event.id,
                "operation": event.operation,
                "result": event.result,
                "replayed": replayed,
            }
    except (AdminNotFound, LookupError):
        raise HTTPException(404, "Record not found") from None
    except (AdminConflict, ValueError, DBAPIError):
        raise HTTPException(
            409,
            "Record changed or action violates a domain safeguard. Reread before a new decision; "
            "retry an uncertain request with its original key.",
        ) from None
    pending = False
    if job_id := event.result.get("job_id"):
        try:
            await dispatch_pending(session, request.app.state.report_queue, job_ids=[UUID(job_id)])
        except Exception:
            pending = True
    return {**receipt, "queue_pending": pending}


@router.get("/audit")
async def audit(
    session: Session,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    response.headers["Cache-Control"] = "no-store"
    from sqlalchemy import literal

    from app.models import EmergingReviewDecision

    queries = []
    for model, name, op, subject, actor in [
        (AdminAction, "admin", AdminAction.operation, AdminAction.subject_id, AdminAction.actor),
        (
            HypothesisLifecycleEvent,
            "lifecycle",
            HypothesisLifecycleEvent.operation,
            HypothesisLifecycleEvent.target_id,
            HypothesisLifecycleEvent.actor,
        ),
        (
            ReportModerationEvent,
            "report",
            ReportModerationEvent.action,
            ReportModerationEvent.report_id,
            ReportModerationEvent.actor,
        ),
        (
            EmergingReviewDecision,
            "emergence",
            EmergingReviewDecision.action,
            EmergingReviewDecision.hypothesis_id,
            literal("admin-token"),
        ),
    ]:
        queries.append(
            select(
                model.id,
                literal(name).label("source"),
                op.label("operation"),
                subject.label("subject_id"),
                actor.label("actor"),
                model.reason,
                model.created_at,
            )
        )
    stream = union_all(*queries).subquery()
    total = await session.scalar(select(func.count()).select_from(stream))
    rows = (
        (
            await session.execute(
                select(stream)
                .order_by(stream.c.created_at.desc(), stream.c.source, stream.c.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }
