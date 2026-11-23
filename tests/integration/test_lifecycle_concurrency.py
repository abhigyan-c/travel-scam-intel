import asyncio
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.hypothesis_lifecycle import apply_resolution
from app.db.hypothesis_resolution import resolve_hypothesis
from app.db.session import create_database_engine
from app.models import (
    Claim,
    Document,
    Evidence,
    Hypothesis,
    HypothesisLifecycleEvent,
    HypothesisResolution,
    Location,
    Source,
)
from app.models.location import LocationType
from app.models.source import SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


async def test_simultaneous_application_commits_one_candidate_and_attachment():
    engine = create_database_engine(Settings())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    location_id, source_id, document_id, claim_id = [uuid4() for _ in range(4)]
    embedder, client = AsyncMock(), AsyncMock()
    embedder.model, embedder.revision = "fixture-concurrent", "v1"
    try:
        async with sessions() as session, session.begin():
            session.add_all(
                [
                    Location(
                        id=location_id,
                        name="Concurrent fixture",
                        canonical_name=str(location_id),
                        type=LocationType.CITY,
                    ),
                    Source(
                        id=source_id,
                        platform="concurrent-fixture",
                        source_type=SourceType.USER_REPORT,
                    ),
                ]
            )
            await session.flush()
            session.add(
                Document(
                    id=document_id,
                    source_id=source_id,
                    raw_object_path="raw/concurrent",
                    content_hash="f" * 64,
                )
            )
            await session.flush()
            session.add(
                Claim(
                    id=claim_id,
                    document_id=document_id,
                    location_id=location_id,
                    claim_text="A reported fare scam.",
                    normalized_claim="reported fare scam",
                    claim_type="transport",
                )
            )
            await session.flush()
            resolution = await resolve_hypothesis(
                session, claim_id, embedder=embedder, client=client
            )
            resolution_id = resolution.id

        async def apply():
            async with sessions() as session, session.begin():
                event = await apply_resolution(session, resolution_id, embedder=embedder)
                return event.id, event.target_id

        one, two = await asyncio.wait_for(asyncio.gather(apply(), apply()), timeout=10)
        assert one == two
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Hypothesis)
                    .where(Hypothesis.primary_location_id == location_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(Evidence).where(Evidence.claim_id == claim_id)
                )
                == 1
            )
    finally:
        try:
            async with sessions() as session, session.begin():
                await session.execute(
                    delete(HypothesisLifecycleEvent).where(
                        HypothesisLifecycleEvent.resolution_id.in_(
                            select(HypothesisResolution.id).where(
                                HypothesisResolution.claim_id == claim_id
                            )
                        )
                    )
                )
                await session.execute(
                    delete(HypothesisResolution).where(HypothesisResolution.claim_id == claim_id)
                )
                await session.execute(delete(Evidence).where(Evidence.claim_id == claim_id))
                await session.execute(
                    delete(Hypothesis).where(Hypothesis.primary_location_id == location_id)
                )
                await session.execute(delete(Claim).where(Claim.id == claim_id))
                await session.execute(delete(Document).where(Document.id == document_id))
                await session.execute(delete(Source).where(Source.id == source_id))
                await session.execute(delete(Location).where(Location.id == location_id))
        finally:
            await engine.dispose()
