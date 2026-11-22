import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.api.locations import get_session
from app.core.config import Settings
from app.main import create_app
from app.models import (
    AdminAction,
    Claim,
    Document,
    Evidence,
    Hypothesis,
    Location,
    LocationResearchState,
    ResearchJob,
    Source,
    UserReport,
)
from app.models.location import LocationType
from app.models.source import SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
TOKEN = "synthetic-local-admin-token-" + "x" * 32


@pytest.fixture
async def fixture(session):
    city = Location(
        name="Admin synthetic city", canonical_name=str(uuid4()), type=LocationType.CITY
    )
    source = Source(platform="admin-fixture", source_type=SourceType.BLOG)
    session.add_all([city, source])
    await session.flush()
    document = Document(
        source_id=source.id, raw_object_path="raw/private-not-in-api", content_hash="a" * 64
    )
    session.add(document)
    await session.flush()
    hypotheses = [
        Hypothesis(
            primary_location_id=city.id,
            title=f"Synthetic candidate {i}",
            canonical_description="Synthetic review example",
            category="transport",
        )
        for i in range(2)
    ]
    session.add_all(hypotheses)
    await session.flush()
    claim = Claim(
        document_id=document.id,
        location_id=city.id,
        claim_text="Synthetic held allegation",
        normalized_claim="synthetic held allegation",
        claim_type="transport",
        structured_data={"publication_status": "held", "sensitive": True},
    )
    session.add(claim)
    await session.flush()
    evidence = Evidence(hypothesis_id=hypotheses[0].id, claim_id=claim.id, stance="supports")
    session.add(evidence)
    await session.flush()
    await session.commit()
    return city, document, hypotheses, claim, evidence


@pytest.fixture
async def client(session):
    app = create_app(Settings(_env_file=None, environment="test", admin_api_token=TOKEN))

    async def override():
        if session.in_transaction():
            await session.commit()
        yield session

    app.dependency_overrides[get_session] = override
    app.state.report_queue = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        yield client


async def send(client, body, key=None):
    return await client.post(
        "/v1/admin/commands",
        json={**body, "confirmed": True, "reason": "Synthetic reviewed decision"},
        headers={"Idempotency-Key": key or str(uuid4())},
    )


async def test_private_lists_and_details_are_bounded_and_redacted(client, fixture):
    city, document, hypotheses, _, _ = fixture
    for kind, identity in [
        ("locations", city.id),
        ("documents", document.id),
        ("hypotheses", hypotheses[0].id),
    ]:
        page = await client.get(f"/v1/admin/items/{kind}?limit=1")
        assert page.status_code == 200, page.text
        assert len(page.json()["items"]) <= 1
        detail = await client.get(f"/v1/admin/items/{kind}/{identity}")
        assert detail.status_code == 200, detail.text
        assert detail.headers["cache-control"] == "no-store"
        assert "raw/private-not-in-api" not in detail.text
    assert (await client.get("/v1/admin/items/hypotheses?status=invalid-status")).json()[
        "total"
    ] == 0
    assert (await client.get("/v1/admin/items/jobs?limit=51")).status_code == 422
    assert (await client.get("/v1/admin/audit")).status_code == 200


async def test_status_command_cas_replay_and_conflicting_key(client, session, fixture):
    h = fixture[2][0]
    key = str(uuid4())
    body = {
        "operation": "set_status",
        "subject_id": str(h.id),
        "expected_updated_at": h.updated_at.isoformat(),
        "status": "disputed",
    }
    first = await send(client, body, key)
    assert first.status_code == 200, first.text
    replay = await send(client, body, key)
    assert replay.json()["replayed"] is True
    assert replay.json()["action_id"] == first.json()["action_id"]
    assert (await send(client, {**body, "status": "active"}, key)).status_code == 409
    # Independent stale request cannot overwrite the accepted decision.
    assert (
        await send(client, {**body, "expected_updated_at": "2000-01-01T00:00:00Z"})
    ).status_code == 409
    await session.rollback()
    assert await session.scalar(select(func.count()).select_from(AdminAction)) == 1
    await session.refresh(fixture[3])
    assert fixture[3].structured_data["publication_status"] == "held"


async def test_move_preserves_original_evidence_and_hold(client, session, fixture):
    _, _, hs, claim, e = fixture
    body = {
        "operation": "move_evidence",
        "subject_id": str(hs[0].id),
        "expected_updated_at": hs[0].updated_at.isoformat(),
        "target_id": str(hs[1].id),
        "expected_target_at": hs[1].updated_at.isoformat(),
        "evidence_ids": [str(e.id)],
    }
    result = await send(client, body)
    assert result.status_code == 200, result.text
    await session.refresh(e)
    replacement = await session.get(Evidence, e.superseded_by_id)
    assert (
        e.archived_at and replacement.hypothesis_id == hs[1].id and replacement.claim_id == claim.id
    )
    assert claim.structured_data["sensitive"] is True
    page = await client.get(f"/v1/admin/hypotheses/{hs[0].id}/evidence")
    assert page.json()["items"][0]["archived"] is True


async def test_cross_location_move_is_atomic(client, session, fixture):
    _, _, hs, _, e = fixture
    other = Location(name="Other city", canonical_name=str(uuid4()), type=LocationType.CITY)
    session.add(other)
    await session.flush()
    hs[1].primary_location_id = other.id
    await session.commit()
    await session.refresh(hs[1])
    body = {
        "operation": "move_evidence",
        "subject_id": str(hs[0].id),
        "expected_updated_at": hs[0].updated_at.isoformat(),
        "target_id": str(hs[1].id),
        "expected_target_at": hs[1].updated_at.isoformat(),
        "evidence_ids": [str(e.id)],
    }
    assert (await send(client, body)).status_code == 409
    assert await session.scalar(select(func.count()).select_from(AdminAction)) == 0
    await session.refresh(e)
    assert e.archived_at is None


async def test_priority_preserves_research_lease_and_budget(client, session, fixture):
    city = fixture[0]
    token = uuid4()
    state = LocationResearchState(
        location_id=city.id,
        status="researching",
        run_token=token,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        progress={"continuation": {"used": {"llm_calls": 9}}},
    )
    session.add(state)
    await session.commit()
    result = await send(
        client,
        {
            "operation": "set_priority",
            "subject_id": str(city.id),
            "expected_updated_at": state.updated_at.isoformat(),
            "priority": 75,
        },
    )
    assert result.status_code == 200, result.text
    await session.refresh(state)
    assert state.priority_override == 75 and state.run_token == token
    assert state.progress["continuation"]["used"]["llm_calls"] == 9
    assert (
        await send(
            client,
            {
                "operation": "set_priority",
                "subject_id": str(city.id),
                "expected_updated_at": None,
                "priority": 101,
            },
        )
    ).status_code == 422


async def test_job_reissue_is_bounded_and_queue_failure_durable(client, session, fixture):
    document = fixture[1]
    old = ResearchJob(
        kind="process",
        status="failed",
        attempts=3,
        payload={"document_id": str(document.id)},
        idempotency_key=str(uuid4()),
    )
    session.add(old)
    await session.commit()
    body = {
        "operation": "retry_job",
        "subject_id": str(old.id),
        "expected_updated_at": old.updated_at.isoformat(),
    }
    # Transport failure after commit must not destroy the durable queued job.
    client_app = client._transport.app
    client_app.state.report_queue.enqueue_job.side_effect = ConnectionError("synthetic")
    key = str(uuid4())
    result = await send(client, body, key)
    assert result.status_code == 200 and result.json()["queue_pending"] is True, result.text
    again = await send(client, body, key)
    assert again.json()["replayed"] is True
    new = await session.get(ResearchJob, again.json()["result"]["job_id"])
    assert old.attempts == 3 and old.status == "failed" and new.attempts == 0
    await session.rollback()
    await session.refresh(document)
    duplicate = await send(
        client,
        {
            "operation": "reprocess_document",
            "subject_id": str(document.id),
            "expected_updated_at": document.updated_at.isoformat(),
        },
    )
    assert duplicate.status_code == 409


@pytest.mark.parametrize(
    "kind,status",
    [
        ("research", "failed"),
        ("location_research", "failed"),
        ("hypothesis_research", "failed"),
        ("process", "queued"),
    ],
)
async def test_research_and_nonfailed_jobs_cannot_be_reset(client, session, fixture, kind, status):
    job = ResearchJob(kind=kind, status=status, payload={}, idempotency_key=str(uuid4()))
    session.add(job)
    await session.commit()
    response = await send(
        client,
        {
            "operation": "retry_job",
            "subject_id": str(job.id),
            "expected_updated_at": job.updated_at.isoformat(),
        },
    )
    assert response.status_code == 409, response.text


@pytest.mark.parametrize("hold", [None, "text_hold", "media_hold", "flags"])
async def test_report_hold_blocks_retry_but_moderation_replays(client, session, fixture, hold):
    city, document, _, _, _ = fixture
    report = UserReport(
        document_id=document.id,
        location_id=city.id,
        report_text="Private synthetic report",
        provenance_class="SECOND_HAND",
        processing_status="failed",
        moderation_status="pending" if hold else "needs_review",
        processing_details={hold: ["private"] if hold == "flags" else True} if hold else {},
    )
    session.add(report)
    await session.flush()
    job = ResearchJob(
        kind="report",
        status="failed",
        attempts=3,
        payload={"report_id": str(report.id)},
        idempotency_key=str(uuid4()),
    )
    session.add(job)
    await session.commit()
    body = {
        "operation": "retry_job",
        "subject_id": str(job.id),
        "expected_updated_at": job.updated_at.isoformat(),
    }
    assert (await send(client, body)).status_code == 409
    await session.refresh(report)
    rid = str(report.id)
    body = {
        "operation": "moderate_report",
        "subject_id": rid,
        "expected_revision": 0,
        "status": "rejected",
    }
    key = str(uuid4())
    first = await send(client, body, key)
    assert first.status_code == 200, first.text
    second = await send(client, body, key)
    assert second.json()["replayed"] is True
    detail = await client.get(f"/v1/admin/items/reports/{rid}")
    assert detail.json()["moderation_status"] == "rejected"
    audit = await client.get("/v1/admin/audit")
    assert any(item["source"] == "report" for item in audit.json()["items"])


@pytest.mark.parametrize("operation", ["edit_hypothesis", "merge", "split"])
async def test_graph_commands_reuse_guarded_lifecycle(client, session, fixture, operation):
    _, _, hs, _, e = fixture
    body = {
        "operation": operation,
        "subject_id": str(hs[0].id),
        "expected_updated_at": hs[0].updated_at.isoformat(),
    }
    if operation in {"edit_hypothesis", "split"}:
        body["draft"] = {
            "title": "Reviewed synthetic pattern",
            "description": "Reviewed synthetic description",
            "category": "transport",
        }
    if operation == "merge":
        body.update(target_id=str(hs[1].id), expected_target_at=hs[1].updated_at.isoformat())
    if operation == "split":
        body["evidence_ids"] = [str(e.id)]
    result = await send(client, body)
    assert result.status_code == 200, result.text
    assert result.json()["result"]["lifecycle_event_id"]
    detail = await client.get(f"/v1/admin/items/hypotheses/{body['subject_id']}")
    if operation == "edit_hypothesis":
        assert detail.json()["title"] == "Reviewed synthetic pattern"
    if operation == "merge":
        assert detail.json()["merged_into_id"] == str(hs[1].id)
    if operation == "split":
        target = await session.get(Hypothesis, result.json()["result"]["target_id"])
        assert (
            target.status == "candidate" and target.primary_location_id == hs[0].primary_location_id
        )


async def test_sensitive_report_cannot_be_released_by_admin_command(client, session, fixture):
    city, document, _, _, _ = fixture
    report = UserReport(
        document_id=document.id,
        location_id=city.id,
        report_text="Private held synthetic report",
        provenance_class="SECOND_HAND",
        processing_status="needs_review",
        moderation_status="needs_review",
        processing_details={"text_complete": True},
    )
    session.add(report)
    await session.commit()
    response = await send(
        client,
        {
            "operation": "moderate_report",
            "subject_id": str(report.id),
            "expected_revision": 0,
            "status": "accepted",
        },
    )
    assert response.status_code == 409
    assert await session.scalar(select(func.count()).select_from(AdminAction)) == 0


async def test_concurrent_exact_commands_commit_one_audit_record():
    import asyncio

    from pydantic import TypeAdapter
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.session import Base, create_database_engine
    from app.schemas.admin import AdminCommand
    from app.services.admin import command

    # Independent real transactions, rather than sharing the rollback fixture connection.
    owner = create_database_engine(Settings(_env_file=None))
    schema = "admin_concurrency_" + uuid4().hex
    async with owner.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        Settings(_env_file=None).database_url.get_secret_value(),
        connect_args={"options": f"-csearch_path={schema},public"},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
        async with sessions() as session, session.begin():
            city = Location(
                name="Concurrent fixture",
                canonical_name="concurrent fixture",
                type=LocationType.CITY,
            )
            session.add(city)
            await session.flush()
            h = Hypothesis(
                primary_location_id=city.id,
                title="Concurrent synthetic candidate",
                canonical_description="Synthetic only",
                category="transport",
            )
            session.add(h)
            await session.flush()
            payload = TypeAdapter(AdminCommand).validate_python(
                {
                    "operation": "set_status",
                    "subject_id": h.id,
                    "expected_updated_at": h.updated_at,
                    "status": "disputed",
                    "reason": "Synthetic concurrent review",
                    "confirmed": True,
                }
            )
        key = str(uuid4())

        async def apply():
            async with sessions() as session, session.begin():
                event, replayed = await command(session, payload, key, embedder=None)
                return event.id, replayed

        results = await asyncio.wait_for(asyncio.gather(apply(), apply()), timeout=10)
        assert results[0][0] == results[1][0]
        assert sorted(row[1] for row in results) == [False, True]
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(AdminAction)) == 1
        # Verify the documented downgrade/re-upgrade contract in this disposable schema.
        import importlib.util
        from pathlib import Path

        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        from app.models import HypothesisLifecycleEvent

        path = Path("migrations/versions/0023_admin.py")
        spec = importlib.util.spec_from_file_location("admin_migration_test", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        async with sessions() as session, session.begin():
            session.add(
                HypothesisLifecycleEvent(
                    key=str(uuid4()),
                    input_hash="b" * 64,
                    operation="move",
                    source_id=h.id,
                    target_id=h.id,
                    actor="fixture",
                    reason="Historical move fixture",
                    details={},
                )
            )

        def round_trip(connection):
            assert connection.scalar(select(func.current_schema())) == schema
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                assert (
                    connection.scalar(select(func.count()).select_from(HypothesisLifecycleEvent))
                    == 1
                )
                migration.upgrade()
                assert connection.scalar(select(func.count()).select_from(AdminAction)) == 0

        async with engine.begin() as connection:
            await connection.run_sync(round_trip)
    finally:
        await engine.dispose()
        assert schema.startswith("admin_concurrency_") and schema[18:].isalnum()
        async with owner.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await owner.dispose()


@pytest.mark.parametrize("changed", [False, True])
async def test_resolution_review_keeps_snapshot_fences_and_publication_hold(
    client, session, fixture, changed
):
    from app.db.hypothesis_resolution import resolve_hypothesis

    _, _, hs, claim, _ = fixture
    hs[0].canonical_description = claim.claim_text
    await session.flush()
    embedder = AsyncMock()
    embedder.model = "fixture"
    embedder.revision = "admin-v1"
    client._transport.app.state.embedder = embedder
    resolution = await resolve_hypothesis(session, claim.id, embedder=embedder, client=AsyncMock())
    assert resolution.needs_review is True
    if changed:
        claim.claim_text = "A changed synthetic claim"
    await session.commit()
    rid = str(resolution.id)
    detail = await client.get(f"/v1/admin/items/resolutions/{rid}")
    assert detail.status_code == 200 and detail.json()["candidates"], detail.text
    response = await send(client, {"operation": "apply_resolution", "subject_id": rid})
    assert response.status_code == (409 if changed else 200), response.text
    await session.refresh(claim)
    assert claim.structured_data["publication_status"] == "held"
    queue = await client.get("/v1/admin/items/resolutions")
    assert (rid in [item["id"] for item in queue.json()["items"]]) == changed
