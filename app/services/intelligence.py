"""Bounded, read-only public intelligence contracts for REST and MCP adapters."""

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import String, case, cast, func, or_, select, text
from sqlalchemy.orm import load_only

from app.db.resolution import _descendants, get_location_path
from app.embeddings import validate_vector
from app.frequency import utc
from app.models import (
    Claim,
    Document,
    DuplicateGroup,
    Evidence,
    Hypothesis,
    HypothesisRelation,
    LocationResearchState,
    Source,
    UserReport,
)
from app.reports import redact_pii
from app.schemas.intelligence import (
    EvidencePage,
    HypothesisCard,
    HypothesisPage,
    IncidentMatch,
    IncidentMatches,
    LocationRef,
    LocationSummary,
    PublicEvidence,
    PublicMetrics,
    RelatedCard,
    RelatedPage,
    ResearchState,
)
from app.services.public_evidence import (
    MAX_COHORT,
    current_hypothesis,
    eligible,
    join_evidence,
    project_metrics,
    public_text,
    public_url,
)

MAX_PAGE = 20
MAX_OFFSET = 1000
MAX_MATCH = 50


class IntelligenceInputError(ValueError):
    pass


class IntelligenceNotFound(LookupError):
    pass


def _page(limit, offset):
    if (
        type(limit) is not int
        or type(offset) is not int
        or not 1 <= limit <= MAX_PAGE
        or not 0 <= offset <= MAX_OFFSET
    ):
        raise IntelligenceInputError("invalid_pagination")


def _location(row):
    return LocationRef(
        id=row.id,
        name=public_text(row.name, 200),
        type=row.type.value,
        country_code=row.country_code,
        provisional=row.is_provisional,
    )


def _outcome(total, research):
    return (
        "findings"
        if total
        else "nothing_found"
        if research.status == "ready" and research.complete
        else "research_incomplete"
    )


class _Query:
    def __init__(self, session, as_of):
        self.session, self.as_of = session, as_of
        self.paths, self.states, self.cohorts = {}, {}, {}

    async def path(self, identity):
        if identity not in self.paths:
            try:
                self.paths[identity] = await get_location_path(self.session, identity)
            except ValueError as exc:
                raise IntelligenceNotFound("location_not_found") from exc
        return self.paths[identity]

    async def research(self, identity):
        if identity not in self.states:
            row = await self.session.get(LocationResearchState, identity)
            if row is None:
                result = ResearchState(status="never_researched")
            else:
                stale = row.status == "stale" or bool(
                    row.fresh_until and row.fresh_until <= self.as_of
                )
                result = ResearchState(
                    status="stale" if row.status == "ready" and stale else row.status,
                    initial=row.initial_completed_at is not None,
                    enriched=row.enriched_at is not None,
                    in_progress=row.status == "researching",
                    stale=stale,
                    complete=row.progress.get("complete") is True,
                    last_success_at=row.last_success_at,
                    fresh_until=row.fresh_until,
                )
            self.states[identity] = result
        return self.states[identity]

    async def scope(self, identity, parents=True, descendants=True):
        path = await self.path(identity)
        conditions = [Hypothesis.primary_location_id == identity]
        if parents:
            conditions.append(Hypothesis.primary_location_id.in_([row.id for row in path]))
        if descendants:
            conditions.append(
                Hypothesis.primary_location_id.in_(select(_descendants(identity).c.id))
            )
        return or_(*conditions)

    async def relation(self, actual, requested):
        if actual == requested:
            return "exact"
        if actual in [row.id for row in await self.path(requested)]:
            return "ancestor"
        if requested in [row.id for row in await self.path(actual)]:
            return "descendant"
        return "other"

    def category(self):
        return (
            join_evidence(select(Claim.claim_type))
            .where(
                Evidence.hypothesis_id == Hypothesis.id, Evidence.stance == "supports", eligible()
            )
            .order_by(Claim.id)
            .limit(1)
            .correlate(Hypothesis)
            .scalar_subquery()
        )

    def summary(self):
        return (
            join_evidence(select(Claim.claim_text))
            .where(
                Evidence.hypothesis_id == Hypothesis.id, Evidence.stance == "supports", eligible()
            )
            .order_by(Claim.id)
            .limit(1)
            .correlate(Hypothesis)
            .scalar_subquery()
        )

    async def cohort(self, hypothesis_id):
        if hypothesis_id not in self.cohorts:
            query = join_evidence(
                select(
                    Evidence,
                    Claim,
                    Document,
                    Source,
                    UserReport,
                    DuplicateGroup,
                    Document.url.label("document_url"),
                    (
                        func.strpos(
                            Document.clean_text, Claim.structured_data["evidence_quote"].astext
                        )
                        - 1
                    ).label("quote_position"),
                )
            )
            query = (
                query.join(Hypothesis, Hypothesis.id == Evidence.hypothesis_id)
                .outerjoin(DuplicateGroup, Evidence.duplicate_group_id == DuplicateGroup.id)
                .where(Evidence.hypothesis_id == hypothesis_id, eligible())
            )
            query = query.options(
                load_only(
                    Document.id,
                    Document.content_hash,
                    Document.clean_text_hash,
                    Document.published_at,
                    Document.url,
                )
            )
            rows = (
                await self.session.execute(query.order_by(Claim.id).limit(MAX_COHORT + 1))
            ).all()
            if len(rows) > MAX_COHORT:
                metrics, grades = (
                    PublicMetrics(
                        status="unavailable",
                        as_of=self.as_of,
                        limitations=["public_evidence_limit"],
                    ),
                    {},
                )
            else:
                try:
                    metrics, grades = await asyncio.to_thread(project_metrics, rows, self.as_of)
                except (ValueError, TypeError, KeyError):
                    metrics, grades = (
                        PublicMetrics(
                            status="unavailable",
                            as_of=self.as_of,
                            limitations=["invalid_evidence_metadata"],
                        ),
                        {},
                    )
            self.cohorts[hypothesis_id] = rows, metrics, grades
        return self.cohorts[hypothesis_id]

    async def card(self, row, requested):
        # Canonical/internal labels can contain held associations; use current eligible support.
        summary = await self.session.scalar(
            select(self.summary()).select_from(Hypothesis).where(Hypothesis.id == row.id)
        )
        category = await self.session.scalar(
            select(self.category()).select_from(Hypothesis).where(Hypothesis.id == row.id)
        )
        _, metrics, _ = await self.cohort(row.id)
        if row.status == "candidate" or metrics.confidence_label in {
            "limited",
            "unknown",
            "no_support",
        }:
            disclaimer = (
                "Unverified candidate or limited evidence: use as a precaution, not as "
                "a verified incident, proof of prevalence or confirmed new scam. "
                "Pattern matching may be incomplete; similar reports may appear separately."
            )
            metrics = metrics.model_copy(
                update={"limitations": list(dict.fromkeys([*metrics.limitations, disclaimer]))}
            )
        path = await self.path(row.primary_location_id)
        return HypothesisCard(
            id=row.id,
            title=public_text(summary, 120),
            description=public_text(summary, 1000),
            category=public_text(category, 100),
            status=row.status.value,
            location=_location(path[-1]),
            location_path=[_location(node) for node in path],
            scope=await self.relation(row.primary_location_id, requested),
            research=await self.research(row.primary_location_id),
            metrics=metrics,
        )

    async def visible(self, identity):
        row = await self.session.scalar(
            select(Hypothesis).where(Hypothesis.id == identity, current_hypothesis())
        )
        if row is None:
            raise IntelligenceNotFound("hypothesis_not_found")
        return row

    def recent_ids(self, days):
        occurred = case(
            (UserReport.occurred_at.is_not(None), UserReport.occurred_at), else_=Claim.occurred_at
        )
        report_key = case(
            (UserReport.id.is_not(None), "r:" + cast(UserReport.id, String)),
            else_="s:" + cast(Source.id, String),
        )
        return (
            join_evidence(select(Evidence.hypothesis_id))
            .join(Hypothesis, Hypothesis.id == Evidence.hypothesis_id)
            .where(eligible(), Evidence.stance == "supports")
            .group_by(Evidence.hypothesis_id, report_key)
            .having(
                func.count(func.distinct(occurred)) == 1,
                func.min(occurred) > self.as_of - timedelta(days=days),
                func.max(occurred) <= self.as_of,
            )
        )

    async def page(
        self,
        identity,
        *,
        q=None,
        category=None,
        limit=20,
        offset=0,
        inherit_parents=True,
        include_descendants=True,
        recent_days=None,
        pattern_ids=None,
    ):
        _page(limit, offset)
        if type(inherit_parents) is not bool or type(include_descendants) is not bool:
            raise IntelligenceInputError("invalid_geographic_options")
        path = await self.path(identity)
        query = select(Hypothesis).where(
            current_hypothesis(), await self.scope(identity, inherit_parents, include_descendants)
        )
        if pattern_ids is not None:
            query = query.where(Hypothesis.id.in_(pattern_ids))
        if q is not None:
            if not isinstance(q, str) or not q.strip() or len(q) > 200:
                raise IntelligenceInputError("invalid_search_text")
            literal = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(self.summary().ilike(f"%{literal}%", escape="\\"))
        if category is not None:
            if not isinstance(category, str) or not category.strip() or len(category) > 100:
                raise IntelligenceInputError("invalid_category")
            query = query.where(self.category() == category)
        if recent_days is not None:
            if recent_days not in (30, 90, 365):
                raise IntelligenceInputError("invalid_recent_window")
            query = query.where(Hypothesis.id.in_(self.recent_ids(recent_days)))
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = (
            await self.session.scalars(
                query.order_by(
                    case((Hypothesis.primary_location_id == identity, 0), else_=1), Hypothesis.id
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
        state = await self.research(identity)
        return HypothesisPage(
            requested_location=_location(path[-1]),
            research=state,
            items=[await self.card(row, identity) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(rows) < total,
            outcome=_outcome(total, state),
        )

    def evidence(self, row, grades):
        e, c, s, report = row.Evidence, row.Claim, row.Source, row.UserReport
        grade = grades.get(e.id)
        provenance = (
            report.provenance_class.value
            if report
            else "FIRST_HAND"
            if c.structured_data.get("first_hand") is True
            else "SECOND_HAND"
            if c.structured_data.get("first_hand") is False
            else "UNKNOWN"
        )
        return PublicEvidence(
            id=e.id,
            stance=e.stance.value,
            source_type="user_report" if report else s.source_type.value,
            provenance_class=provenance,
            label="anonymous_traveler_report" if report else "public_web_source",
            platform=None
            if report
            else s.platform
            if re.fullmatch(r"[A-Za-z0-9.-]{1,200}", s.platform)
            else None,
            source_url=None if report else public_url(s.canonical_url),
            claim=None if report else public_text(c.claim_text, 400),
            excerpt=None if report else public_text(c.structured_data["evidence_quote"], 500),
            published_at=None if report else row.Document.published_at or s.published_at,
            occurred_at=report.occurred_at if report and report.occurred_at else c.occurred_at,
            source_quality=grade["source_quality"] if grade else None,
            independence_credit=grade["components"]["independence_score"] if grade else None,
            evidence_weight=grade["weight"] if grade else None,
            discounted=grade["discounted"] if grade else None,
        )


class IntelligenceService:
    """Each call owns one read-only repeatable-read transaction; no research is triggered."""

    def __init__(self, sessions, *, embedder=None):
        self.sessions, self.embedder = sessions, embedder

    @asynccontextmanager
    async def _read(self, as_of=None):
        reference = utc(as_of) if as_of is not None else datetime.now(UTC)
        async with self.sessions() as session, session.begin():
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            yield _Query(session, reference)

    async def search_location_scams(self, location_id, **options):
        as_of = options.pop("as_of", None)
        async with self._read(as_of) as query:
            return await query.page(location_id, **options)

    async def search_scams(self, location_id, q, **options):
        return await self.search_location_scams(location_id, q=q, **options)

    async def get_hypothesis(self, hypothesis_id, *, requested_location_id=None, as_of=None):
        async with self._read(as_of) as query:
            row = await query.visible(hypothesis_id)
            requested = requested_location_id or row.primary_location_id
            await query.path(requested)
            return await query.card(row, requested)

    async def get_hypothesis_evidence(self, hypothesis_id, *, limit=20, offset=0, as_of=None):
        _page(limit, offset)
        async with self._read(as_of) as query:
            row = await query.visible(hypothesis_id)
            cohort, metrics, grades = await query.cohort(hypothesis_id)
            base = (
                join_evidence(select(Evidence.id))
                .join(Hypothesis, Hypothesis.id == Evidence.hypothesis_id)
                .where(Evidence.hypothesis_id == hypothesis_id, eligible())
            )
            total = await query.session.scalar(select(func.count()).select_from(base.subquery()))
            if metrics.status == "current":
                rows = sorted(cohort, key=lambda record: record.Evidence.id)[
                    offset : offset + limit
                ]
            else:
                # Do not silently serve a truncated cohort as a complete evidence page.
                ids = (
                    await query.session.scalars(
                        base.order_by(Evidence.id).offset(offset).limit(limit)
                    )
                ).all()
                page_query = join_evidence(
                    select(Evidence, Claim, Document, Source, UserReport)
                ).where(Evidence.id.in_(ids))
                page_query = page_query.options(load_only(Document.id, Document.published_at))
                rows = (await query.session.execute(page_query.order_by(Evidence.id))).all()
            path = await query.path(row.primary_location_id)
            return EvidencePage(
                hypothesis_id=row.id,
                location=_location(path[-1]),
                research=await query.research(row.primary_location_id),
                items=[query.evidence(item, grades) for item in rows],
                total=total,
                limit=limit,
                offset=offset,
                has_more=offset + len(rows) < total,
                metrics_status=metrics.status,
            )

    async def get_recent_patterns(self, location_id, *, days=30, **options):
        return await self.search_location_scams(location_id, recent_days=days, **options)

    async def get_related_hypotheses(self, hypothesis_id, *, limit=20, offset=0, as_of=None):
        _page(limit, offset)
        async with self._read(as_of) as query:
            source = await query.visible(hypothesis_id)
            target_id = case(
                (
                    HypothesisRelation.source_hypothesis_id == hypothesis_id,
                    HypothesisRelation.target_hypothesis_id,
                ),
                else_=HypothesisRelation.source_hypothesis_id,
            )
            base = (
                select(HypothesisRelation, Hypothesis)
                .join(Hypothesis, Hypothesis.id == target_id)
                .where(
                    or_(
                        HypothesisRelation.source_hypothesis_id == hypothesis_id,
                        HypothesisRelation.target_hypothesis_id == hypothesis_id,
                    ),
                    current_hypothesis(),
                )
            )
            total = await query.session.scalar(select(func.count()).select_from(base.subquery()))
            rows = (
                await query.session.execute(
                    base.order_by(HypothesisRelation.id).offset(offset).limit(limit)
                )
            ).all()
            return RelatedPage(
                hypothesis_id=hypothesis_id,
                total=total,
                limit=limit,
                offset=offset,
                has_more=offset + len(rows) < total,
                items=[
                    RelatedCard(
                        relation=relation.relation_type.value,
                        direction="outgoing"
                        if relation.relation_type.value == "SUBTYPE_OF"
                        and relation.source_hypothesis_id == hypothesis_id
                        else "incoming"
                        if relation.relation_type.value == "SUBTYPE_OF"
                        else "symmetric",
                        hypothesis=await query.card(target, source.primary_location_id),
                    )
                    for relation, target in rows
                ],
            )

    async def get_location_summary(self, location_id, *, as_of=None):
        async with self._read(as_of) as query:
            path = await query.path(location_id)
            ancestor_ids = [row.id for row in path[:-1]]
            base = (
                select(
                    Hypothesis.primary_location_id.label("location"),
                    query.category().label("category"),
                )
                .where(current_hypothesis(), await query.scope(location_id))
                .subquery()
            )
            exact, inherited, descendants = (
                await query.session.execute(
                    select(
                        func.count().filter(base.c.location == location_id),
                        func.count().filter(base.c.location.in_(ancestor_ids)),
                        func.count().filter(
                            (base.c.location != location_id) & base.c.location.not_in(ancestor_ids)
                        ),
                    ).select_from(base)
                )
            ).one()
            categories = (
                await query.session.execute(
                    select(base.c.category, func.count().label("n"))
                    .group_by(base.c.category)
                    .order_by(func.count().desc(), base.c.category)
                    .limit(21)
                )
            ).all()
            result = {}
            for name, count in categories[:20]:
                safe = public_text(name, 100)
                result[safe] = result.get(safe, 0) + count
            state = await query.research(location_id)
            return LocationSummary(
                location=_location(path[-1]),
                research=state,
                exact_patterns=exact,
                inherited_patterns=inherited,
                descendant_patterns=descendants,
                total_patterns=exact + inherited + descendants,
                categories=result,
                categories_complete=len(categories) <= 20,
                outcome=_outcome(exact + inherited + descendants, state),
            )

    async def match_incident(
        self, location_id, incident, *, limit=5, min_similarity=0.7, as_of=None
    ):
        _page(limit, 0)
        if (
            not isinstance(incident, str)
            or not 20 <= len(incident.strip()) <= 2000
            or type(min_similarity) not in (float, int)
            or not 0 <= min_similarity <= 1
        ):
            raise IntelligenceInputError("invalid_incident_match")
        cleaned, flags = redact_pii(incident)
        async with self._read(as_of) as query:
            path = await query.path(location_id)
            state = await query.research(location_id)
            response = {"requested_location": _location(path[-1]), "research": state}
            tokens = re.findall(r"\w{3,}", cleaned)[:20]
            rank = func.ts_rank_cd(
                func.to_tsvector("simple", query.summary()),
                func.websearch_to_tsquery("simple", " OR ".join(tokens)),
            )
            rows = (
                await query.session.execute(
                    select(Hypothesis, query.summary().label("public_summary"))
                    .where(current_hypothesis(), await query.scope(location_id))
                    .order_by(rank.desc(), Hypothesis.id)
                    .limit(MAX_MATCH + 1)
                )
            ).all()
            complete = len(rows) <= MAX_MATCH
            limitations = ["input_redacted"] if flags else []
            if not complete:
                limitations.append("candidate_limit")
            if not rows:
                return IncidentMatches(
                    **response, status="no_match", complete=True, items=[], limitations=limitations
                )
            if self.embedder is None:
                return IncidentMatches(
                    **response,
                    status="unavailable",
                    complete=False,
                    items=[],
                    limitations=[*limitations, "embedding_unavailable"],
                )
            candidates = rows[:MAX_MATCH]
            try:
                async with asyncio.timeout(10):
                    await self.embedder.preload(download=False)
                    vectors = []
                    texts = [
                        cleaned,
                        *[public_text(row.public_summary, 1000) for row in candidates],
                    ]
                    for start in range(0, len(texts), 16):
                        batch = await self.embedder.embed(texts[start : start + 16])
                        if len(batch) != len(texts[start : start + 16]):
                            raise ValueError("embedding_count_mismatch")
                        vectors.extend(validate_vector(vector) for vector in batch)
            except (ValueError, RuntimeError, OSError, TimeoutError):
                return IncidentMatches(
                    **response,
                    status="unavailable",
                    complete=False,
                    items=[],
                    limitations=[*limitations, "embedding_unavailable"],
                )
            matches = [
                (
                    max(0.0, min(1.0, sum(a * b for a, b in zip(vectors[0], vector, strict=True)))),
                    row.Hypothesis,
                )
                for vector, row in zip(vectors[1:], candidates, strict=True)
            ]
            matches = sorted(
                (item for item in matches if item[0] >= min_similarity),
                key=lambda item: (-item[0], item[1].id),
            )[:limit]
            return IncidentMatches(
                **response,
                status="matched" if matches else "no_match",
                complete=complete,
                items=[
                    IncidentMatch(
                        similarity=round(similarity, 6),
                        hypothesis=await query.card(row, location_id),
                    )
                    for similarity, row in matches
                ],
                limitations=limitations,
            )
