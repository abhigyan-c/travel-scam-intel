# Anonymous report pipeline (PR 25)

`POST /v1/reports` accepts an anonymous traveler report and returns HTTP 202 after
private raw storage, normalized records and a durable processing job have committed.
It does not wait for Sarvam, require an account, verify the allegation, or publish a
hypothesis merely because a report was accepted.

PR 34 adds a ChatGPT draft/review/confirmation view over this same intake and key
contract. Its optional evidence is user-selected text/source links, incorporated
in the exact confirmed narrative as unverified references; no separate corroborating
source or image upload is created. MCP now accepts the existing unresolved
`location_text` alternative as well as `location_id`. See [the reporting UI](pr34-reporting-ui.md)
for date-only semantics, uncertain-outcome retries, memory-only drafts and the
still-open actual-host gate.

## Request and receipt

Send JSON and an `Idempotency-Key` header. Use a newly generated UUID per submission
and reuse that key and exact payload when retrying. Keys must contain 16–128 characters
from letters, digits, `.`, `_`, `:` and `-`, beginning with a letter or digit.

```json
{
  "report_text": "Describe the incident in your own words in English.",
  "provenance_class": "FIRST_HAND",
  "location_text": "Fiji",
  "occurred_at": null
}
```

The narrative must contain 20–8000 characters. `provenance_class` is required and is
one of `FIRST_HAND`, `WITNESSED`, `SECOND_HAND`. Optional `occurred_at` must have an
explicit timezone. A future occurrence is held for review rather than treated as current.
Do not put contact details, credentials or payment information in a report.

Location can be an existing `location_id`, or `location_text` with optional `parent_id`,
`country_code` and `location_type`. These two forms cannot be mixed. Text with trusted
parent/type context can create a provisional record through the existing resolver.
Unresolved or ambiguous explicit context remains on hold; countries are never guessed.
Location may also be omitted so extraction can resolve geography from the narrative.
If a grounded claim contains no place, an explicit resolved submission location can
provide context. Conflicting or still-unresolved claim geography requires review.

The receipt contains `report_id`, processing `status`, `replayed`, `queue_pending`,
moderation status/revision (added in PR 26) and a short message. It never returns the narrative, contact information, private raw
object path, model prompts, internal job ID or sensitive hypothesis IDs. Processing
status can be `queued`, `processed`, `partial`, `needs_review` or `failed`.
`processed` means pipeline processing completed, not that an allegation was verified.

Concurrent matching retries create one report/document/job and return the same report
ID. Changing the validated payload under the same key returns 409. Schema/context/junk
errors return 422 without echoing submitted values. Storage unavailability returns 503;
no report is accepted, and retrying with the same key is safe. An unavailable queue
still returns 202 with `queue_pending=true` because the database outbox is durable.
There is no public read/edit/list endpoint in this PR. Replaying POST with the same
payload/key can return the current receipt state; it does not restart a finished job.

## Privacy and normalization

Intake performs basic Unicode/control-character checks and rejects empty, links-only
or highly repetitive junk. It does not attempt identity, reputation or truth scoring.
Obvious email addresses, supported phone formats and long numeric identifiers are
redacted from the normalized narrative and flagged. Threat language and non-English or
unconfirmed-English reports also go to review. PII in location text cannot create a
public location record. These are deliberately small heuristics, not comprehensive PII
detection; names and street addresses are not reliably detected at intake.

Flagged intake is stored for private review without any provider call or processing job.
Named actors or sensitive allegations discovered by extraction also require review.
The review dashboard is still planned for PR 35; there is no release/approval bypass here.

The canonical original submitted fields are stored as private immutable JSON at
`reports/<key-hash>/<payload-hash>`. They can include PII needed for restricted review.
Neither paths nor job payloads contain the original key or narrative. Original raw bytes
are not sent to the model. `UserReport.report_text` and `Document.clean_text` hold the
normalized/redacted narrative, with a separate clean-text hash. `Document.content_hash`
identifies the private raw JSON, preserving the distinction between raw and normalized
content. A storage write followed by a rolled-back database transaction can leave an
unreferenced private object; its deterministic key makes retry safe.

Each submission has a `Source(source_type=user_report)` without a fabricated author ID,
one normalized Document and one UserReport. Intake records contain no IP address, user
agent, login or reporter identity. SQL parameters are hidden in application database exception
messages, and validation errors/worker logs do not reflect report text. Providers and
existing safety/grounding prompts only receive eligible normalized English text.

## Shared processing and publication rules

The worker reuses the existing relevance classifier, grounded extraction and location
resolver. Both external research and user reports now call the same claim application/
scoring and scoped embedding helpers (`app/db/claim_pipeline.py`). Claims still have
the exact document/extraction/quotation lineage; evidence still points to a claim and
hypothesis. There is no separate user-report hypothesis or confidence formula.

Ordinary grounded reports become `moderation_status=processed` before eligible evidence
is scored. The typed report provenance overrides extracted first-hand guesses in source
quality. Report occurrence dates remain authoritative for report recency. Anonymous
identity remains unknown for independence scoring; a submission is not automatically
fully independent just because its report UUID is new. Repeated/copy text can receive
discounted credit through existing duplicate/independence rules.

Only eligible deterministic resolutions are applied automatically. Semantic, ambiguous,
contradictory or sensitive decisions keep their existing review requirements. A report
requiring review is excluded from public weighting; existing attached hypotheses are
rescored when a report becomes held. Unresolved/held claims remain internally available
without inventing geography or publishing allegations. A complete irrelevant/empty
extraction is processed with zero hypotheses rather than inventing a scam.

Eligible partial extraction can retain completed claims/evidence while transient work
retries. Relevance and extraction caches are reused. A normal attempt permits at most
8 logical LLM calls, 3 passages per extraction and a roughly 40-second application budget;
the existing worker has a 55-second outer deadline and 90-second lease. Ownership is
checked before committing domain changes. Normal database deadlocks/serialization or
worker failures roll back the attempt and are retried by the durable job mechanism.

At most three attempts run per job. Partial provider/model results commit with explicit
incompleteness, then retry; exhausted partial work remains `partial` with a failed job.
Unhandled/crash exhaustion updates the report to `failed`. Successful redelivery is a
no-op. The 15-second outbox sweep now dispatches report jobs as well as location jobs,
including missed queue delivery and expired leases. New candidate reports do not start
additional location research in this PR; later orchestration/scheduling can request it.

PR 26 adds [private image attachments and ordinary-report moderation](media-and-moderation.md).
Sensitive release remains deferred to the dashboard. Client-supplied
moderation states, evidence weights and arbitrary extra fields are rejected.

## Migration and review gate

Migration 0018 adds nullable intake hashes (preserving legacy reports), processing state,
internal details and a processing-job reference, plus the `report` job kind. Constraints
enforce unique key hashes, paired valid digests and known state values. Downgrade refuses
to forget intake records/jobs until an operator has explicitly preserved that work.

The convergence review uses the real API, PostgreSQL, normalization, grounded extraction,
resolver, evidence and scoring code with controlled provider/storage boundaries. An
external Fiji source creates a candidate, and a separate anonymous report attaches a
second claim to that same hypothesis. The two evidence rows retain distinct Documents
and Sources. A declared `SECOND_HAND` report keeps that provenance even when extraction
labels its narrative first-hand, yielding the expected source-quality prior of 0.4.
No semantic review was manufactured and no real travel allegation was seeded for this
test: per-test database schemas are created and removed independently of user data.

Additional checks cover concurrency/idempotency conflicts, private raw/sanitized content,
PII location protection, provisional geography, explicit context fallback, sensitive and
semantic holds, provider failures, cached retry recovery, queue/storage outages, exhausted
workers, replay/redelivery, English-only handling and honest empty extraction.

Validation: 690 Linux tests passed (8 opt-in provider tests skipped), 689 Windows tests
passed (those 8 plus one Unix-only worker test skipped). Formatting, lint and Alembic
schema checks passed. Migration 0018's downgrade/upgrade cycle was exercised before any
public-schema intake records existed. The convergence review used synthetic grounded
fixtures in isolated schemas; no new live-provider extraction quality claim is made.
