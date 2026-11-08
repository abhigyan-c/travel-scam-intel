# Travel Scam Intelligence Implementation Plan

Planning baseline: 2026-10-11. This plan follows `docs/prompts/travel_scam_intelligence_master_prompt.md` and its confirmed project decisions. It does not authorize implementing multiple PRs in one run.

Deployment revision, 2026-10-31: target approximately 100 user requests/day on one
low-cost VPS with no separate load balancer. The full AWS topology is superseded.
See [minimal MVP deployment decision](minimal-mvp-deployment.md) for the recommended
Compose/Caddy/R2 design, current price comparisons, tradeoffs and outstanding choices.
DigitalOcean is the selected provider as of 2026-11-03. R2/Bitwarden are provisioned.
The [sizing experiment](digitalocean-sizing-experiment.md) makes 2 GiB conditional
on safeguards. The provisioned 2-vCPU / 4-GiB / 80-GB-class Bangalore Droplet and
Tailscale administration passed [host preflight](host-readiness.md) on 2026-11-03.

## Delivery workflow

- Retain exactly 38 numbered implementation PRs and their scope/review gates. PR 31 resumed on 2026-11-05 after HTTPS and ChatGPT access became available. Its hostname connection, first-ever query and follow-up gate passed with honest partial state; no completed research/findings were demonstrated before the worker deadlines. The user requested deadline investigation within PR 31: extraction time reservation, reusable partial relevance and retained diagnostics were implemented; a measured live refresh still returned zero claims because of fetch failures and grounding rejections. These remain product-readiness concerns; see `docs/pr31-mcp.md`. Any dependency-ready reordering must follow the deployment decision above and user confirmation.
- Create each branch from the latest `develop`; target `develop`. Branch convention: `codex/pr-NN-short-description`.
- Merge prerequisite PRs into `develop` before starting dependent work. Avoid stacked branches; independent work may proceed only when requested.
- Include relevant behavior tests, Ruff checks, a concise change summary, and material simplifications or deferred work in every PR.
- Stop after each PR. The designated review gates require manual inspection before proceeding.
- Keep the prompt and this planning document as the baseline on `develop` before PR 01. They are planning changes, not an additional numbered implementation PR. No commits or remote changes are performed by this plan.
- Update the README with installation, configuration, and feature usage as features arrive. Create application modules only when they contain implemented functionality.

## Implementation choices

| Concern | Choice | Reason / introduction |
| --- | --- | --- |
| Runtime | Python 3.12 initially | One tested minor version, consistent locally and in containers; PR 01. |
| Dependencies | uv, committed `uv.lock` | Reproducible installs; PR 01. |
| API | FastAPI, Pydantic 2, pydantic-settings, Uvicorn | Typed requests, responses, and `.env` configuration; PR 01. |
| Database | SQLAlchemy 2 async sessions, psycopg 3, Alembic | Shared async approach for API and worker I/O; PR 02. Alembic uses its ordinary migration connection. |
| Durable storage | PostgreSQL with pgvector | Domain records, job history, score history, and vectors; PR 02 onward. |
| Objects | MinIO locally; proposed private R2 Standard in production, boto3 | Preserve the S3-compatible boundary and verify the actual remote operations before release. |
| HTTP | httpx AsyncClient | Shared connection pools and bounded network calls; PR 08 onward. |
| Search | Serper.dev, one small provider boundary | Discovery only; underlying pages remain evidence sources; PR 09. |
| LLM | Sarvam API, `sarvam-105b`, minimal httpx wrapper | Structured relevance, extraction, hypothesis generation, and resolution; PR 12. |
| Embeddings | FastEmbed, `BAAI/bge-small-en-v1.5`, initially 384 dimensions | Separate local English embedding model; no embedding API credential. Generate in bounded thread work, store in pgvector; PR 16. |
| Background jobs | arq with Redis | Async jobs and bounded retries; PR 11. PostgreSQL retains durable research job state. |
| Scheduler | Existing arq periodic worker trigger | Approximately every 15 minutes; PR 30. Runs on the same VPS, opt-in, without a separate cloud scheduler. |
| Tests and lint | pytest, pytest-asyncio when needed, Ruff | Behavior tests and small fixtures; PR 01 onward. |
| CI | GitHub Actions | Locked install, Ruff, tests; add real PostgreSQL integration checks when persistence arrives. |
| ChatGPT | MCP endpoint mounted alongside REST; supported ChatGPT app UI | Shared intelligence services. Select and pin the supported SDKs at PR 31/33 rather than scaffolding them early. |
| Admin | Bearer token from `ADMIN_API_TOKEN`; server-rendered minimal dashboard | One operator, no consumer accounts; PR 35. |
| Deployment | One DigitalOcean x86 Droplet, Docker Compose, trusted HTTPS | Bangalore 2-vCPU / 4-GiB / 80-GB class; GitHub OIDC deployment, R2 backup/isolated restore and IP-based HTTPS preview verified. Public routes limited to health/reserved MCP; external alerts deferred by user. No load balancer or managed database/Redis. |

Dependency versions are resolved and locked when introduced, rather than guessed during planning. Validate the embedding model on scam-matching fixtures at PR 16/17; change it if retrieval quality is inadequate. Model changes require compatible dimensions and deliberate backfill, never mixed incompatible vectors.

References: [uv locking](https://docs.astral.sh/uv/concepts/projects/sync/), [arq jobs and retries](https://arq-docs.helpmanual.io/), [FastEmbed quickstart](https://qdrant.tech/documentation/fastembed/fastembed-quickstart/), [Sarvam structured chat API](https://docs.sarvam.ai/api-reference/chat/chat-completions-v2), [Serper](https://serper.dev/).

## Architecture and domain decisions

One codebase serves REST, MCP, worker functions, and scheduling. Acquisition, extraction, evidence attachment, and scoring use ordinary functions and small modules. REST and MCP call the same intelligence services; neither assembles a second research pipeline.

```text
REST / MCP
    -> intelligence and research services
        -> PostgreSQL: locations, source/document lineage, claims,
                       hypotheses, evidence, scores, research state/jobs
        -> Serper -> bounded HTTP fetch -> MinIO/S3 raw version
        -> cleaning -> Sarvam structured extraction
        -> local embeddings -> pgvector candidates -> Sarvam resolution
        -> deduplication -> independence -> deterministic scoring
        -> Redis/arq: background enrichment and refresh
```

### Provenance and identity

- Use UUID identifiers, foreign keys, UTC timestamps, explicit uniqueness constraints, and reversible Alembic migrations where practical.
- A Source represents an article, thread, or traveler report. A Document represents an acquired immutable version, with its own hash, retrieval time, and raw object reference.
- Re-fetching unchanged content is idempotent. Changed content produces a new version; it does not silently replace retained raw content or count as a new independent observation.
- Each Claim belongs to a Document. A traveler report produces a Source and normalized Document; preserve the UserReport link and experienced/witnessed/second-hand classification.
- A hypothesis describes one mechanism at one location. Link similar patterns across places rather than pooling them into a global hypothesis. Broader inherited advice must identify its actual observation location.
- Provisional locations carry their resolution status and available parent context. Ambiguous names remain unresolved or require clarification; do not invent coordinates or silently choose a country.
- Retain supporting, contradicting, related, and uncertain evidence. Only eligible evidence contributes to public scoring.
- Start with explicit conservative scoring components, not a statistical probability-of-truth claim. Tune weights against hand-built cases and the evaluation harness.

### Publication and reports

Anonymous users can read and submit. Submissions are treated as reports with provenance, not verified facts. Ordinary candidates remain queryable with confidence labels. Sensitive allegations are held from public results and public scoring until approved; their raw and derived records remain internally available. Early moderation flags arrive with extraction/report processing, and the operator reviews them through the PR 35 dashboard.

Public evidence responses show appropriate excerpts and provenance, never private raw attachments or unnecessary personal identifiers. Store credentials in ignored `.env`; `.env.example` contains placeholders. Protect admin operations with `ADMIN_API_TOKEN` and never log it.

### Research lifecycle and failure handling

- Fresh location: query persisted intelligence.
- Stale location: return existing intelligence and request one refresh.
- Unresearched location: claim a PostgreSQL research lease, run bounded fast research, persist completed stages, return initial results, and enqueue enrichment.
- Concurrent callers share the active location job. Return available results and explicit in-progress state; do not start duplicate research.
- Expired leases and unfinished work can be reclaimed after crashes. Queue delivery is retryable; database uniqueness and stage checkpoints protect domain records.
- Start with a configurable 25-second interactive deadline, 3 search queries, 30 ranked candidates, at most 10 fetches and 6 relevant documents, fetch concurrency 4, and LLM concurrency 2. These are benchmark starting points, not a promised SLA.
- PR 24 implements serial stage/fetch execution with a shared LLM concurrency cap (default 2); fetching stays below the benchmark concurrency ceiling. Completed scoring commits before separate embedding work. Enrichment reuses cached stages under a second bounded run, while semantic/sensitive applications keep their existing review requirements.
- Bound individual calls and stop scheduling new expensive work as the deadline approaches. Persist completed outputs and leave unfinished stages for background continuation.
- Successful research with no relevant findings is READY with zero findings and an honest message. A failed provider or unusable acquisition is not a successful empty search. Partial results include failure/incompleteness metadata.
- Introduce conservative stale eligibility before the full scheduler, configurable rather than tied to confidence. PR 30 adds demand tiers and prioritization.
- Track usage and enforce configured resource limits. Monetary caps and monetary defaults remain deferred.
- Store claim/model/prompt versions when extraction arrives, embedding model/revision when embeddings arrive, and scoring versions when scoring arrives.

The partial-result contract is implemented at PR 24 and finalized through PR 27/28. At PR 31, validate actual ChatGPT MCP timeout behavior. If the host cannot accommodate fast research, return current results plus research-in-progress metadata immediately and provide a later status/result read; never wait for deep enrichment.

Update — 2026-11-08: the user requires one request to a sourced report without
manual “check again” prompts. Individual MCP calls remain short, but PR 32 adds
bounded automatic waiting and PR 33 adds an automatically updating progress/report
view, with narrative completion tested separately in ChatGPT. The detailed
[automatic research report plan](automatic-research-reports.md) supersedes the
earlier assumption that the conversation must end before enrichment finishes.
Research budgets remain unchanged; a five-to-ten-minute watch is not additional
paid research. Host limitations and terminal partials must remain explicit.
Also separate research progress from freshness and stop reasons under the
[research state semantics plan](research-state-semantics.md). PR 32 includes the
state/contract migration, legacy adapters and admission/scheduler regression checks;
PR 33 displays the corrected model. Newly collected partial results must not be
labeled stale solely because research stopped incomplete.

## PR-by-PR sequence

Every row implies a distinct branch `codex/pr-NN-<slug>` and PR targeting `develop`. A gate marks mandatory manual review after that PR.

| PR | Branch slug | Concrete scope | Acceptance evidence / gate |
| --- | --- | --- | --- |
| 01 | repository-foundation | Minimal running API, settings, request IDs, JSON logs, errors, uv, Ruff, pytest, CI, README. | Clean install, successful lint/tests and live health request. |
| 02 | local-infrastructure | Compose API/Postgres/Redis/MinIO, Dockerfile, clients, Alembic setup, pgvector, readiness checks. | One-command startup; readiness fails clearly when a required local dependency is unavailable. |
| 03 | locations-and-sources | Hierarchical Location, aliases and provisional status; Source identity; required seed locations and lookup. | Alias resolution, parent integrity, ambiguous-name behavior, source persistence. |
| 04 | documents-and-provenance | Document versions, hashes, raw object references, basic persistence. | Every document retains a recoverable source; repeat/changed versions behave correctly. |
| 05 | claims-and-hypotheses | Claims, location-specific hypotheses, status and vector columns. | Valid domain persistence and constraints; embeddings may remain absent until PR 16. |
| 06 | evidence-and-relations | Claim-to-hypothesis evidence, stance and relationships. | No self-relations or duplicate attachments; contradictions preserved. |
| 07 | reports-and-domain-integrity | UserReport-to-normalized-Document linkage, DuplicateGroup and score history entities. | Complete provenance graph, deletion safeguards and report lineage. **Gate: schema review.** |
| 08 | ingestion | URL normalization, secure bounded fetch, host limits, hashes, immutable raw objects, idempotent persistence. | Repeat URL ingestion, changed version, redirect/private-address rejection, timeout and partial storage failures. |
| 09 | serper-discovery | Serper provider, deterministic English queries, rank/dedupe/filter, usage counts. | Provider fixtures and controlled real search; request Serper key here. |
| 10 | document-processing | Main-text cleaning, language/metadata extraction, English filtering and exact duplicates. | Explicit page fixtures; retained raw data; blocked or empty pages stay distinguishable. |
| 11 | async-workers | arq worker, ingestion/processing jobs, minimal durable ResearchJob, retry/recovery state. | Restart/retry does not duplicate domain data; failure state remains inspectable. |
| 12 | sarvam-relevance | Sarvam structured wrapper, bounded passage classification, output validation and model metadata. | Valid/invalid/refused/rate-limited outputs; controlled real call; request Sarvam key here. |
| 13 | claim-extraction | Strict claim schema, passage lineage, extraction versions, persistence and sensitivity flags. | Claims stay grounded in input passages; malicious source instructions do not direct application operations. |
| 14 | location-resolution | Aliases, parent-aware resolution, provisional creation and unresolved text; minimal entity normalization. | Known, unseen and ambiguous geography; no geocoding dependency. |
| 15 | search-to-claims | Connect existing discovery, acquisition and extraction functions in a bounded research path. | Controlled real destination research and deterministic provider-boundary integration tests. **Gate: traceable useful claims.** |
| 16 | embeddings-and-retrieval | FastEmbed generation, model metadata, geographic vector search and backfill command. | Retrieval fixtures, correct dimensions and repeatable backfill; local CPU latency measurement. |
| 17 | hypothesis-resolver | Candidate retrieval, deterministic matches, Sarvam SAME/RELATED/SUBTYPE/CONTRADICTS/NEW decisions and ambiguity records. | False-merge fixtures and invalid-decision safeguards. **Gate: resolver inspection.** |
| 18 | hypothesis-lifecycle | Candidate generation, observed dates, provenance-preserving merge, split primitives and relationship queries. | Evidence survives merges/splits; ambiguous destructive operations require explicit review. |
| 19 | duplicate-detection | URL/hash/author/thread/quotation/similarity signals and duplicate groups. | Copies discounted without treating similar independent incidents as identical. |
| 20 | evidence-independence | Deterministic independence components and recalculation. | Same author/thread/copy cases behave consistently; unknown identity does not imply independence. |
| 21 | evidence-weight | Source quality, provenance class, moderation eligibility and explainable evidence weight. | Deterministic weights; held/rejected evidence excluded from public aggregation. |
| 22 | confidence-engine | Diminishing-return corroboration, contradiction handling, components and versioned snapshots. | Hand-built independent/copy/contradiction scenarios. **Gate: scoring review.** |
| 23 | recency-and-frequency | Date semantics, 30/90/365-day windows, weighted counts, decay and trends. | Missing occurrence dates handled transparently; observed frequency never called population incidence. |
| 24 | on-demand-research | Research state, location lease, bounded fast pipeline, stale response, partial results and enrichment continuation. | Concurrent first queries, deadlines, crashes, stale results, successful empty results and provider failures. **Gate: unseen-location flow.** |
| 25 | report-pipeline | Anonymous report endpoint, normalized document, PII handling, extraction/resolution/evidence/scoring. | Retry-safe submissions; user and external evidence converge. **Gate: report integration.** |
| 26 | media-and-moderation | Validated private attachments, report states and sensitive allegation hold. | File limits/type validation; held content absent from public responses and scores. |
| 27 | intelligence-services | Shared query/matching/evidence/location services with research metadata and parent inheritance. | Stable compact output contracts, public eligibility and provenance labels. **Gate: contracts review.** |
| 28 | public-rest | Versioned endpoints, filters, pagination and consistent errors using PR 27 services. | HTTP integration tests, useful OpenAPI and explicit research-status contract. |
| 29 | emerging-patterns | Growth/novelty/geographic signals, historical comparisons and publication review queue. | Dataset novelty distinct from objective novelty; candidates not automatically published. |
| 30 | research-scheduler | Periodic deterministic selection, demand windows, tiers, hypothesis/location research, usage limits and recovery. | Priorities, demand decay, concurrent cycles and resource limits tested; same ingestion pipeline. |
| 31 | mcp-integration | MCP read/match/report tools, shared services, research metadata and host-compatible transport. | Real ChatGPT reads, first-ever query and timeout/continuation behavior. **Gate: ChatGPT integration.** |
| 32 | mcp-quality | Tool descriptions, compact results, evidence pagination, errors, provenance and location clarification; durable report-run identity and bounded automatic request/wait/report flow. | Representative conversations remain useful without large evidence dumps; real ChatGPT single-prompt waiting/report test with host limitations recorded. No duplicate research or budget resets. |
| 33 | scam-explorer | ChatGPT scam cards, limited filters, evidence viewer and one automatically updating progress/report view. | Read flow shows confidence, recency, actual scope and research state; no manual re-prompt during supported active-view completion. Test automatic narrative separately from in-place report rendering, including five-minute delay and reconnect cases. |
| 34 | reporting-ui | Editable report confirmation, anonymous submission, optional evidence and recoverable errors. | Real ChatGPT report flow with preserved input on failure. **Gate: customer flow.** |
| 35 | admin-dashboard | Token-protected minimal UI/API, review queues, merges/splits, job inspection, reprocessing and audit records. | Operator can review held allegations/candidates and failed jobs; unauthorized operations rejected. |
| 36 | evaluation-harness | Labeled fixtures for extraction/resolution/matches/duplicates/stance; confidence and scheduler regression evaluation. | Reproducible quality metrics and false-merge tracking. **Gate: evaluation baseline.** |
| 37 | operational-hardening | Rate/size/spam controls, dependency checks, useful metrics, load checks and measured optimizations. | Critical controls verified; only demonstrated bottlenecks justify caches/index tuning. |
| 38 | single-vps-mvp-release | Minimal VPS provisioning, production Compose, Caddy TLS, private S3-compatible objects, local PostgreSQL/Redis, backups and release. | Small-machine memory/latency validation, migration/deployment/restore checks, popular/unseen destination traceability, frozen contracts and release tag. **Gate: production release.** |

## Intermediate research repair PRs after PR 31

User-authorized on 2026-11-06. PR 31 is merged; create each intermediate branch from
latest `develop`, target `develop`, and merge its prerequisite before starting the
next. The original 01–38 roadmap remains numbered as before; PR 32 waits for 31F.

| PR | Branch suffix | Scope / gate |
| --- | --- | --- |
| 31A | research-benchmark | Reproducible labeled cold/warm baseline, stage losses, bounded provider usage; see `docs/pr31a-research-benchmark.md`. |
| 31B | grounded-location-resolution | Grounded city/landmark hierarchy and provisional records; ambiguous/conflicting places remain unresolved. |
| 31C | grounded-extraction-quality | Measure field/quote failures and improve extraction without weaker grounding or moderation. |
| 31D | source-acquisition-quality | Failure-aware source diversity and acquisition within existing resource/security bounds. |
| 31E | research-continuation | Durable passage/document progress, version-valid reuse and bounded cumulative retries. |
| 31F | research-acceptance | Controlled and live traceable results plus real ChatGPT retest; honest incomplete/empty controls. |
| 31G | research-quality-repair | User-requested repair before PR 32: bounded empty/invalid-entity audit, grounded location prefixes, retained holds; frozen quality gate and replay. |

Branch names use `codex/pr-31a-research-benchmark` and the analogous 31b–31f prefix.
PR 31A is merged. PR 31B's grounded qualified-place policy and measured limits are
documented in `docs/pr31b-grounded-location-resolution.md`; its source-derived
provisional POIs do not bypass moderation or broaden the research target's scope.
PR 31B is merged. PR 31C's extraction experiments, rejected candidates, versioned
cache behavior and retained safety rules are in `docs/pr31c-grounded-extraction-quality.md`.
PR 31C is merged. PR 31D's bounded parallel discovery, run-local host feedback,
measured article-intent query mix and source-access limitations are documented in
`docs/pr31d-source-acquisition-quality.md`; cross-job continuation remains PR 31E.
PR 31D is merged. PR 31E's durable query/document/passage checkpoints, cumulative
root budgets and measured prefix-stall fix are documented in
`docs/pr31e-research-continuation.md`. Live deployment/ChatGPT acceptance remains PR 31F.
PR 31E is merged. PR 31F deployed that merged release and passed live provenance,
continuation and real ChatGPT read/evidence/scope checks, but the unchanged controlled
quality targets still fail. See `docs/pr31f-research-acceptance.md` and the explicit
`accepted=false` artifact. PR 32 remains gated on repairing and rerunning those targets.
Keep Serper/Sarvam/current infrastructure. Initially retain 25/45-second execution
limits. This targeted baseline does not replace PR 36's broader evaluation harness.

Update — 2026-11-08: the user explicitly chose quality repair before PR 32.
PR 31G's selected controlled capture/replay meets the unchanged thresholds (9/9
claims, 7/7 geography, 5/6 eligible public patterns; perfect safety controls).
See `docs/pr31g-research-quality-repair.md` for unsuccessful trials, aggregate usage,
cache/hold safeguards and limits. Review/merge and live reacceptance of the repaired
release remain required; the earlier PR 31F host evidence tests the older release.

## PR 01 detailed implementation plan

Branch: `codex/pr-01-repository-foundation`. Base and target: latest `develop`.

### Files

```text
app/
    __init__.py
    main.py
    api/
        __init__.py
        routes.py
    core/
        __init__.py
        config.py
        logging.py
        errors.py
        middleware.py
tests/
    test_api.py
    test_config.py
    test_request_context.py
.github/workflows/ci.yml
.python-version
.env.example
.gitignore
pyproject.toml
uv.lock
README.md
```

Package marker files support implemented packages. Do not create empty future domain folders, migrations, worker code or provider clients.

### Behavior

1. Declare Python 3.12 and the minimal runtime/dev dependencies. Configure Ruff and pytest in `pyproject.toml`; resolve and commit the lockfile.
2. Build `create_app()` for explicit settings injection in tests. Expose an importable application for Uvicorn.
3. Load minimal settings from `.env`: environment, app name and log level. Do not require service credentials before their integration PRs.
4. Return application identity from `GET /`; return a small `{"status":"ok"}` response from `GET /health`. Health means the process is alive. Dependency readiness arrives in PR 02.
5. Generate a request ID, accept only a bounded valid incoming `X-Request-ID`, return it in the response, and include it in request logs/errors. Keep concurrent request context isolated and reset it afterward.
6. Emit JSON operational logs through standard-library logging. Include method, path, status, duration and request ID. Do not log authorization headers, request bodies, credentials or raw query strings.
7. Use one concise error envelope with code, message and request ID. Cover validation errors, HTTP errors and unexpected failures; unexpected responses must not reveal exception details. Add domain-specific errors later when real domains require them.
8. Ignore `.env`, virtual environments, local caches and generated artifacts. Commit `.env.example` placeholders for current settings.
9. Add a small GitHub Actions workflow for Python 3.12: locked dependency installation, Ruff lint/format checks and pytest. No database service yet.
10. Update README with install, configure, start, lint and test commands.

### Verification

- Test root/health responses, settings precedence, useful validation errors, hidden unexpected-error details, request ID handling and concurrent request isolation.
- Check JSON request logging and confirm the selected error path retains its request ID without exposing sensitive input.
- Run `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest`.
- Start `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` and make one real local health request.
- Review the diff for accidental credentials, future scaffolding and unrelated changes. Summarize validation and stop after this PR.

No API keys, databases, Docker services, embeddings, MCP setup or AWS credentials are needed for PR 01.

## Later decisions and measurements

- PR 09: validate Serper response mapping and access to useful source pages; skip unavailable/restricted pages rather than treating snippets as strong evidence.
- PR 12/15: measure Sarvam structured-output reliability and latency on actual passages. Do not promise a 10–30 second complete research result before measurements.
- PR 16/17: test the initial local embedding model against scam mechanism and geography fixtures, and benchmark warm/cold performance. Preload it before serving matching requests.
- PR 22: finalize confidence components and labels through scenario review; no arbitrary truth probabilities.
- PR 24/30: tune work limits, stale intervals and scheduler weights from observations. Monetary caps remain a later decision.
- PR 31/33: confirm current ChatGPT MCP/UI requirements and request timing before finalizing those integrations.
- PR 38: use DigitalOcean; confirm region, plan/tax, off-server storage, measured sizing and research-cost policy. Address the sizing experiment's media-memory and query/concurrency findings before release. First test trusted HTTPS at the VPS IP with automated IP-certificate renewal and a live ChatGPT MCP connection; IP URL acceptance is unverified. Defer buying a domain unless that compatibility test requires a hostname. Use the minimal deployment decision instead of the original AWS topology.
