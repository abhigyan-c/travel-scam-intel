import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.hypothesis_resolution import resolve_hypothesis
from app.db.session import create_database_engine
from app.embeddings import Embedder
from app.integrations.sarvam import Sarvam


async def main(args):
    settings = Settings()
    embedder = Embedder(args.cache_dir)
    await embedder.preload()
    engine = create_database_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            async with session.begin():
                result = await resolve_hypothesis(
                    session,
                    args.claim_id,
                    embedder=embedder,
                    client=Sarvam(settings.sarvam_api_key),
                    max_candidates=args.candidates,
                )
            print(
                json.dumps(
                    {
                        "resolution_id": str(result.id),
                        "decision": result.decision,
                        "target_id": str(result.target_id) if result.target_id else None,
                        "confidence": result.confidence,
                        "needs_review": result.needs_review,
                        "error": result.error,
                        "details": result.details,
                    },
                    indent=2,
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Record a hypothesis decision for an existing claim"
    )
    parser.add_argument("claim_id", type=UUID)
    parser.add_argument("--cache-dir", default=".tools/embedding-cache")
    parser.add_argument("--candidates", type=int, choices=range(1, 11), default=6)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main(parser.parse_args()))
