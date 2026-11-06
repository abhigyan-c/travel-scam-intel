"""Read-only, body-free snapshot of the recent Bangkok/Chiang Mai research jobs.

Run inside an existing API container, redirect stdout to a local JSON file.
No claims, quotes, URLs, identifiers, credentials or raw provider responses are exported.
"""

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import String, select, text

from app.core.config import Settings
from app.db.session import create_database_engine
from app.models import Location, ResearchJob


def safe_job(location, job):
    result = job.result or {}
    stages = Counter()
    for row in result.get("diagnostics", {}).get("stage_timings", []):
        stages[row["stage"]] += row["seconds"]
    return {
        "location": location,
        "phase": job.payload.get("phase"),
        "created_at": job.created_at.isoformat(),
        "job_status": getattr(job.status, "value", job.status),
        "job_elapsed_seconds": round((job.updated_at - job.created_at).total_seconds(), 3),
        "result_status": result.get("status"),
        "complete": result.get("complete", False),
        "error_code": job.error_code,
        "stage_seconds": {k: round(v, 3) for k, v in stages.items()},
        "candidates": result.get("candidates"),
        "fetch_attempts": result.get("fetched"),
        "documents": len(result["documents"]) if "documents" in result else None,
        "claims": len(set(result["claim_ids"])) if "claim_ids" in result else None,
        "hypotheses": len(set(result["hypothesis_ids"])) if "hypothesis_ids" in result else None,
        "llm_calls": result.get("usage", {}).get("llm_calls"),
        "tokens": sum(
            row.get("usage", {}).get("total_tokens", 0)
            for row in result.get("usage", {}).get("llm", [])
        )
        if "usage" in result
        else None,
        "errors": dict(Counter(row["code"] for row in result.get("errors", []))),
        "extraction_errors": dict(
            Counter(
                a["error"]
                for d in result.get("documents", [])
                for a in d.get("extraction_attempts", [])
                if a.get("error")
            )
        ),
        "usage_complete": result.get("usage_complete", "usage" in result),
    }


async def main():
    engine = create_database_engine(Settings())
    try:
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            jobs = []
            for canonical in ("bangkok", "chiang mai"):
                # ORM sessions are unnecessary for this bounded read-only export.
                rows = (
                    (
                        await connection.execute(
                            select(ResearchJob.__table__, Location.name.label("location_name"))
                            .join(
                                Location,
                                ResearchJob.payload["location_id"].astext
                                == Location.id.cast(String),
                            )
                            .where(Location.canonical_name == canonical)
                            .order_by(ResearchJob.created_at.desc())
                            .limit(3)
                        )
                    )
                    .mappings()
                    .all()
                )
                from types import SimpleNamespace

                for row in rows:
                    jobs.append(safe_job(row["location_name"], SimpleNamespace(**row)))
        print(
            json.dumps(
                {
                    "captured_at": datetime.now(UTC).isoformat(),
                    "kind": "observational_production_jobs",
                    "jobs": jobs,
                    "limitations": [
                        "No replay; live search ordering and availability vary.",
                        "Job elapsed includes queue/finish overhead, not just execution.",
                        "Missing historical telemetry is null, never interpreted as zero.",
                    ],
                },
                indent=2,
            )
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
