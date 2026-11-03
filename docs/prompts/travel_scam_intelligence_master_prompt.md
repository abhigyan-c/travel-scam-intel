# Travel Scam Intelligence
## Master Implementation Prompt for Codex

Read this entire document before changing code.

## Deployment Override — 2026-10-31

The user has replaced the full AWS release target with a minimal product for about
100 requests/day: one inexpensive Linux VPS, no separate load balancer and no
mandatory managed database/Redis services. Preserve the existing application and
evidence architecture. The proposed Docker Compose/Caddy/private-R2 deployment,
provider comparison, prerequisites and revised release sequencing are documented in
[`docs/minimal-mvp-deployment.md`](../minimal-mvp-deployment.md).
OVHcloud is the user-selected hosting provider. Region, purchase/billing terms,
storage choice and the proposed sequencing still require discussion.
This override takes precedence over remaining historical AWS examples. PR 31 is
deferred until an HTTPS preview is available; no deployment or purchase is authorized
by this document alone.

## Confirmed Project Decisions — 2026-10-11

These decisions take precedence over conflicting examples elsewhere in this document.

- Plan all 38 PRs, with detailed implementation planning beginning at PR 01. Implement one numbered PR at a time and preserve the review gates.
- Create each PR branch from the latest `develop` and target `develop`. Use `codex/pr-NN-short-description`, for example `codex/pr-01-repository-foundation`.
- Use Serper.dev for search discovery. Request its API key when real integration validation requires it.
- Store local credentials in an untracked `.env`; commit only placeholder values in `.env.example`.
- Replace OpenAI inference with Sarvam 105B (`sarvam-105b`) for relevance classification, structured claim extraction, hypothesis generation, and semantic resolution.
- Use a separate embedding model/provider to generate vectors stored in pgvector. Its selection remains open for implementation planning; Sarvam chat inference is not the embedding dependency.
- Research English-language content initially. Create provisional records for previously unknown locations and preserve unresolved or ambiguous geography.
- Keep hypotheses location-specific and link related patterns across locations rather than combining their evidence into one global hypothesis.
- A Source represents an individual article, thread, or report; Documents represent acquired versions of that source. Normalize traveler reports into Documents while retaining their UserReport lineage.
- Expose ordinary low-confidence candidates with clear labels. Exclude sensitive allegations from public results and public scoring until approved through the admin dashboard. Keep them pending until that dashboard is available in PR 35.
- Public reads and report submissions are anonymous. Protect admin operations with one admin token configured through `.env` initially.
- Return partial intelligence and research status when interactive research reaches its limit or the location is already being researched. Successful research with no findings must say no reports were found in the sources checked; never invent scams. Distinguish provider failure from no findings.
- Defer monetary spending caps and their numerical defaults for now. Retain bounded per-request/per-job work, usage tracking, and configurable scheduler resource limits; do not block planning on a daily spending cap.
- Choose routine tooling during planning. Create modules as their functionality arrives, rather than scaffolding empty future modules. Introduce essential fetch protections with ingestion and processing-version metadata with the relevant processing features.

You are implementing the backend and ChatGPT integration for a travel-scam intelligence system.

The system converts public travel content and traveler reports into an evidence-backed database of tourist scam patterns. It must preserve provenance, distinguish claims from facts, discount duplicated/correlated reporting, maintain confidence separately from prevalence and recency, and improve its knowledge base over time.

The project is being built by one developer. Optimize for simplicity, clarity, low operational burden, easy local development, and easy modification.

Do not attempt to build the whole system at once.

The implementation is divided into **38 pull requests**. Treat each numbered PR below as exactly one PR.

When asked to implement a PR:

1. Implement only that PR.
2. Run relevant tests.
3. Run linting.
4. Briefly summarize the change.
5. Mention important simplifications or deferred work.
6. Stop.

Do not automatically begin the next PR.

---

# 1. Product Objective

The product answers questions such as:

- What scams are commonly reported in Bangkok?
- What should I watch out for near the Grand Palace?
- Is this incident I just experienced similar to a known scam pattern?
- Which scam patterns have started appearing recently in Barcelona?
- Are there recent reports around a specific neighborhood, attraction, airport, station, or tourist area?

The durable asset is not a chatbot answer. It is a structured intelligence graph built from evidence.

```text
Location
   ↓
Hypothesis
   ↓
Evidence
   ↓
Claim
   ↓
Document
   ↓
Source
```

A statement found online is not automatically treated as fact.

```text
source material
      ↓
claim
      ↓
existing hypothesis?
   /             \
 yes             no
  ↓               ↓
evidence      candidate hypothesis
   \             /
    ↓           ↓
      corroboration
           ↓
      confidence
```

Multiple independent reports can increase confidence.

Copied, syndicated, quoted, or highly correlated reports should contribute much less than independent evidence.

Contradictory evidence must be preserved.

Low-confidence hypotheses remain queryable rather than being silently discarded.

The system should accumulate intelligence over time rather than merely generate one-off summaries.

---

# 2. Core Product Principles

The durable asset is:

```text
Location
    ↓
Hypothesis
    ↓
Evidence
    ↓
Provenance
    ↓
Historical confidence
```

Important distinctions:

```text
mention count != independent evidence count
confidence != prevalence
confidence != recency
confidence != novelty
new to our database != objectively new scam
```

A hypothesis can:

```text
gain evidence
lose confidence
be contradicted
split
merge
become a subtype
become inactive
become disputed
expand geographically
```

Design accordingly.

---

# 3. Global Coverage Strategy: Do Not Precompute the World

Do **not** attempt to pre-research every country, city, neighborhood, attraction, airport, or station in advance.

The world is too large, most locations will be queried rarely, and precomputing everything would waste Search API calls, Sarvam/embedding usage, storage, and developer effort.

The system should use **lazy/on-demand intelligence generation with caching and background enrichment**.

Conceptually:

```text
User asks about a location
          │
          ▼
Check existing intelligence
          │
   ┌──────┼──────────┐
   │      │          │
 NONE    FRESH      STALE
   │      │          │
   ▼      ▼          ▼
Fast     Return     Return existing
research DB         intelligence
   │                 +
   │              queue refresh
   ▼
Initial answer
   │
   ▼
Deep enrichment in background
```

A first-ever query for a niche destination such as Fiji should trigger real research.

The second traveler benefits from the research performed for the first traveler.

We are not caching an answer. We are caching structured research, evidence, hypotheses, provenance, and score history.

---

# 4. Research States

Each location should eventually have a small research-state record.

Use simple states such as:

```text
NEVER_RESEARCHED
RESEARCHING
READY
STALE
FAILED
```

A reasonable conceptual model is:

```text
LocationResearchState

location_id
status
last_started_at
last_completed_at
last_successful_research_at
next_eligible_at
research_depth

document_count
relevant_document_count
hypothesis_count

queries_24h
queries_7d
queries_30d
```

Do not add every field immediately. Add them when the relevant PR requires them.

Research depth can remain simple:

```text
INITIAL
ENRICHED
```

The API/MCP layer should be able to communicate whether intelligence is initial, enriched, stale, or currently being refreshed.

---

# 5. Fast Research vs Deep Research

Separate interactive research from deeper background enrichment from the beginning.

## Fast Research

Fast research exists to make the first query for a previously unseen location useful.

Target conceptually:

```text
~10–30 seconds total interactive budget
```

This is an engineering target, not a guaranteed SLA. Benchmark the actual Search API, sites, models, and infrastructure before making user-facing promises.

Fast research should be intentionally bounded, for example:

```text
small number of broad search queries
~10–20 fetched documents
relevance filtering
structured claim extraction
initial hypothesis matching/creation
initial scoring
```

The system should stop once its interactive budget is exhausted and return the best intelligence available so far.

Do not wait for exhaustive research.

## Deep Research

Deep research runs asynchronously after the initial answer.

It may perform:

```text
more search queries
more document fetching
hypothesis-specific corroboration searches
contradiction searches
city/area-specific searches
additional deduplication
confidence recalculation
emerging-pattern analysis
```

A first-time location may take roughly minutes to become richly enriched, but ChatGPT should not wait for that full process.

The intended lifecycle is:

```text
First location query
        │
        ▼
FAST RESEARCH
        │
        ├── initial intelligence returned to ChatGPT
        │
        ▼
DEEP RESEARCH
background workers
        │
        ▼
enriched database
        │
        ▼
future requests are fast
```

---

# 6. Interactive Latency Strategy

Do not design normal MCP calls to wait several minutes.

The intended target behavior is:

```text
Already researched + fresh
→ query PostgreSQL
→ backend target under ~1 second

Researched but stale
→ return current DB intelligence immediately
→ queue refresh

Never researched
→ bounded fast research
→ target roughly ~10–30 seconds

Deep enrichment
→ background only
→ can take minutes
```

Again, treat these as design targets rather than guarantees until measured.

Parallelize bounded work where useful:

```text
location
   │
   ├── search query 1 ─┐
   ├── search query 2 ─┼── parallel
   ├── search query 3 ─┘
   │
   ▼
deduplicate URLs
   │
   ├── fetch A ─┐
   ├── fetch B ─┼── bounded parallel
   ├── fetch C ─┘
   │
   ▼
relevant passages
   │
   ├── LLM batch A ─┐
   ├── LLM batch B ─┼── bounded parallel
   └── LLM batch C ─┘
```

Do not serialize independent network and model work unnecessarily.

Do not introduce complicated distributed orchestration just to parallelize basic async work.

---

# 7. Research Scheduler / Periodic Research Daemon

The system should have a lightweight **research scheduler**.

The scheduler should wake approximately every **15 minutes** during the MVP.

This does **not** mean every location is researched every 15 minutes.

The scheduler is cheap. Its purpose is to decide which research jobs are currently valuable and queue them for workers.

Conceptually:

```text
Every ~15 minutes
       │
       ▼
Research Scheduler
       │
       ▼
Find eligible research work
       │
       ▼
Calculate priority
       │
       ▼
Apply current budget
       │
       ▼
Queue top jobs
       │
       ▼
Workers execute jobs
```

Do not implement this as a giant infinite loop that researches the world.

Locally, a simple periodic scheduler is enough.

In AWS later, this can map cleanly to a scheduled trigger plus the existing worker infrastructure.

---

# 8. What the Scheduler Prioritizes

The scheduler prioritizes **research tasks**, not merely locations.

Research priority should be deterministic application logic, not an LLM decision.

Use understandable signals such as:

```text
priority =
    demand
  + staleness
  + uncertainty
  + recent activity
  + research incompleteness
```

Exact weights should be configurable and changed only after observation/evaluation.

A simple starting concept might be:

```text
35% demand
25% staleness
20% uncertainty
15% recent activity
 5% research incompleteness
```

Do not treat those percentages as sacred.

Priority overrides are allowed where obvious:

```text
first-time fast research
→ immediate, user-triggered, highest urgency

first-time deep enrichment
→ high priority

new low-confidence hypothesis
→ high priority

popular stale location
→ high priority

normal refresh
→ medium priority

cold location with no demand
→ low/no proactive priority
```

---

# 9. Demand Signal

Do not use lifetime query count as the main demand signal.

Demand should decay over time.

Track recent windows such as:

```text
queries last 24 hours
queries last 7 days
queries last 30 days
```

Recent demand should matter much more than old historical demand.

Example:

```text
Bangkok
Today: 420
7 days: 2,800
30 days: 11,000
→ hot
```

```text
Fiji
Today: 2
7 days: 19
30 days: 65
→ normal
```

```text
Very rarely queried destination
Today: 0
7 days: 0
30 days: 1
→ cold
```

Do not spend proactive research budget on cold locations unless another signal justifies it.

---

# 10. Refresh Eligibility

Do not assign one fixed global refresh frequency.

Use broad refresh tiers or minimum eligibility windows.

A reasonable starting concept is:

```text
HOT
high demand or rapidly changing
→ eligible every ~6–12 hours

ACTIVE
regular demand
→ eligible every ~1–3 days

NORMAL
occasional demand
→ eligible every ~1–2 weeks

COLD
rarely requested
→ no proactive refresh

DORMANT
no meaningful recent demand
→ research again only when queried
```

Being eligible does not mean the scheduler must research that location immediately.

It only means the scheduler may consider it.

Actual execution still depends on priority and budget.

---

# 11. Activity and Uncertainty Signals

Recent research findings should influence future research priority.

If a location suddenly produces many new claims or hypotheses, temporarily research it more aggressively.

Example:

```text
normal state
→ refresh every ~1–3 days

latest research discovers unusual burst
→ activity spike
→ eligible again sooner

activity returns to baseline
→ normal cadence resumes
```

Uncertainty can include:

```text
low-confidence hypothesis
new single-source hypothesis
meaningful contradictory evidence
ambiguous hypothesis match
possible duplicate contamination
new geographic variant
sudden evidence change
```

Research should be directed toward places where more evidence has high information value.

---

# 12. Location Research vs Hypothesis Research

The scheduler should eventually support two types of research jobs.

## Location Research

Broadly asks:

```text
What scam patterns are currently being reported in this location?
```

Example queries:

```text
Fiji tourist scams
Fiji travel scams
Fiji tourist traps
Fiji taxi scams
Fiji tourist fraud
Fiji scams Reddit
Nadi tourist scams
Suva tourist scams
```

Location research helps discover things the system does not yet know.

## Hypothesis Research

Asks:

```text
Is this specific hypothesis corroborated, contradicted, geographically broader, or more recent than we currently believe?
```

Example hypothesis:

```text
Fake tour operators request deposits from tourists in Fiji.
```

Possible corroboration queries:

```text
Fiji fake tour operator scam
Fiji tour deposit scam
Nadi fake tour booking
Fiji tourist fake excursion
```

Conceptually:

```text
Research Scheduler
       │
   ┌───┴─────────────┐
   ▼                 ▼
Location          Hypothesis
Research          Research
   │                 │
   └───────┬─────────┘
           ▼
       Search API
```

Do not create two separate ingestion pipelines. Both research types must feed the same Search → Fetch → Document → Claim → Evidence → Hypothesis pipeline.

---

# 13. Research Budget

Automated research must be budget-constrained.

Otherwise the system can burn Search API and Sarvam/embedding credits indefinitely.

Support configurable budgets such as:

```text
per scheduler cycle
- max research jobs
- max search queries
- max fetched documents
- max documents sent to LLM processing

daily
- max search queries
- max fetched documents
- max LLM/embedding usage (monetary caps deferred)
```

Do not hardcode expensive assumptions.

The scheduler's real question should be:

```text
What are the highest-value research tasks I can execute within the remaining budget?
```

not:

```text
What locations have not been crawled today?
```

---

# 14. Research Job Model

Use a simple research-job record when this functionality is implemented.

Conceptually:

```text
ResearchJob

id
job_type
location_id
hypothesis_id nullable
priority
status
created_at
started_at
completed_at

search_queries_used
documents_discovered
documents_processed
llm_usage
```

Do not add a generic workflow engine.

Do not create a DAG framework.

Jobs should be idempotent enough that retries do not casually duplicate data.

---

# 15. Geographic Inheritance

Locations should be hierarchical.

Example:

```text
Fiji
 ├── Nadi
 ├── Suva
 └── Lautoka
```

and:

```text
Thailand
   ↓
Bangkok
   ↓
Grand Palace
```

When a user asks about a highly specific place with limited direct research, the answer may combine:

```text
specific-location intelligence
+
relevant parent-location intelligence
```

while optionally triggering deeper research for the specific location.

Do not duplicate every country-level hypothesis into every child location.

Preserve the distinction between where evidence was actually observed and where broader advice may still be relevant.

---

# 16. Search API Is a First-Iteration Dependency

For a meaningful end-to-end MVP, the system requires search and LLM APIs plus a separate embedding model/provider:

```text
Search API
Sarvam API + separate embedding model/provider
```

Everything else should run locally.

The Search API is not the evidence source. It is the **discovery mechanism**.

Correct model:

```text
Search API
   │
   │ discovers
   ▼
actual webpage / Reddit thread / news article / advisory / blog
   │
   ▼
Source + Document
```

Do not store Search API as the provenance source for a document unless the search result itself is genuinely the content being analyzed.

The source should represent the actual underlying page/platform.

---

# 17. Search Provider Boundary

A small search-provider abstraction is justified because the provider may change.

Keep it tiny.

For example:

```python
class SearchProvider(Protocol):
    async def search(
        self,
        query: str,
        limit: int = 10,
    ) -> list[SearchResult]:
        ...
```

and:

```python
class SearchResult(BaseModel):
    url: str
    title: str | None
    snippet: str | None
```

Do not create:

```text
SearchProviderFactory
SearchProviderRegistry
SearchStrategyManager
SearchOrchestrationEngine
```

unless later requirements genuinely justify them.

One configured provider is enough for the MVP.

Keep credentials/settings behind simple environment configuration.

---

# 18. Search Result Prioritization

Search results provide useful metadata before full fetching:

```text
title
URL
snippet
domain
```

Use cheap filtering/ranking to decide which pages deserve fetching and LLM processing.

Prefer:

```text
50 search results
      ↓
cheap ranking/filtering
      ↓
top ~10–20 promising URLs
      ↓
fetch + extract
```

instead of:

```text
50 search results
      ↓
fetch everything
      ↓
send everything to LLM
```

This reduces latency and cost.

Do not treat snippets as strong evidence by themselves when the underlying page can be fetched.

---

# 19. Source Acquisition vs Intelligence

Keep source acquisition separate from intelligence extraction.

The intelligence system should not care whether a document came from:

```text
Search API discovery + HTTP fetch
official API
manual import
user report
uploaded file
future source adapter
```

Everything eventually becomes normalized source/document data.

Do not tightly couple the knowledge model to Google, Reddit, TripAdvisor, Quora, or any other individual source.

Respect applicable source access restrictions and robots/terms where relevant.

---

# 20. Development Philosophy

This project is being built by one developer.

Optimize for:

```text
simplicity
clarity
low operational burden
easy debugging
easy local development
easy modification
```

Use a modular monolith.

Do not create microservices.

---

# 21. Coding Style — VERY IMPORTANT

Keep the implementation deliberately simple.

## Prefer the smallest implementation that works

Do not add abstractions unless the current PR actually requires them.

Prefer:

```text
small functions
small services
plain SQLAlchemy models
plain Pydantic schemas
straightforward repository queries
simple modules
```

Avoid unnecessary:

```text
factories
registries
managers
orchestrators
generic frameworks
deep inheritance
dependency-injection frameworks
plugin frameworks
event buses
abstract base classes with one implementation
```

If:

```python
if provider == "sarvam":
    ...
```

is sufficient today, do not build a generalized provider registry unless the current PR needs multiple providers.

Repeated code is acceptable when removing it would create a premature abstraction.

Future PRs may refactor earlier code when a real requirement appears.

Do not build extension points "just in case."

Do not scaffold later PRs.

Do not create empty modules for planned functionality.

Do not add TODO-heavy placeholder architectures.

Do not add database fields purely because they might be useful someday.

Implement what is required now.

---

# 22. Minimal Code Documentation

Do not add verbose documentation inside code.

Do not add a docstring to every function or class.

Do not write comments explaining obvious code.

Comments are appropriate only for:

```text
non-obvious constraints
external-service behavior
security requirements
important scoring/math decisions
surprising implementation choices
```

Do not narrate code.

---

# 23. Minimal Repository Documentation

Do not create documentation files unless explicitly requested.

Prefer updating:

```text
README.md
```

instead of creating many architecture/design markdown files.

README changes should be concise and practical.

Documentation should mainly explain how to:

```text
install
run
test
configure
use a feature
```

---

# 24. Naming and Module Structure

Use plain names.

Prefer:

```text
extract_claims
resolve_hypothesis
calculate_confidence
fetch_document
normalize_location
schedule_research
calculate_research_priority
```

Avoid verbose names such as:

```text
ClaimExtractionOrchestrator
HypothesisResolutionManager
ConfidenceComputationEngineFactory
ResearchPriorityCoordinationService
```

A sufficient module layout is:

```text
app/
    api/
    core/
    db/
    models/
    schemas/
    ingestion/
    extraction/
    hypotheses/
    evidence/
    scoring/
    intelligence/
    research/
    submissions/
    workers/
    integrations/

tests/
migrations/
scripts/
```

Do not fragment tiny classes across deep folder trees.

---

# 25. Database Philosophy

PostgreSQL is the primary durable data store.

Use:

```text
normal relational tables
foreign keys
constraints
indexes
JSONB where flexibility is genuinely useful
pgvector for embeddings
```

Do not implement:

```text
EAV models
generic graph databases
custom database frameworks
database triggers unless clearly necessary
premature denormalization
```

PostgreSQL remains the source of truth.

Redis is not the durable intelligence database.

---

# 26. Testing Philosophy

Test meaningful behavior.

Use:

```text
unit tests
integration tests
small explicit fixtures
```

Do not:

```text
mock every object
test private implementation details
create enormous fixture frameworks
pursue arbitrary 100% coverage
```

Particularly important logic:

```text
hypothesis matching
duplicate detection
independence calculation
confidence calculation
location resolution
provenance
research priority
research budgeting
research idempotency
```

---

# 27. Dependencies, Errors, Logging and Performance

Avoid unnecessary packages.

Before adding a dependency, ask whether the standard library or an existing dependency is sufficient.

Use a small number of meaningful application errors.

Do not create a huge exception hierarchy.

Use structured operational logs.

Useful fields include:

```text
request_id
document_id
hypothesis_id
research_job_id
location_id
source
operation
status
```

Never log:

```text
API keys
passwords
database credentials
authentication tokens
unnecessary PII
```

Correctness first.

Do not add caching, batching, parallelism, custom indexing, or complex queues until the relevant PR requires them.

When bounded parallelism becomes necessary for interactive research, implement it simply.

---

# 28. Primary Technology Stack

Use:

```text
Python 3.12+
FastAPI
Pydantic
SQLAlchemy 2
Alembic
PostgreSQL
pgvector
Redis
MinIO / S3-compatible storage
pytest
Docker Compose
Sarvam API + separate embedding model/provider
Search API
MCP
```

For background jobs, choose one simple Python worker system when that PR arrives.

Do not introduce several queue libraries.

---

# 29. Local Development Architecture

Local architecture should eventually be:

```text
Laptop
│
├── FastAPI application
│   ├── REST API
│   └── MCP endpoint
│
├── background worker
├── research scheduler
├── PostgreSQL + pgvector
├── Redis
├── MinIO
│
└── External APIs
    ├── Search API
    └── Sarvam API + separate embedding model/provider
```

Docker Compose should eventually run approximately:

```text
api
worker
scheduler
postgres
redis
minio
```

The scheduler may initially run in the worker container if that keeps the implementation simpler. Do not create a separate container merely for conceptual purity.

Do not attempt to emulate AWS locally.

---

# 30. Sarvam LLM and Separate Embedding Strategy

Use Sarvam 105B as the inference model initially. Use a separate embedding model/provider for vector generation.

Keep model names in configuration.

Do not scatter model identifiers through application code.

Initial configuration:

```text
SARVAM_API_KEY=<local credential>
LLM_RELEVANCE_MODEL=sarvam-105b
LLM_EXTRACTION_MODEL=sarvam-105b
LLM_RESOLVER_MODEL=sarvam-105b
LLM_HYPOTHESIS_MODEL=sarvam-105b
EMBEDDING_MODEL=<selected during planning>
```

Use bounded Sarvam calls for structured extraction/classification.

Use Sarvam for semantic decisions where bad output can damage the knowledge graph, such as SAME vs RELATED vs SUBTYPE vs NEW. Validate outputs and preserve ambiguous decisions for review.

Use embeddings for semantic candidate retrieval.

Do not use LLMs for deterministic work such as:

```text
URL normalization
content hashing
database queries
date arithmetic
recency decay
confidence formula execution
evidence aggregation
trend calculations
research priority math
budget enforcement
pagination
rate limiting
```

Backend LLM calls should return structured output where practical and must be validated before application logic mutates the database.

---

# 31. Minimal Production Direction

Deploy the MVP on one small Linux VPS using Docker Compose. Caddy handles HTTPS on
the same server. API/MCP, worker, PostgreSQL/pgvector and Redis run together; the
existing periodic scheduler remains in the worker. Private S3-compatible object
storage and off-server database backups are proposed via Cloudflare R2.

Do not provision a load balancer, ECS/Fargate, RDS or ElastiCache for this MVP.
The server size, OVHcloud region/purchase terms and research usage policy must be confirmed against
cost and measured capacity before purchase. Keep infrastructure automation small.

Accept a single-server availability tradeoff only with working backups and a tested
restore procedure. Scaling to managed infrastructure later is optional, not an MVP
prerequisite. Do not redesign the research logic around a hosting vendor.

Core application code must continue to operate through ordinary configuration such as:

```text
DATABASE_URL
REDIS_URL
S3_ENDPOINT / bucket config
SEARCH_API_KEY
SARVAM_API_KEY
```

Do not build Kubernetes/EKS.

---

# 32. Explicit Infrastructure Non-Goals

Do not add initially:

```text
Kubernetes
EKS
Kafka
MSK
OpenSearch
Elasticsearch
DynamoDB
Neptune
dedicated vector database
service mesh
multi-region architecture
multi-cloud abstractions
consumer mobile app
consumer website
consumer account system
payments
social network
custom chatbot frontend
```

---

# 33. PR 01 — Repository Foundation

## Goal

Create the application skeleton and development standards.

## Implement

Create a minimal structure including:

```text
app/
    api/
    core/
    db/
    models/
    schemas/
    ingestion/
    extraction/
    hypotheses/
    evidence/
    scoring/
    intelligence/
    research/
    submissions/
    workers/
    integrations/

tests/
migrations/
scripts/
```

Add:

```text
pyproject.toml
README.md
.gitignore
FastAPI app
GET /
GET /health
Pydantic settings
.env.example
structured logging
request IDs
central exception handling
Ruff
pytest
```

No database implementation yet.

## Definition of Done

A clean clone can install, lint, test and start the API.

---

# 34. PR 02 — Local Infrastructure

## Goal

Create local infrastructure foundations.

## Implement

Docker Compose services:

```text
api
postgres
redis
minio
```

Add:

```text
SQLAlchemy
PostgreSQL driver
Alembic
pgvector extension
Redis client
S3-compatible storage client
Dockerfile
```

Create:

```text
GET /ready
```

Use simple environment variables such as:

```text
DATABASE_URL
REDIS_URL
S3_ENDPOINT
S3_BUCKET
S3_ACCESS_KEY
S3_SECRET_KEY
```

Do not include AWS-specific behavior.

## Definition of Done

A developer can start local infrastructure with one command.

---

# 35. PR 03 — Locations and Sources

## Goal

Create geography and source provenance.

## Location

Fields approximately:

```text
id
name
canonical_name
type
parent_id
country_code
latitude
longitude
created_at
updated_at
```

Allow hierarchy:

```text
Country
 ↓
City
 ↓
Neighborhood / Area
 ↓
Point of Interest
```

Add LocationAlias.

## Source

Fields approximately:

```text
id
platform
canonical_url
author_identifier
source_type
published_at
retrieved_at
metadata
```

Seed:

```text
Thailand
Bangkok
Grand Palace
Wat Pho
Fiji
Nadi
Suva
```

Add location lookup/search endpoints.

## Definition of Done

Locations can be nested and resolved by canonical name or alias, and sources can be persisted with provenance.

---

# 36. PR 04 — Documents and Provenance

## Goal

Represent retrieved source material.

Document fields approximately:

```text
id
source_id
url
title
raw_object_path
clean_text
language
content_hash
published_at
retrieved_at
metadata
processing_status
created_at
updated_at
```

Add simple repository operations.

Test that:

```text
Document → Source
```

always remains recoverable.

---

# 37. PR 05 — Claims and Hypotheses

## Goal

Implement central intelligence entities.

## Claim

```text
id
document_id
location_id
claim_text
normalized_claim
claim_type
occurred_at
extraction_confidence
structured_data
embedding
processing_status
created_at
updated_at
```

## Hypothesis

```text
id
primary_location_id
title
canonical_description
category
status
confidence_score
prevalence_score
recency_score
first_seen
last_seen
embedding
created_at
updated_at
```

Use pgvector.

Do not implement semantic matching yet.

---

# 38. PR 06 — Evidence and Hypothesis Relationships

## Goal

Connect claims to hypotheses.

Evidence fields approximately:

```text
id
hypothesis_id
claim_id
stance
semantic_match_score
source_quality_score
independence_score
evidence_weight
duplicate_group_id
created_at
```

Stances:

```text
supports
contradicts
related
uncertain
```

Hypothesis relationships:

```text
SAME_AS
RELATED_TO
SUBTYPE_OF
CONTRADICTS
```

Prevent self-relations.

---

# 39. PR 07 — User Reports, Score History and Domain Integrity

## Goal

Finish the initial domain model.

Add UserReport with provenance classes:

```text
FIRST_HAND
WITNESSED
SECOND_HAND
```

Add HypothesisScoreSnapshot.

Add DuplicateGroup.

Centralize genuinely shared enums.

Add integrity tests for:

```text
Location
→ Hypothesis
→ Evidence
→ Claim
→ Document
→ Source
```

## Review Gate

Stop after this PR and manually review the foundational schema.

---

# 40. PR 08 — Ingestion Framework

## Goal

Create generic source ingestion.

Implement:

```text
normalized discovery result
HTTP fetch
timeouts
bounded retries
per-host rate limiting
URL canonicalization
content hashing
raw object storage
source creation
document creation
idempotency
```

Use a small fetch boundary only where useful.

Do not create a large adapter framework.

## Definition of Done

Given a URL, the system can fetch it and persist source, raw content and a document record.

---

# 41. PR 09 — Search API and Discovery

## Goal

Make real destination research possible from a location name alone.

This PR is part of the first meaningful end-to-end iteration.

Add one configured Search API provider.

Use a tiny SearchProvider boundary.

Add:

```text
SearchResult schema
search query execution
search result normalization
URL canonicalization/deduplication
basic result filtering/ranking
search usage tracking
```

Add deterministic query generation for a location.

Examples:

```text
Bangkok tourist scams
Bangkok common scams
Bangkok tourist traps
Bangkok taxi scam
Bangkok tourist fraud
Bangkok scams Reddit
```

and:

```text
Fiji tourist scams
Fiji common scams
Fiji tourist traps
Fiji taxi scams
Fiji tourist fraud
Fiji scams Reddit
```

The Search API is a discovery service, not the evidence source.

Store the real underlying webpage/platform as the source.

Keep provider-specific code in `integrations/`.

Do not create a provider registry or multi-provider framework.

## Definition of Done

Given a location string, the system can generate searches, call the configured Search API, normalize results, deduplicate discovered URLs, and return a ranked candidate set for fetching.

---

# 42. PR 10 — Document Processing

## Goal

Convert fetched pages into useful clean text.

Pipeline:

```text
raw content
   ↓
main content extraction
   ↓
boilerplate removal
   ↓
language detection
   ↓
metadata extraction
   ↓
clean document
```

Extract when available:

```text
title
author
publication time
canonical URL
language
```

Add exact duplicate detection using hashes.

Avoid building a sophisticated crawler.

---

# 43. PR 11 — Async Workers and Research Job Foundation

## Goal

Move expensive processing out of HTTP requests and establish the minimum background job model needed by research.

Choose one simple Python worker system.

Add worker support to Docker Compose.

Support jobs for:

```text
document ingestion
document processing
research job execution
```

Add:

```text
bounded retries
failure logging
basic idempotency
basic job status
```

Create the minimal ResearchJob model needed for later research orchestration.

Do not implement the periodic scheduler yet.

Do not create distributed workflow orchestration or a DAG engine.

---

# 44. PR 12 — Sarvam Client and Scam Relevance

## Goal

Introduce AI processing.

Implement a minimal Sarvam wrapper configured for `sarvam-105b`.

Do not create a multi-provider AI framework.

Use structured output.

Create scam relevance output approximately:

```text
relevant
category
confidence
reason
```

Add simple passage chunking where required.

Avoid sending irrelevant text into expensive downstream extraction.

---

# 45. PR 13 — Structured Claim Extraction

## Goal

Convert relevant text into structured claims.

Use a strict Pydantic output model containing approximately:

```text
claim
location
approach[]
methods[]
warning_signs[]
entities[]
occurred_at
first_hand
```

Persist claims.

Preserve:

```text
Claim → Document → Source
```

Add regression fixtures.

---

# 46. PR 14 — Location and Entity Resolution

## Goal

Normalize locations extracted from claims and support hierarchical lookup.

Implement basic normalization and alias resolution.

Examples:

```text
BKK → Bangkok
Grand Palace → Bangkok → Thailand
Nadi → Fiji
```

Do not add a geocoding API.

Unknown locations remain represented as unresolved text rather than being discarded.

Standardize only basic entity fields needed now.

---

# 47. PR 15 — First Real Vertical Slice with Search

## Goal

Validate the first real end-to-end research path.

Demonstrate:

```text
"Bangkok" or "Fiji"
   ↓
query generation
   ↓
Search API
   ↓
ranked URLs
   ↓
fetch
   ↓
raw object storage
   ↓
document cleaning
   ↓
relevance classification
   ↓
claim extraction
   ↓
location resolution
   ↓
claim persistence
```

Use bounded search and fetch counts.

Create integration tests with mocked provider/network boundaries where appropriate, plus at least one controlled real manual validation.

## Review Gate

Stop after this PR.

Inspect whether a location name alone can generate useful, traceable claims without manually supplying URLs.

---

# 48. PR 16 — Embeddings and Candidate Search

## Goal

Enable semantic hypothesis retrieval.

Generate embeddings for claims/hypotheses.

Store model/version metadata.

Implement pgvector nearest-neighbor lookup with geographic filtering.

Add a basic backfill command.

---

# 49. PR 17 — Hypothesis Resolver

## Goal

Decide whether a claim describes an existing pattern.

Resolver decisions:

```text
SAME
RELATED
SUBTYPE
CONTRADICTS
NEW
```

Pipeline:

```text
claim
 ↓
geographic filter
 ↓
pgvector candidates
 ↓
obvious deterministic matches
 ↓
semantic/LLM comparison
 ↓
decision
```

Low-confidence ambiguous decisions should be reviewable.

## Review Gate

Stop after this PR. False merges are especially dangerous.

---

# 50. PR 18 — Hypothesis Lifecycle

## Goal

Allow hypotheses to evolve safely.

Implement:

```text
candidate defaults
title/canonical description/category generation
first_seen/last_seen updates
merge operation
split primitives
relationship queries
```

Merging must not destroy provenance.

Keep implementation simple.

---

# 51. PR 19 — Duplicate Detection

## Goal

Prevent repeated copies from appearing as independent reports.

Use signals such as:

```text
same canonical URL
same content hash
same normalized claim
same author
same thread
high embedding similarity
large quotation overlap
cross-post similarity
```

Create duplicate groups.

Do not attempt perfect plagiarism detection.

---

# 52. PR 20 — Evidence Independence

## Goal

Estimate whether reports represent independent observations.

Consider:

```text
duplicate group
author identity
discussion/thread identity
content overlap
explicit reference/quotation
platform
publication timing
```

Calculate a deterministic score from 0 to 1.

Persist important component signals.

Allow recalculation.

---

# 53. PR 21 — Source Quality and Evidence Weight

## Goal

Represent how useful an evidence item is.

Inputs may include:

```text
source type
first-hand / witnessed / second-hand
authorship metadata
publication metadata
moderation status
official advisory status
```

Calculate deterministic source quality and evidence weight.

Do not ask an LLM for an arbitrary truth score.

---

# 54. PR 22 — Confidence Engine

## Goal

Estimate strength of corroboration for each hypothesis.

Consider:

```text
independent supporting evidence
weighted evidence
source diversity
semantic agreement
location agreement
contradictory evidence
```

Use diminishing returns.

1 → 5 independent reports should matter much more than 101 → 105.

Generate both a numeric internal score and an explanation/components object.

Persist snapshots.

All calculations should be deterministic.

## Review Gate

Stop and inspect scoring behavior with hand-built scenarios.

---

# 55. PR 23 — Recency, Frequency and Prevalence

## Goal

Measure how current and how commonly observed a pattern is.

Implement:

```text
time decay
raw report count
independence-weighted report count
recency score
prevalence score
```

Support basic windows such as:

```text
30 days
90 days
365 days
all time
```

Add simple trend classifications.

Do not present observed report frequency as population incidence.

---

# 56. PR 24 — On-Demand Research Orchestration

## Goal

Implement the central lazy research behavior for unseen and stale locations.

Add the minimal LocationResearchState needed to support:

```text
NEVER_RESEARCHED
RESEARCHING
READY
STALE
FAILED
```

Implement a research service that decides:

```text
FRESH
→ return DB intelligence

STALE
→ return DB intelligence
→ queue refresh

NEVER_RESEARCHED
→ trigger bounded fast research
→ return initial intelligence
→ queue deep enrichment
```

Fast research must reuse the existing Search API, ingestion, processing, extraction, resolver, evidence and scoring pipeline.

Do not create a parallel pipeline.

Support configurable interactive limits such as:

```text
max_search_queries
max_search_results
max_documents_to_fetch
max_relevant_documents
max_llm_concurrency
max_total_seconds
```

Exact defaults should be conservative and easy to tune.

If the budget/time limit is reached, return partial successfully processed intelligence and continue via background work.

Expose enough state so MCP/API consumers can know whether research is initial, enriched, stale, or still in progress.

## Review Gate

Test a never-before-researched location such as Fiji end to end.

---

# 57. PR 25 — User Report Pipeline

## Goal

Allow traveler reports to enter the same intelligence system.

Add:

```text
POST /v1/reports
```

Pipeline:

```text
submission
 ↓
basic spam/safety/PII handling
 ↓
location resolution
 ↓
claim extraction
 ↓
hypothesis resolution
 ↓
existing hypothesis OR candidate hypothesis
 ↓
evidence
 ↓
score update
```

Novel reports may trigger hypothesis research later.

## Review Gate

Verify external source evidence and user evidence converge into the same hypothesis model.

---

# 58. PR 26 — User Media and Moderation

## Goal

Allow optional evidence attachments safely.

Use object storage.

Support upload validation and report states:

```text
pending
processed
needs_review
accepted
rejected
```

Flag sensitive claims involving named businesses or people for review.

Do not automatically publicly surface sensitive allegations without review.

---

# 59. PR 27 — Intelligence Query Layer

## Goal

Create stable application services used by REST and MCP.

Implement approximately:

```text
search_location_scams()
search_scams()
get_hypothesis()
get_hypothesis_evidence()
match_incident()
get_recent_patterns()
get_related_hypotheses()
get_location_summary()
```

Return structured data, not polished prose.

Include research-state metadata where relevant.

## Review Gate

Treat these service contracts as an important boundary.

---

# 60. PR 28 — Public REST API

## Goal

Expose structured intelligence through HTTP.

Implement approximately:

```text
GET  /v1/locations/{id}/hypotheses
GET  /v1/hypotheses/{id}
GET  /v1/hypotheses/{id}/evidence
POST /v1/hypotheses/match
GET  /v1/locations/{id}/recent
POST /v1/reports
```

Add pagination, filters, consistent errors, concise OpenAPI descriptions and integration tests.

Where useful, expose research status without exposing internal scheduler complexity.

---

# 61. PR 29 — Emerging Pattern Detection

## Goal

Identify potentially emerging scam patterns.

Keep separate:

```text
novelty
growth
confidence
```

Signals may include:

```text
first observed date
recent independent evidence growth
historical baseline
new geographic appearance
similarity to existing hypotheses
```

Use cautious language such as:

```text
first observed in our dataset
recently observed pattern
emerging candidate
```

Create a review queue.

---

# 62. PR 30 — Research Scheduler and Periodic Deep Research

## Goal

Implement the periodic research daemon/scheduler.

The scheduler should evaluate work approximately every **15 minutes**.

The scheduler itself should be cheap.

It should not crawl the world.

Implement deterministic research priority using signals such as:

```text
demand
staleness
uncertainty
recent activity
research incompleteness
```

Track demand windows such as:

```text
24h
7d
30d
```

Support simple refresh eligibility tiers conceptually similar to:

```text
HOT → ~6–12h minimum eligibility
ACTIVE → ~1–3d
NORMAL → ~1–2w
COLD → no proactive refresh
DORMANT → user-triggered only
```

These should be configuration, not deeply hardcoded policy.

Support at least two research job types:

```text
LOCATION_RESEARCH
HYPOTHESIS_RESEARCH
```

Priority should allow first-time deep enrichment, active locations, new hypotheses, evidence bursts, and contradictory/uncertain hypotheses to outrank routine refreshes.

Enforce configured per-cycle and daily resource limits. Monetary caps are deferred pending a later decision.

Workers perform the actual research.

The scheduler only selects/queues work.

Do not implement machine learning for scheduling.

Do not implement an elaborate workflow engine.

## Definition of Done

Given a mix of hot, stale, uncertain, active and cold locations/hypotheses, the scheduler deterministically selects sensible work within a configured budget.

---

# 63. PR 31 — MCP Integration

## Goal

Expose the proprietary intelligence database directly to ChatGPT.

Implement MCP tools approximately:

```text
search_scam_patterns
get_scam_pattern
get_scam_evidence
match_possible_scam
get_recent_patterns
get_emerging_patterns
submit_scam_report
```

The search tool must handle three cases cleanly:

```text
fresh location
→ fast DB response

stale location
→ current DB response + refresh queued

never researched location
→ bounded fast research path
```

Return structured, compact, provenance-aware results.

Include useful research metadata such as:

```text
research_state
last_researched_at
research_in_progress
```

Do not have the backend call another LLM merely to produce prose for ChatGPT.

## Review Gate

Demonstrate a real ChatGPT read flow including a first-ever location query.

---

# 64. PR 32 — MCP Context Efficiency and Quality

## Goal

Make MCP practical in real conversations.

Improve:

```text
tool descriptions
response size limits
evidence pagination
provenance references
location resolution
error contracts
research-state communication
```

Return useful corroboration labels.

Do not overload ChatGPT context with every evidence record by default.

---

# 65. PR 33 — ChatGPT Scam Explorer UI

## Goal

Add UI only where visual interaction is better than conversation.

Create scam cards and an evidence viewer.

Possible filters:

```text
confidence
recency
category
area
```

Possible sorting:

```text
confidence
recent activity
observed report frequency
```

Show when intelligence is currently initial or being refreshed if that is materially useful.

Do not recreate an entire conventional website inside ChatGPT.

---

# 66. PR 34 — ChatGPT Reporting UI

## Goal

Allow travelers to report incidents entirely inside ChatGPT.

Collect/confirm:

```text
location
date
description
experienced / witnessed / second-hand
optional evidence
```

Allow editing before submission.

Submit through MCP.

Display success/error states without losing entered data.

## Review Gate

At this point the customer-facing product should be usable inside ChatGPT.

---

# 67. PR 35 — Admin API and Minimal Dashboard

## Goal

Provide the internal tooling required to operate the intelligence system.

Support:

```text
review candidate hypotheses
inspect evidence
merge/split hypotheses
move evidence
moderate reports
review emerging candidates
trigger reprocessing
inspect failed jobs
inspect research jobs
inspect location research state
manually raise/lower research priority where necessary
```

Protect admin endpoints.

Maintain a basic audit log.

Build the simplest usable internal interface.

---

# 68. PR 36 — Evaluation Harness

## Goal

Measure whether changes improve or damage intelligence quality.

Create labeled data for:

```text
documents
claims
locations
hypotheses
expected matches
duplicate groups
stance
```

Evaluate:

```text
claim extraction
location resolution
hypothesis matching
false merges
false splits
stance classification
duplicate detection
independence scoring
```

Add confidence scenario tests.

Also add deterministic scheduler tests, for example:

```text
hot stale location should outrank cold stale location
new uncertain hypothesis should outrank routine cold refresh
cycle budget must never be exceeded
recent demand should decay over time
first-time deep enrichment should receive high priority
```

## Review Gate

Future prompt/model/scheduler changes should be evaluated against this harness.

---

# 69. PR 37 — Security, Observability and Performance

## Goal

Harden the application enough for MVP operation.

## Security

Add necessary controls such as:

```text
rate limiting
request size limits
URL/SSRF protection
secret-log protection
PII handling
submission spam protection
dependency checks
```

## Observability

Track useful metrics such as:

```text
API latency/errors
worker failures
research scheduler cycles
research jobs queued/completed/failed
search queries used
search result counts
fetch success rate
documents processed
claims extracted
new hypotheses
resolver ambiguity
corroboration success
Sarvam/embedding usage
user reports
moderation backlog
interactive research latency
```

## Performance

Only now add justified optimizations such as:

```text
Redis caching
cache invalidation
missing DB indexes
vector tuning
batch embeddings
batch scoring
worker concurrency limits
research concurrency limits
basic load testing
```

---

# 70. PR 38 — Minimal VPS Deployment, Durability and MVP Release

## Goal

Deploy the validated application without redesigning it.

Target architecture:

```text
Internet / ChatGPT
        |
        v
One low-cost Linux VPS
  Caddy HTTPS -> API/MCP/admin
  PostgreSQL + pgvector
  Redis + arq worker + existing periodic scheduler
        |
        v
Private S3-compatible objects + off-server database backups
```

Add production configuration for:

```text
single x86-64 VPS and firewall
production Docker Compose
Caddy TLS; first test a publicly trusted IP certificate with automated renewal and ChatGPT MCP compatibility, using domain/DNS only as a fallback
PostgreSQL + pgvector and Redis on private container networking
private S3-compatible storage (R2 proposed)
restricted untracked server credentials
bounded worker/model/database resources
rotated logs and simple health/backup checks
existing in-worker scheduler; no separate cloud trigger
```

Use minimal Terraform/cloud-init and versioned deployment/restore scripts, scoped to
this one-machine deployment. OVHcloud VPS-2 was selected on 2026-11-03 due to limited
VPS-1 availability. Confirm the ordered CPU/RAM/disk and actual recurring price,
then profile that allocation with one worker job initially. Increase capacity only
if measurements justify it. See the deployment decision for the pending choices.

Do not introduce Kubernetes.

Implement/document:

```text
database backups
restore procedure
object-storage lifecycle
production migrations
worker deployment
scheduler deployment
readiness checks
monitoring alarms
basic runbook
```

Record important processing version information:

```text
pipeline version
LLM model
embedding model
scoring version
```

Prove traceability:

```text
hypothesis score
   ↓
evidence
   ↓
claim
   ↓
document
   ↓
raw source
```

Validate at least one popular destination and one previously unseen/niche destination end to end.

Freeze initial REST and MCP contracts.

Tag the MVP release.

---

# 71. Major Review Gates

Stop for manual inspection after:

```text
PR 07  — foundational domain model
PR 15  — location → Search API → structured claims
PR 17  — hypothesis resolver
PR 22  — confidence engine
PR 24  — first-time/on-demand location research
PR 25  — user-report integration
PR 27  — intelligence service contracts
PR 31  — ChatGPT/MCP integration
PR 34  — complete customer-facing ChatGPT flow
PR 36  — evaluation harness
PR 38  — production release
```

At these points, changing earlier code is acceptable.

Do not preserve a bad abstraction merely because it already exists.

---

# 72. First Important Product Milestone

Before maps, fancy dashboards, large-scale crawling or broad global precomputation, prove:

```text
INPUT

"Fiji"

        ↓

Generate search queries

        ↓

Search API

        ↓

Rank/dedupe URLs

        ↓

Fetch documents

        ↓

Retain raw source

        ↓

Clean content

        ↓

Identify scam-related passages

        ↓

Extract normalized claims

        ↓

Resolve locations

        ↓

Generate embeddings

        ↓

Match/create hypotheses

        ↓

Attach evidence

        ↓

Identify duplicate/correlated evidence

        ↓

Calculate independence

        ↓

Calculate confidence

        ↓

Return initial hypotheses with provenance

        ↓

Queue deeper corroboration
```

This intelligence loop matters more than building a giant crawler.

---

# 73. Second Important Product Milestone

Prove incident matching:

```text
"I was near the Grand Palace and someone told
me it was closed and offered me a cheap tuk-tuk."
```

Flow:

```text
description
    ↓
claim representation
    ↓
semantic retrieval
    ↓
existing scam hypothesis
    ↓
matching evidence
    ↓
structured intelligence response
```

Then prove user contribution:

```text
traveler report
      ↓
claim extraction
      ↓
existing hypothesis match
      ↓
evidence
      ↓
confidence/history update
```

---

# 74. Full Research Lifecycle

The system should eventually behave approximately like this:

```text
                         USERS
                           │
                    query locations
                           │
                           ▼
                  DEMAND SIGNALS
                           │
              ┌────────────┴─────────────┐
              │                          │
              ▼                          ▼
     first-time location          existing intelligence
              │                          │
              ▼                          ▼
         fast research               return DB
              │                          │
              └────────────┬─────────────┘
                           ▼
                   INTELLIGENCE DB
                           │
                           ▼
                 RESEARCH SCHEDULER
                    every ~15 min
                           │
             ┌─────────────┼──────────────┐
             │             │              │
          demand        staleness      uncertainty
             │             │              │
             └─────────────┼──────────────┘
                           │
                    recent activity
                           │
                           ▼
                    PRIORITY QUEUE
                           │
                ┌──────────┴──────────┐
                ▼                     ▼
         Location Research      Hypothesis Research
                │                     │
                └──────────┬──────────┘
                           ▼
                       Search API
                           │
                           ▼
                        Sources
                           │
                           ▼
                        Claims
                           │
                           ▼
                       Evidence
                           │
                           ▼
                      Hypotheses
                           │
                           ▼
                       Re-score
                           │
                           └──────────────► next cycle
```

---

# 75. User Report Flow

```text
Traveler
   │
   ▼
ChatGPT conversation
   │
   ▼
Report confirmation UI
   │
   ▼
submit_scam_report
   │
   ▼
UserReport
   │
   ▼
PII / moderation
   │
   ▼
Claim extraction
   │
   ▼
Hypothesis resolution
   │
   ├── existing → evidence
   │
   └── new → candidate hypothesis
                     │
                     ▼
             hypothesis research
                     │
                     ▼
                score update
```

---

# 76. Emerging Intelligence Flow

```text
Historical evidence
        │
        ▼
Score snapshots
        │
        ▼
Recent evidence growth
        │
        ▼
Geographic changes
        │
        ▼
Novelty / growth analysis
        │
        ▼
Emerging candidates
        │
        ▼
Human review
        │
        ▼
Published intelligence
        │
        ▼
ChatGPT
```

---

# 77. Cost Philosophy

Keep expensive model and search work limited.

Preferred sequence:

```text
query generation
       ↓
Search API
       ↓
cheap result filtering
       ↓
fetch only promising pages
       ↓
cheap deterministic filtering
       ↓
Sarvam 105B extraction/classification
       ↓
small embedding candidate set
       ↓
Sarvam 105B for harder semantic decisions
```

Do not send every discovered page to an LLM.

Do not send every hypothesis in the database to an LLM.

Do not call the LLM when deterministic code is sufficient.

Do not make multiple LLM calls when one structured call can reliably return the required information.

The research scheduler must respect configured resource limits; monetary caps are deferred pending a later decision.

---

# 78. Provenance Rule

Provenance is mandatory.

Every public intelligence claim should ultimately be traceable through:

```text
Hypothesis
   ↓
Evidence
   ↓
Claim
   ↓
Document / UserReport
   ↓
Source
```

Do not generate intelligence records with no evidence lineage.

---

# 79. Raw Data Rule

Raw acquired source material should be treated as immutable where practical.

Do not overwrite it during processing.

Derived data may be regenerated.

This allows future reprocessing when:

```text
prompts improve
models change
extraction schemas change
scoring changes
```

---

# 80. Idempotency Rule

Processing and research jobs should be retryable wherever practical.

Re-running:

```text
document ingestion
document processing
claim extraction
embedding
scoring
research jobs
```

should not casually create duplicate data.

Do not build an elaborate exactly-once distributed system.

Use simple uniqueness constraints and idempotency keys where useful.

---

# 81. Human Review Rule

Automation should not make irreversible decisions about ambiguous or sensitive data.

Human review should remain possible for:

```text
ambiguous hypothesis merges
hypothesis splits
sensitive allegations
named businesses
named individuals
emerging-pattern publication
uncertain resolver decisions
manual research-priority intervention
```

Do not build the full admin system before its assigned PR.

---

# 82. Definition of MVP

MVP is complete when:

1. A user can provide a location name without manually supplying URLs.
2. The backend can generate useful search queries.
3. A Search API can discover candidate sources.
4. Raw fetched sources are preserved.
5. Scam-related passages can be identified.
6. Structured claims can be extracted.
7. Claims retain provenance.
8. Locations can be normalized hierarchically.
9. Claims can retrieve similar hypotheses.
10. Claims can create or join hypotheses.
11. Duplicate evidence is discounted.
12. Evidence independence is estimated.
13. Evidence has explainable weight.
14. Hypotheses have explainable confidence.
15. Confidence is separate from prevalence and recency.
16. A never-before-researched location can trigger bounded fast research.
17. Initial results can be returned without waiting for exhaustive research.
18. Deep enrichment can continue in the background.
19. Stale intelligence can be returned immediately while refresh is queued.
20. A periodic scheduler can choose high-value research work using demand, staleness, uncertainty, activity and budget.
21. Both location-level and hypothesis-level research are supported.
22. User reports enter the same evidence system.
23. Emerging patterns can be detected from history.
24. REST exposes structured intelligence.
25. MCP exposes structured intelligence to ChatGPT.
26. Users can report incidents from ChatGPT.
27. Important automated decisions can be reviewed.
28. Evaluation can detect major regressions.
29. The system can deploy to the planned low-cost single-VPS architecture with working HTTPS and tested off-server recovery.

---

# 83. Final Instruction

When implementing any PR, optimize for:

```text
the simplest correct code
```

not:

```text
the most architecturally impressive code
```

Do not solve problems we do not have yet.

Do not build infrastructure we do not need yet.

Do not precompute the world.

Do not create abstractions merely because they might be useful later.

Do not turn the research scheduler into a crawler platform.

Do not let background research delay interactive answers unnecessarily.

A small system that clearly works, preserves evidence, improves on demand, and is easy for one developer to operate is preferred over a sophisticated system that is difficult to understand or expensive to run.
