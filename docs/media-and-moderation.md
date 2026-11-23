# Private report media and moderation (PR 26)

Reporters can append optional JPEG/PNG evidence to a PR 25 submission. Images stay
private even after acceptance. They never become public evidence URLs, model inputs,
extra independent reports or automatically extracted claims. Each new upload places
the report on review hold and refreshes its evidence weights and scores.

## Upload contract

`POST /v1/reports/{report_id}/attachments` takes raw image bytes. Headers:

- `Content-Type: image/jpeg` or `image/png`.
- `Report-Submission-Key`: the original report's `Idempotency-Key`.
- `Idempotency-Key`: a new UUID for this upload, reused on retries.

The original submission key is a per-report upload capability. Keep it private and use
a random UUID when creating the report. Report ID alone cannot authorize uploads.
Missing reports and incorrect keys both return 404. Legacy reports without intake keys
cannot accept anonymous uploads. There is no filename field or multipart dependency.

HTTP 201 returns attachment/report IDs, attachment state, moderation revision and replay
status. Matching retries create neither a new hold nor a new audit event. Changed bytes
under the same key return 409. Rejected reports cannot receive new files. At most four
files per report are allowed, including rejected files; concurrent requests share the cap.

Limits: 5 MiB per original and normalized file, 10 million decoded pixels, one frame,
four files per report. Actual streamed bytes are bounded without trusting Content-Length;
compressed request encodings are rejected. Pillow checks the decoded format against MIME,
verifies/loads the image, rejects animation/excessive dimensions, applies EXIF orientation,
then re-encodes fresh pixels without EXIF, GPS, PNG text, profiles or original filenames.
Unsupported formats (PDF, SVG, GIF, video, etc.) return 415, malformed images 422 and
invalid byte sizes 413. Re-encoding is not a malware scan or visible-pixel PII detection.

Both original and normalized bytes use immutable content-addressed private object keys.
`ReportAttachment` stores their hashes, sizes, dimensions, type and private paths. Originals
may contain PII and are restricted raw provenance. Only normalized images have an admin
download route. No raw path, public/presigned URL or hash is returned in public receipts.
Storage/database failure does not commit an attachment or hold. Interrupted writes can
leave unreferenced private objects; deterministic keys make a retry safe.

## States and publication

Moderation states are pending, processed, needs_review, accepted and rejected. Processing
status remains separate; the report receipt exposes both and the moderation revision.
An ordinary new report progresses from pending to processed through the text worker.
PII, unresolved geography, semantic/text holds and sensitive/named actors require review.

An image arriving before processing does not prevent ordinary English text processing,
but the resulting candidate/evidence has zero public weight until media review. The
`text_complete` marker distinguishes finished text from pending image review. Images
arriving after processing or acceptance hold the report again. No upload increases the
independent-report count. Decisions refresh scores and preserve prior snapshots.

Accepting ordinary fully processed text after reviewing all current images marks all
attachments accepted. Rejecting the report marks attachments rejected and removes its
public contribution while preserving history. Reopening a rejected report as needs_review
is required before acceptance, which still requires complete ordinary text. These endpoints
do not edit submissions or restart incomplete/failed text jobs.

**Sensitive release remains deferred to PR 35's dashboard.** Acceptance is blocked when
intake flags remain, text processing is incomplete, a semantic/text hold exists, geography
is unresolved, or claims contain sensitive/named actors or held publication markers.
An admin token cannot bypass these checks. Ordinary media approval never clears claim
sensitivity or rewrites grounding. Accepted images remain private.

## Authenticated backend

Set a random `ADMIN_API_TOKEN` of at least 32 characters in ignored `.env`; Compose supplies
it to the API. Admin routes return 503 when it is absent/too short. Send
`Authorization: Bearer <token>`; missing/incorrect credentials return 401 when configured.
Tokens are never query parameters, response fields or audit data. This is a shared operator
credential; the actor label is operator attribution, not a separately authenticated account.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/admin/reports/{id}/attachments` | Revision, normalized report text, processing flags and attachment metadata |
| `GET /v1/admin/reports/{id}/attachments/{attachment_id}` | Hash-verified normalized private image download |
| `POST /v1/admin/reports/{id}/moderation` | Accept/reject/hold ordinary reports with audit history |

Metadata/downloads use `Cache-Control: no-store`. Downloads use safe generated filenames
and `X-Content-Type-Options: nosniff`. Hash/size mismatch or storage errors return 503.
There is no anonymous attachment read/list route or HTTP route for originals. The PR 35
dashboard UI is not introduced here.

Moderation POST takes its own `Idempotency-Key` and JSON:

```json
{
  "status": "accepted",
  "expected_revision": 2,
  "actor": "operator-label",
  "reason": "Reviewed current ordinary text and all private images"
}
```

Status is accepted, rejected or needs_review. Obtain the revision from the authenticated
metadata read. Uploads and completed text attempts advance it; stale decisions return
409. Replaying an old successful key never re-applies approval to newer files. Responses
include the current report status/revision, original event ID and replay flag. Changed
decisions under the same key return 409.

`ReportModerationEvent` preserves action, actor, private reason, before/after status and
revision. Upload/processing events use fixed reasons; moderation stores the operator's
reason. Application helpers do not rewrite event rows. Decisions, holds and score updates
commit together. Database transaction/deadlock failures can be retried with the same key.

## Migration and validation

Migration 0019 adds attachment/audit tables and a report revision counter. Existing
reports begin at revision zero. Downgrade refuses to discard attachments, audit history
or nonzero revisions until the operator has explicitly preserved that work. Pillow is
now explicit in project dependencies; its locked version was already present transitively.

Tests cover size/type/dimension/frame bounds, metadata stripping/orientation, capability
and admin access, hash-checked downloads, concurrent limits, rollback, idempotency, stale
review protection, media before/after text processing, sensitive-release refusal and public
score holds. Real MinIO checks verify anonymous object requests return 403 while authorized
normalized review succeeds. Generated test records/objects are cleaned independently of
user data; no real allegations or additional LLM/provider calls are needed.

Final validation: 717 Linux tests passed (8 opt-in provider tests skipped), 716 Windows
tests passed (the same 8 plus one Unix-only worker test skipped). Formatting, lint and
Alembic checks passed. Migration 0019's empty-state downgrade/upgrade cycle was verified
before any persistent moderation or media records existed.
