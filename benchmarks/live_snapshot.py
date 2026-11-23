"""Read-only production trace. Exports IDs/checks, never held claim text or credentials."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.processing import _read_raw
from app.db.session import create_database_engine
from app.integrations.storage import create_storage_client
from app.models import (
    Claim,
    Document,
    Evidence,
    Location,
    LocationResearchState,
    ResearchJob,
    Source,
)
from app.services.intelligence import IntelligenceService


def scrub(value):
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items() if key != "request_id"}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def local_reads(identity):
    observations = []
    for _ in range(3):
        started = time.perf_counter()
        url = f"http://127.0.0.1:8000/v1/locations/{identity}/hypotheses?research=false&limit=5"
        with urllib.request.urlopen(url, timeout=10) as response:
            page = json.load(response)
        observations.append(
            dict(
                seconds=round(time.perf_counter() - started, 4),
                outcome=page["outcome"],
                total=page["total"],
            )
        )
    return observations


async def snapshot(identities):
    settings = Settings()
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = IntelligenceService(sessions)
    storage = create_storage_client(settings)
    observations = []
    try:
        for identity in identities:
            page = await service.search_location_scams(identity, limit=5)
            async with sessions() as session, session.begin():
                await session.execute(text("SET TRANSACTION READ ONLY"))
                location = await session.get(Location, identity)
                state = await session.get(LocationResearchState, identity)
                cursor = state.progress.get("continuation", {}) if state else {}
                jobs = list(
                    await session.scalars(
                        select(ResearchJob)
                        .where(ResearchJob.payload["location_id"].astext == str(identity))
                        .order_by(ResearchJob.created_at.desc())
                        .limit(cursor.get("jobs", 1))
                    )
                )
                progress = dict(
                    run_id=cursor.get("run_id"),
                    caps=cursor.get("caps"),
                    reserved=cursor.get("used"),
                    seconds_cap=cursor.get("seconds_cap"),
                    seconds_reserved=cursor.get("seconds_reserved"),
                    jobs=cursor.get("jobs"),
                    queries=[
                        dict(
                            query=q,
                            attempts=v.get("attempts"),
                            complete=v.get("complete"),
                            usage=v.get("usage"),
                        )
                        for q, v in cursor.get("queries", {}).items()
                    ],
                    candidates=len(cursor.get("candidates", {})),
                    candidate_errors=[
                        error
                        for c in cursor.get("candidates", {}).values()
                        for error in c.get("errors", [])
                    ],
                    document_ids=[
                        c["document_id"]
                        for c in cursor.get("candidates", {}).values()
                        if c.get("document_id")
                    ],
                )
                job_rows = [
                    dict(
                        id=str(j.id),
                        phase=j.payload.get("phase"),
                        status=j.status,
                        attempts=j.attempts,
                        created_at=j.created_at.isoformat(),
                        updated_at=j.updated_at.isoformat(),
                        error_code=j.error_code,
                        result_status=j.result.get("status"),
                        complete=j.result.get("complete"),
                        errors=j.result.get("errors"),
                        usage=j.result.get("usage"),
                        diagnostics=j.result.get("diagnostics"),
                    )
                    for j in jobs
                ]
            verified = []
            for card in page.items:
                evidence = await service.get_hypothesis_evidence(card.id, limit=5)
                for item in evidence.items:
                    if item.label != "public_web_source":
                        continue
                    async with sessions() as session, session.begin():
                        await session.execute(text("SET TRANSACTION READ ONLY"))
                        row = (
                            await session.execute(
                                select(Claim, Document, Source)
                                .join(Document, Claim.document_id == Document.id)
                                .join(Source, Document.source_id == Source.id)
                                .join(Evidence, Evidence.claim_id == Claim.id)
                                .where(Evidence.id == item.id)
                            )
                        ).one()
                        claim, document, source = row
                        data = claim.structured_data
                        quote, extraction = (
                            data.get("evidence_quote", ""),
                            data.get("extraction", {}),
                        )
                        start, end = extraction.get("quote_start"), extraction.get("quote_end")
                        offsets = (
                            isinstance(start, int)
                            and isinstance(end, int)
                            and 0 <= start < end <= len(document.clean_text or "")
                        )
                        raw, _ = await asyncio.to_thread(
                            _read_raw, storage, settings.s3_bucket, document.raw_object_path
                        )
                        checks = dict(
                            raw_hash=hashlib.sha256(raw).hexdigest() == document.content_hash,
                            clean_hash=hashlib.sha256(
                                (document.clean_text or "").encode()
                            ).hexdigest()
                            == document.clean_text_hash,
                            quote=bool(quote)
                            and bool(offsets)
                            and document.clean_text[start:end] == quote,
                            lineage=extraction.get("clean_text_hash") == document.clean_text_hash
                            and claim.extraction_id is not None,
                            same_source=item.source_url == source.canonical_url,
                            not_held=data.get("publication_status") in {"unreviewed", "approved"}
                            and data.get("sensitive") is False
                            and not data.get("entities"),
                        )
                        verified.append(
                            dict(
                                pattern_id=str(card.id),
                                evidence_id=str(item.id),
                                claim_id=str(claim.id),
                                document_id=str(document.id),
                                source_id=str(source.id),
                                source_url=item.source_url,
                                actual_location=card.location.model_dump(mode="json"),
                                scope=card.scope,
                                provenance_class=item.provenance_class,
                                quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
                                quote_start=start,
                                quote_end=end,
                                checks=checks,
                                verified=all(checks.values()),
                                from_current_root=str(document.id) in progress["document_ids"],
                            )
                        )
            observations.append(
                dict(
                    location_id=str(identity),
                    location=location.name,
                    captured_at=datetime.now(UTC).isoformat(),
                    public=page.model_dump(mode="json"),
                    progress=progress,
                    jobs=job_rows,
                    public_evidence=verified,
                    localhost_rest_reads=await asyncio.to_thread(local_reads, identity),
                    job_selection="Newest N jobs; N from cursor, not a historical root join.",
                )
            )
        return scrub(
            dict(
                version="live-research-snapshot-v1",
                observations=observations,
                read_only=True,
                synthetic_production_records_created=False,
            )
        )
    finally:
        storage.close()
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("location_ids", nargs="+", type=UUID)
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.remote:
        code = "import sys\nsys.argv=" + repr(["snapshot", *map(str, args.location_ids)]) + "\n"
        code += Path(__file__).read_text()
        result = subprocess.run(
            ["ssh", "travel-scam-prod", "docker", "exec", "-i", "travel-scam-prod-api-1", "python"],
            input=code,
            text=True,
            capture_output=True,
            check=True,
            timeout=60,
        )
        report = json.loads(result.stdout)
    else:
        report = asyncio.run(snapshot(args.location_ids))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
