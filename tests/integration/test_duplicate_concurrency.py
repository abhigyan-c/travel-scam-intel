import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.duplicates import detect_duplicates
from app.db.session import create_database_engine
from app.models import (
    Claim,
    Document,
    DuplicateAssessment,
    DuplicateDetectionRun,
    DuplicateGroup,
    Evidence,
    Hypothesis,
    Location,
    Source,
)
from app.models.location import LocationType
from app.models.source import SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]
QUOTE = (
    "A driver at the station refused the meter and demanded a much higher fixed fare "
    "before taking the traveler to a hotel late that evening."
)


@pytest.fixture
async def committed_case():
    engine = create_database_engine(Settings())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    location_id, source_id, document_id, hypothesis_id = [uuid4() for _ in range(4)]
    claims = [uuid4(), uuid4()]
    try:
        async with sessions() as session, session.begin():
            session.add_all(
                [
                    Location(
                        id=location_id,
                        name="Concurrent duplicate fixture",
                        canonical_name=str(location_id),
                        type=LocationType.CITY,
                    ),
                    Source(id=source_id, platform="fixture", source_type=SourceType.FORUM),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Document(
                        id=document_id,
                        source_id=source_id,
                        raw_object_path="raw/duplicate-concurrent",
                        content_hash="f" * 64,
                    ),
                    Hypothesis(
                        id=hypothesis_id,
                        primary_location_id=location_id,
                        title="Reported fare scam",
                        canonical_description="Fare inflation",
                        category="transport",
                    ),
                ]
            )
            await session.flush()
            for claim_id in claims:
                session.add(
                    Claim(
                        id=claim_id,
                        document_id=document_id,
                        location_id=location_id,
                        claim_text="A driver refused the meter.",
                        normalized_claim="driver refused meter",
                        claim_type="transport",
                        structured_data={"evidence_quote": QUOTE},
                    )
                )
            await session.flush()
            session.add_all(
                [
                    Evidence(hypothesis_id=hypothesis_id, claim_id=claim_id, stance="supports")
                    for claim_id in claims
                ]
            )
        yield sessions, hypothesis_id
    finally:
        try:
            async with sessions() as session, session.begin():
                await session.execute(
                    delete(DuplicateAssessment).where(
                        DuplicateAssessment.run_id.in_(
                            select(DuplicateDetectionRun.id).where(
                                DuplicateDetectionRun.hypothesis_id == hypothesis_id
                            )
                        )
                    )
                )
                await session.execute(
                    delete(DuplicateDetectionRun).where(
                        DuplicateDetectionRun.hypothesis_id == hypothesis_id
                    )
                )
                await session.execute(
                    delete(Evidence).where(Evidence.hypothesis_id == hypothesis_id)
                )
                await session.execute(
                    delete(DuplicateGroup).where(
                        DuplicateGroup.detection_metadata["hypothesis_id"].astext
                        == str(hypothesis_id)
                    )
                )
                await session.execute(delete(Hypothesis).where(Hypothesis.id == hypothesis_id))
                await session.execute(delete(Claim).where(Claim.id.in_(claims)))
                await session.execute(delete(Document).where(Document.id == document_id))
                await session.execute(delete(Source).where(Source.id == source_id))
                await session.execute(delete(Location).where(Location.id == location_id))
        finally:
            await engine.dispose()


async def test_simultaneous_detection_commits_one_run_and_one_group(committed_case):
    sessions, hypothesis_id = committed_case

    async def detect():
        async with sessions() as session, session.begin():
            return await detect_duplicates(session, hypothesis_id)

    one, two = await asyncio.wait_for(asyncio.gather(detect(), detect()), timeout=15)
    assert one.run_id == two.run_id and one.groups == two.groups
    assert sorted([one.changed, two.changed]) == [0, 2]
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DuplicateAssessment)
                .where(DuplicateAssessment.run_id == one.run_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DuplicateDetectionRun)
                .where(DuplicateDetectionRun.hypothesis_id == hypothesis_id)
            )
            == 1
        )


async def test_cli_dry_run_rolls_back_groups_runs_and_tags(committed_case):
    sessions, hypothesis_id = committed_case
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "app.detect_duplicates", str(hypothesis_id), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["dry_run"] and len(output["groups"]) == 1
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DuplicateDetectionRun)
                .where(DuplicateDetectionRun.hypothesis_id == hypothesis_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(Evidence.duplicate_group_id)
                .where(Evidence.hypothesis_id == hypothesis_id)
                .limit(1)
            )
            is None
        )
