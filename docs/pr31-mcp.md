# PR 31: MCP integration

Base: merged `develop` at `22cdcb7`; branch `codex/pr-31-mcp-integration`.
The HTTPS and GitHub OIDC foundation is already deployed. This PR implements only
the MCP adapter; conversation optimization, UI and final hardening remain PRs 32–37.

## Server and tool contract

The pinned official Python SDK is `mcp==2.3.0`, with locked `mcp-types==2.3.0`.
The exact `/mcp` endpoint serves stateless Streamable HTTP with JSON responses and
legacy protocol negotiation for current clients. FastAPI owns the MCP session
manager lifespan alongside its existing database/Redis/storage resources. Existing
REST routes and error contracts remain intact; no redirect or separate server.

| Tool | Behavior |
| --- | --- |
| `resolve_scam_location` | Existing resolver; ambiguity stays unresolved; explicit known parent/country/type permits provisional creation. No country roots invented. |
| `search_scam_patterns` | Current public patterns plus a durable shared research request for stale/unseen locations. |
| `get_scam_pattern` | One public card through `IntelligenceService`. |
| `get_scam_evidence` | Bounded public evidence/provenance page; private reports/media excluded. |
| `match_possible_scam` | Local embedding comparison without persisting submitted text; unavailable differs from no match. |
| `get_recent_patterns` | Occurrence-based 30/90/365-day public query with optional background research. |
| `submit_scam_report` | Confirmed anonymous intake with a stable retry key, normalized document and durable processing/review. |

PR 29 assessments and acknowledgements are private administrative signals, not
published emerging labels. `get_emerging_patterns` is deliberately not advertised;
there is no public emerging contract to expose. No admin tokens or review tools
are available over MCP. This preserves PR 29's publication boundary.

Research-enabled calls are annotated as non-destructive writes/open-world because
they update demand/jobs and can invoke external providers in the worker. Resolution
is also annotated as a write because it can create a provisional record. Public
DB reads and local matching are read-only. Report submission requires a strict
`confirmed_by_user=true`; client confirmation remains necessary, since the server
cannot independently prove that a human supplied a boolean.

## Research and failure behavior

Unseen/stale searches queue the existing bounded initial/refresh pipeline, then
return available findings and research state immediately. No provider runs inline,
no duplicate pipeline, no LLM-generated response prose. Subsequent calls use the
same IDs with `request_research=false`; unfinished research remains incomplete,
and successful empty research retains the existing honest `nothing_found` outcome.
This selects the plan's immediate-result/background-continuation option rather
than holding a ChatGPT request open for the 25-second worker budget.

Pages are bounded at 20 items / offset 1000 (default five). Input bodies are capped
at 64 KiB, incident text at 2,000 characters and reports at 8,000. A 12-second tool
deadline bounds database/matching/intake waits. Timeouts after report commit are
safe to retry using the same key and content. The normalized intake/dispatch service
is shared with REST. Errors return `isError=true`, stable safe codes and request
IDs without reflecting input values or SDK/provider tracebacks.

`APP_MCP_PUBLIC_URL` configures the precise public Host/Origin allowlist; defaults
permit localhost only. Production Compose reads `MCP_PUBLIC_URL` from root-only
host configuration. DNS-rebinding protection stays enabled. No consumer login is
introduced; anonymous access follows the agreed product architecture. Caddy still
blocks REST/admin/media/docs and only exposes `/health` and `/mcp`.

## Required ChatGPT review gate

After protocol tests/deployment, connect `https://64.227.161.120/mcp` in ChatGPT
with no authentication. The account/workspace must support custom MCP connections.
Use the current [official connection instructions](https://developers.openai.com/plugins/deploy/connect-chatgpt).

1. Discover the seven tools and resolve Bangkok, Thailand. Inspect scope/confidence
   and evidence on available results; do not interpret incomplete research as safe.
2. First-ever query: ask about Chiang Mai, Thailand (city). Confirm provisional
   location context, immediate research-in-progress response and later reads using
   the returned location ID with `request_research=false`.
3. Ask about ambiguous Springfield without country/parent context; confirm that
   ChatGPT asks for clarification rather than selecting an arbitrary location.
4. Verify that an incident match is a suggestion, unavailable is explicit, and
   unsupported requests cannot call private review tools.
5. Review report confirmation behavior without inventing or submitting a production
   report. Actual report normalization/retry tests run only in isolated local schemas.

Record selected tools/arguments, timing, results and any timeout/continuation or
IP-URL acceptance issue. Do not mark the live gate passed from an SDK client alone.
The user has confirmed they can perform the ChatGPT check; it remains pending
until they report the actual conversation outcome. No PR 32 work starts first.

External alert delivery remains deferred at the user's request. The previously
measured media-memory, embedding backpressure and grown-corpus recent-query issues
remain PR 37/public-release work; this adapter does not claim to resolve them.

References: [MCP Python SDK ASGI mounting](https://py.sdk.modelcontextprotocol.io/run/asgi/),
[OpenAI MCP server guidance](https://developers.openai.com/plugins/build/mcp-server).

## Validation — 2026-11-05

- Full local suite: **841 passed, 10 skipped**. Focused MCP/shared-intake tests
  passed against isolated PostgreSQL schemas with controlled providers.
- Actual Streamable HTTP initialization at the exact endpoint, Host/Origin
  rejection, structured results and correlated sanitized errors passed.
- Database tests covered one durable unseen-location job, non-enqueueing polls,
  fresh reuse, stale refresh, public evidence, ambiguous/provisional places and
  confirmed normalized reports with replay/conflict handling.
- Existing infrastructure fixture now keeps AnyIO/ASGI lifespan in one task;
  a scheduler fixture uses explicit past timestamps to avoid Windows/Docker clock
  skew changing selection. No scheduler production behavior changed.
- Ruff, lockfile, production Compose validation and image build passed.
- Public deployment/protocol smoke and the user-run ChatGPT gate remain pending.
