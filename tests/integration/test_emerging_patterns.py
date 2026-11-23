import asyncio
import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr
from sqlalchemy import func, select

from app.db.emergence import decide_emergence, detect_emergence
from app.embeddings import MODEL, REVISION, text_hash
from app.main import create_app
from app.models import (
    Claim,
    Document,
    EmergingAssessment,
    EmergingReview,
    EmergingReviewDecision,
    Evidence,
    Hypothesis,
    Location,
    UserReport,
)
from tests.integration.test_intelligence_services import seed
from tests.integration.test_on_demand_research import environment as environment

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
NOW = datetime(2030, 1, 1, tzinfo=UTC)
ADMIN = "emergence-review-test-admin-token-only"


async def observed(ctx, location, *, age=1, vector=False, **options):
    record = await seed(ctx, location, **options)
    async with ctx["sessions"]() as session, session.begin():
        claim = await session.get(Claim, record[1])
        claim.created_at = NOW - timedelta(days=age)
        if vector:
            claim.embedding = [1.0] + [0.0] * 383
            claim.embedding_model, claim.embedding_revision = MODEL, REVISION
            claim.embedding_input_hash = text_hash(claim.claim_text)
        if record[5]:
            (await session.get(UserReport, record[5])).created_at = NOW - timedelta(days=age)
    return record


async def scan(ctx, identity, **options):
    async with ctx["sessions"]() as session, session.begin():
        return await detect_emergence(session, identity, as_of=NOW, **options)


async def test_idempotent_assessment_and_private_review_decisions(environment):
    ctx, location, _ = environment
    record = await observed(ctx, location)
    first, review = await scan(ctx, record[0])
    second, again = await scan(ctx, record[0])
    assert first.id == second.id and again.revision == review.revision == 1
    assert first.candidate and first.signals["confidence"]["score"] < 0.99
    async with ctx["sessions"]() as session, session.begin():
        await decide_emergence(
            session, record[0], revision=1, action="dismissed", reason="Needs more context"
        )
    _, preserved = await scan(ctx, record[0])
    assert preserved.status == "dismissed" and preserved.revision == 2
    async with ctx["sessions"]() as session, session.begin():
        _, advanced = await detect_emergence(session, record[0], as_of=NOW + timedelta(days=1))
        assert advanced.status == "dismissed" and advanced.revision == 2
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(EmergingAssessment)) == 2
        assert await session.scalar(select(func.count()).select_from(EmergingReviewDecision)) == 1
        assert (await session.get(Hypothesis, record[0])).confidence_score == 0.99
    ctx["provider"].search.assert_not_called()
    ctx["llm"].complete.assert_not_called()
    ctx["embedder"].embed.assert_not_called()


async def test_copies_and_hidden_support_cannot_inflate_growth(environment):
    ctx, location, _ = environment
    public = await observed(ctx, location, report=True)
    await observed(ctx, location, hypothesis_id=public[0], report=True)
    assessment, _ = await scan(ctx, public[0])
    assert assessment.signals["growth"]["current_credit"] <= 1
    assert assessment.signals["growth"]["status"] == "insufficient_data"
    await observed(ctx, location, hypothesis_id=public[0], data={"sensitive": True}, age=100)
    same, _ = await scan(ctx, public[0])
    assert same.id == assessment.id
    async with ctx["sessions"]() as session, session.begin():
        (await session.get(UserReport, public[5])).moderation_status = "rejected"
    changed, review = await scan(ctx, public[0])
    assert changed.id != assessment.id and review.revision == 2


async def test_new_geographic_appearance_uses_public_compatible_support_vectors(environment):
    ctx, country, _ = environment
    async with ctx["sessions"]() as session, session.begin():
        nadi = Location(
            name="Nadi", canonical_name="nadi", type="city", country_code="FJ", parent_id=country
        )
        suva = Location(
            name="Suva", canonical_name="suva", type="city", country_code="FJ", parent_id=country
        )
        session.add_all([nadi, suva])
        await session.flush()
    old = await observed(ctx, nadi.id, age=100, vector=True)
    local = await observed(ctx, suva.id, vector=True)
    result, _ = await scan(ctx, local[0])
    assert result.signals["geography"]["possible_new_appearance"]
    assert result.signals["comparisons"][0]["hypothesis_id"] == str(old[0])
    assert result.signals["comparisons"][0]["scope"] == "other"
    async with ctx["sessions"]() as session, session.begin():
        claim = await session.get(Claim, old[1])
        claim.embedding_input_hash = "0" * 64
    unknown, _ = await scan(ctx, local[0])
    assert not unknown.signals["complete"] and not unknown.signals["comparisons"]
    assert not unknown.signals["geography"]["possible_new_appearance"]


async def test_queue_withdraws_when_eligibility_is_lost_and_reopens_on_material_change(environment):
    ctx, location, _ = environment
    record = await observed(ctx, location)
    _, review = await scan(ctx, record[0])
    async with ctx["sessions"]() as session, session.begin():
        claim = await session.get(Claim, record[1])
        claim.structured_data = {**claim.structured_data, "sensitive": True}
    withdrawn, review = await scan(ctx, record[0])
    assert not withdrawn.candidate and review.status == "withdrawn" and review.revision == 2
    await observed(ctx, location, hypothesis_id=record[0])
    _, reopened = await scan(ctx, record[0])
    assert reopened.status == "pending" and reopened.revision == 3


async def test_archived_versions_preserve_first_dataset_observation(environment):
    ctx, location, _ = environment
    old = await observed(ctx, location, age=100)
    newer = await observed(ctx, location, hypothesis_id=old[0], age=1)
    async with ctx["sessions"]() as session, session.begin():
        (await session.get(Document, newer[4])).source_id = old[3]
        prior = await session.get(Evidence, old[2])
        prior.archived_at, prior.superseded_by_id = NOW, newer[2]
    assessment, review = await scan(ctx, old[0])
    assert assessment.signals["novelty"]["status"] == "previously_observed"
    assert assessment.signals["growth"]["current_reports"] == 0
    assert not assessment.candidate and review is None


async def test_older_assessment_cannot_rewind_current_queue(environment):
    ctx, location, _ = environment
    record = await observed(ctx, location)
    current, _ = await scan(ctx, record[0])
    async with ctx["sessions"]() as session, session.begin():
        old, review = await detect_emergence(session, record[0], as_of=NOW - timedelta(days=60))
    assert old.id != current.id and review.assessment_id == current.id
    assert review.status == "pending" and review.revision == 1


async def test_unavailable_scoring_is_explicit_and_keeps_existing_candidate_for_reassessment(
    environment, monkeypatch
):
    ctx, location, _ = environment
    record = await observed(ctx, location)
    await scan(ctx, record[0])
    await observed(ctx, location, hypothesis_id=record[0])
    monkeypatch.setattr("app.services.intelligence.MAX_COHORT", 1)
    unknown, review = await scan(ctx, record[0])
    assert unknown.candidate is None and review.status == "needs_assessment"
    assert unknown.signals["limitations"] == ["public_evidence_limit"]


async def test_concurrent_scans_share_one_queue_and_stale_decisions_conflict(environment):
    ctx, location, _ = environment
    record = await observed(ctx, location)
    results = await asyncio.gather(scan(ctx, record[0]), scan(ctx, record[0]))
    assert results[0][0].id == results[1][0].id
    async with ctx["sessions"]() as session, session.begin():
        await decide_emergence(
            session,
            record[0],
            revision=1,
            action="acknowledged",
            reason="Reviewed as a dataset candidate",
        )
    async with ctx["sessions"]() as session, session.begin():
        with pytest.raises(ValueError, match="review_changed"):
            await decide_emergence(
                session, record[0], revision=1, action="dismissed", reason="Stale review"
            )


async def test_private_admin_queue_auth_pagination_and_no_public_promotion(environment):
    ctx, location, _ = environment
    record = await observed(ctx, location)
    async with ctx["sessions"]() as session, session.begin():
        (await session.get(Claim, record[1])).created_at = datetime.now(UTC) - timedelta(days=1)
    application = create_app(ctx["settings"])
    application.state.db_sessions = ctx["sessions"]
    ctx["settings"].admin_api_token = SecretStr(ADMIN)
    headers = {"Authorization": f"Bearer {ADMIN}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        path = f"/v1/admin/hypotheses/{record[0]}/emergence-assessments"
        assert (await client.get("/v1/admin/emerging-patterns")).status_code == 401
        response = await client.post(path, headers=headers)
        assert response.status_code == 200, response.text
        listing = await client.get("/v1/admin/emerging-patterns?limit=1", headers=headers)
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1 and listing.json()["items"][0]["currently_public"]
        assert listing.headers["cache-control"] == "no-store"
        revision = response.json()["review"]["revision"]
        decision = await client.post(
            f"/v1/admin/emerging-patterns/{record[0]}/decision",
            headers=headers,
            json={
                "revision": revision,
                "action": "acknowledged",
                "reason": "Review candidate only",
            },
        )
        assert decision.status_code == 200, decision.text
        public = await client.get(f"/v1/hypotheses/{record[0]}")
        assert public.status_code == 200 and "emerging" not in public.text
        assert "PRIVATE" not in listing.text


async def test_migration_0020_round_trip_in_isolated_schema(environment):
    ctx, _, _ = environment
    path = next(Path("migrations/versions").glob("0020_*.py"))
    spec = importlib.util.spec_from_file_location("migration_emerging_test", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def round_trip(connection):
        assert connection.scalar(select(func.current_schema())).startswith("research_test_")
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()

    async with ctx["sessions"]() as session, session.begin():
        connection = await session.connection()
        await connection.run_sync(round_trip)
        assert await session.scalar(select(func.count()).select_from(EmergingReview)) == 0
