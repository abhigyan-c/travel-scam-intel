"""Production worker with deterministic external LLM responses; real embeddings/SQL.

No production credentials or paid provider calls. External LLM compute is not on
the Droplet. Sleep models network/service latency, not local model inference.
"""

import asyncio
import json
from uuid import uuid4

from app.discovery import SearchResponse, SearchResult, SearchUsage
from app.extraction import ClaimBatch, ExtractedClaim
from app.hypothesis_resolution import Decision
from app.integrations.fetch import FetchedPage
from app.integrations.sarvam import Completion
from app.relevance import Relevance
from app.worker import WorkerSettings as ProductionWorker
from app.worker import startup as production_startup

BODY = (
    "I agreed to a fixed taxi fare, but the driver demanded extra money. "
    "This is a synthetic travel report used only for a server sizing experiment. "
)


class ExternalSearch:
    async def search(self, query, **kwargs):
        await asyncio.sleep(0.5)
        return SearchResponse(
            results=[
                SearchResult(
                    url=f"https://example.org/sizing/{uuid4()}", position=i + 1, query=str(query)
                )
                for i in range(10)
            ],
            usage=SearchUsage(returned_results=10),
        )


class ExternalFetch:
    async def fetch(self, url):
        await asyncio.sleep(0.1)
        html = "<html><head><title>Synthetic traveler report</title></head><body><article>"
        html += "".join(f"<p>{BODY} Observation {i}.</p>" for i in range(100))
        html += "</article></body></html>"
        return FetchedPage(url, html.encode(), "text/html")


class ExternalLLM:
    async def complete(self, *, schema, user, **kwargs):
        await asyncio.sleep(0.5)
        if schema is Relevance:
            data = Relevance(relevant=True, category="fare", confidence=0.9, reason="Synthetic")
        elif schema is Decision:
            data = Decision(
                decision="NEW",
                target_id=None,
                confidence=0.95,
                ambiguous=False,
                reason="Synthetic isolated sizing workload",
            )
        else:
            passage = json.loads(user)["passage"]
            quote = passage.splitlines()[0][:1000]
            if len(passage.splitlines()[0]) > 1000:
                quote = quote[: quote.rfind(" ", 500)]
            data = ClaimBatch(
                claims=[
                    ExtractedClaim(
                        claim=(
                            "A traveler reports a driver demanded extra money "
                            "after an agreed fixed fare."
                        ),
                        evidence_quote=quote,
                        location=None,
                        approach=[],
                        methods=[],
                        warning_signs=[],
                        entities=[],
                        occurred_at=None,
                        first_hand=True,
                        confidence=0.9,
                        sensitive=False,
                    )
                ]
            )
        return Completion(data=data, requests=1)


async def startup(ctx):
    await production_startup(ctx)
    ctx["llm"] = ExternalLLM()
    ctx["provider"] = ExternalSearch()
    ctx["fetcher"] = ExternalFetch()


# arq reads __dict__ rather than inherited attributes when extracting settings.
WorkerSettings = type(
    "WorkerSettings",
    (),
    {
        **{key: value for key, value in vars(ProductionWorker).items() if not key.startswith("__")},
        "on_startup": startup,
    },
)
