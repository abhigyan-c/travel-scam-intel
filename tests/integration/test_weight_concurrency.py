import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.confidence import calculate_confidence
from app.db.evidence_weight import calculate_evidence_weights
from app.db.frequency import calculate_recency_and_frequency
from app.db.session import create_database_engine
from app.models import (
    Claim,
    Document,
    DuplicateAssessment,
    DuplicateDetectionRun,
    DuplicateGroup,
    Evidence,
    EvidenceWeightRun,
    Hypothesis,
    HypothesisScoreSnapshot,
    IndependenceRun,
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
                        name="Weight concurrency fixture",
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
                        raw_object_path="raw/weight-fixture",
                        content_hash="f" * 64,
                    ),
                    Hypothesis(
                        id=hypothesis_id,
                        primary_location_id=location_id,
                        title="Reported fares",
                        canonical_description="Fare reports",
                        category="transport",
                        confidence_score=0.8,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Claim(
                        id=identity,
                        document_id=document_id,
                        location_id=location_id,
                        claim_text="Reported meter refusal",
                        normalized_claim="meter refusal",
                        claim_type="transport",
                        structured_data={
                            "evidence_quote": QUOTE,
                            "first_hand": True,
                            "publication_status": "unreviewed",
                            "sensitive": False,
                        },
                    )
                    for identity in claims
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Evidence(
                        hypothesis_id=hypothesis_id,
                        claim_id=identity,
                        stance="supports",
                        source_quality_score=0.2,
                        evidence_weight=0.6,
                    )
                    for identity in claims
                ]
            )
        yield sessions, hypothesis_id
    finally:
        try:
            async with sessions() as session, session.begin():
                await session.execute(
                    delete(HypothesisScoreSnapshot).where(
                        HypothesisScoreSnapshot.hypothesis_id == hypothesis_id
                    )
                )
                await session.execute(
                    delete(Evidence).where(Evidence.hypothesis_id == hypothesis_id)
                )
                await session.execute(
                    delete(EvidenceWeightRun).where(
                        EvidenceWeightRun.hypothesis_id == hypothesis_id
                    )
                )
                await session.execute(
                    delete(IndependenceRun).where(IndependenceRun.hypothesis_id == hypothesis_id)
                )
                runs = select(DuplicateDetectionRun.id).where(
                    DuplicateDetectionRun.hypothesis_id == hypothesis_id
                )
                await session.execute(
                    delete(DuplicateAssessment).where(DuplicateAssessment.run_id.in_(runs))
                )
                await session.execute(
                    delete(DuplicateDetectionRun).where(
                        DuplicateDetectionRun.hypothesis_id == hypothesis_id
                    )
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


async def test_concurrent_weighting_reuses_one_history_run(committed_case):
    sessions, hypothesis_id = committed_case

    async def calculate():
        async with sessions() as session, session.begin():
            return await calculate_evidence_weights(session, hypothesis_id)

    one, two = await asyncio.wait_for(asyncio.gather(calculate(), calculate()), timeout=15)
    assert one.run_id == two.run_id and one.independence_run_id == two.independence_run_id
    assert sorted([one.changed, two.changed]) == [0, 2]
    assert one.total_weight == two.total_weight == 0.325
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(EvidenceWeightRun)
                .where(EvidenceWeightRun.hypothesis_id == hypothesis_id)
            )
            == 1
        )


async def test_concurrent_confidence_reuses_one_snapshot(committed_case):
    sessions, hypothesis_id = committed_case

    async def calculate():
        async with sessions() as session, session.begin():
            return await calculate_confidence(session, hypothesis_id)

    one, two = await asyncio.wait_for(asyncio.gather(calculate(), calculate()), timeout=15)
    assert one.snapshot_id == two.snapshot_id
    assert one.score == two.score > 0
    assert sorted([one.created, two.created]) == [False, True]


async def test_concurrent_frequency_reuses_one_temporal_snapshot(committed_case):
    sessions, hypothesis_id = committed_case
    as_of = datetime(2026, 10, 28, 12, tzinfo=UTC)

    async def calculate():
        async with sessions() as session, session.begin():
            return await calculate_recency_and_frequency(session, hypothesis_id, as_of=as_of)

    one, two = await asyncio.wait_for(asyncio.gather(calculate(), calculate()), timeout=15)
    assert one.snapshot_id == two.snapshot_id
    assert one.confidence_snapshot_id == two.confidence_snapshot_id
    assert sorted([one.created, two.created]) == [False, True]
    assert one.components["all_time"] == {
        "raw_report_count": 1,
        "independence_weighted_report_count": 0.5,
    }
    async with sessions() as session:
        snapshots = (
            await session.scalars(
                select(HypothesisScoreSnapshot).where(
                    HypothesisScoreSnapshot.hypothesis_id == hypothesis_id
                )
            )
        ).all()
        assert {row.scoring_version for row in snapshots} == {"confidence-v1", "frequency-v1"}


async def test_frequency_cli_preview_rolls_back_temporal_snapshots_and_current_caches(
    committed_case,
):
    sessions, hypothesis_id = committed_case
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "app.calculate_frequency", str(hypothesis_id), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["dry_run"] and output["components"]["all_time"]["raw_report_count"] == 1
    async with sessions() as session:
        for model in (
            HypothesisScoreSnapshot,
            EvidenceWeightRun,
            IndependenceRun,
            DuplicateDetectionRun,
        ):
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.hypothesis_id == hypothesis_id)
                )
                == 0
            )
        hypothesis = await session.get(Hypothesis, hypothesis_id)
        assert hypothesis.confidence_score == 0.8
        assert hypothesis.recency_score is hypothesis.prevalence_score is None
        attachment = await session.scalar(
            select(Evidence).where(Evidence.hypothesis_id == hypothesis_id)
        )
        assert attachment.evidence_weight == 0.6 and attachment.weight_run_id is None


async def test_confidence_cli_preview_rolls_back_snapshots_and_upstream_runs(committed_case):
    sessions, hypothesis_id = committed_case
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "app.calculate_confidence", str(hypothesis_id), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["score"] > 0
    async with sessions() as session:
        for model in (
            HypothesisScoreSnapshot,
            EvidenceWeightRun,
            IndependenceRun,
            DuplicateDetectionRun,
        ):
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.hypothesis_id == hypothesis_id)
                )
                == 0
            )
        assert (await session.get(Hypothesis, hypothesis_id)).confidence_score == 0.8


async def test_cli_preview_rolls_back_all_scoring_stages_and_original_caches(committed_case):
    sessions, hypothesis_id = committed_case
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "app.calculate_evidence_weights", str(hypothesis_id), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["total_weight"] == 0.325
    async with sessions() as session:
        for model in (EvidenceWeightRun, IndependenceRun, DuplicateDetectionRun):
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.hypothesis_id == hypothesis_id)
                )
                == 0
            )
        evidence = await session.scalar(
            select(Evidence).where(Evidence.hypothesis_id == hypothesis_id)
        )
        assert evidence.source_quality_score == 0.2 and evidence.evidence_weight == 0.6
        assert evidence.weight_run_id is None and evidence.weight_components == {}
        assert evidence.duplicate_group_id is None
        assert (await session.get(Hypothesis, hypothesis_id)).confidence_score == 0.8
