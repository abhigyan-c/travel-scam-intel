# PR 34 — ChatGPT incident reporting

Branch `codex/pr-34-reporting-ui`; base and target `develop`, after merged PR 33
(`398d368`). The user requested PR 34 development with PR 33's actual-host acceptance
still open. Neither gate is marked passed by this implementation or its local preview.

## Confirmed scope

On 2026-11-21 the user selected **text and source links for optional evidence**.
No image upload tool, larger MCP request allowance, new storage service, authentication
or public REST exposure is added. PR 26's existing private image pipeline is unchanged.

`open_scam_report_form(location_id?)` returns a strict `report-form-v1` form address
and optional public location reference. It attaches `ui://travel-scam/report-form-v1.html`.
Opening it writes no report, creates no job and invokes no provider. The new read-only
render tool brings discovery to twelve server tools / eleven model-visible tools in
hosts honoring PR 33's app-only watch visibility. The explorer remains separate.
Neither submission receipts nor location lookups attach a new resource.

The form collects an English description, optional occurrence date, personally
experienced/witnessed/heard-or-read provenance, location and optional supporting text
and up to three HTTP(S) source links. A previously resolved location can be preselected;
the user can change it and explicitly check the new place. The existing resolver may
create provisional location records when the user supplies suitable country/type
context. Ambiguity asks for more context rather than selecting a candidate ID.
Alternatively the user can explicitly submit a place name as unresolved, or say the
location is unknown. The existing intake review rules still apply.

A date-only input is appended to the confirmed text with an explicit day-only/time
unknown label; the form does not invent a midnight timestamp or timezone. Exact dates
through the existing conversational MCP tool remain supported. Empty date stays unknown.
Invalid or future dates are rejected by the form; the backend's existing future-date
and PII/language checks remain unchanged.

Supporting text and links are appended to the same exact report text under explicit
submitter-supplied/unverified/not-checked headings. They remain one anonymous report
source, not newly fetched public documents or independent corroboration. The combined
text must fit the existing 8,000-character bound. No additional synthesis or URL fetch
occurs during submission. The user reviews the full text, actual/unresolved location,
provenance and date, and can return to editing before confirmation.

## Confirmation, retries and privacy

Only the explicit confirmation checkbox plus submit button invokes
`submit_scam_report`, with `confirmed_by_user=true`. A new idempotency key is generated
for the reviewed snapshot. Editing before submission invalidates that snapshot and
requires another review/confirmation. Source text, form initialization and host result
echoes cannot submit. The model is instructed not to submit drafts or repeat a form's
accepted submission. The existing server confirmation guard remains in force.

Submission uses the existing private raw/normalized document intake, durable job and
safe receipt. MCP gains only the existing `ReportCreate.location_text` option, mutually
exclusive with `location_id`. No database schema or fingerprint format changes are
introduced; old MCP/REST payloads retain their validated fields and hashes.

While sending, controls are locked and double clicks are ignored. A definitive
`invalid_request` returns to editing without losing the draft. A timeout, unavailable
service, conflict or malformed/missing receipt conservatively leaves acceptance
uncertain: retain the exact snapshot/key, lock editing and allow explicitly confirmed
retry of that same submission. This recovers a committed receipt without creating a
duplicate report. A new report/reset is available only after confirmed success.
Queue-pending, replay and moderation/processing status remain distinct from publication.

Drafts, submission keys and reviewed text stay in memory in the open view. They are
not copied to session/local storage, tool initialization results, model-context updates
or follow-up messages. They survive rerenders and recoverable submission errors in that
view. Closing/reloading it discards its draft and retry key, including during an
uncertain outcome; the UI tells the user to keep it open until a receipt is confirmed.
Durable draft/reconnect recovery is deliberately not promised. Closing the view does
not retract a submission already accepted by the backend. Clearing the view clears its
local fields, not the accepted report in private storage.

The shared MCP Apps bridge is extracted from PR 33 without changing its wire protocol;
both resources inline it, so no browser asset fetch is needed. It preserves safe error
codes for validation versus uncertain-outcome handling but never reflects raw provider,
storage or submitted-value errors. HTML uses `textContent`, never executable source
markup. CSP requests no external resource, connection or frame domains. The form uses
no app context sync or synthetic user-role message.

## Verification and release

Controller tests cover validation, unresolved/unknown/resolved geography, date-only
semantics, source-link bounds/schemes, exact review/edit/confirmation, duplicate clicks,
unknown-outcome locking, same-key retry, definitive error correction and teardown.
MCP/database tests verify non-writing opening, confirmation rejection, one normalized
report/document/job under concurrent retry, safe receipts, unresolved-location review
and rejection of mixed location context. The explorer tests still exercise the shared
bridge. Ruff, Node tests, Python/infrastructure tests and the production image are
checked before review.

Validation on 2026-11-21: **988 Python tests passed, 10 skipped; 20 Node tests passed**.
Ruff lint/format and whitespace checks passed. The production image built with both
self-contained resources; no migration or dependency change is required.

The loopback preview uses the actual resource against synthetic in-memory tool results:

```powershell
.venv/Scripts/python.exe -m benchmarks.explorer_preview
```

Open `http://127.0.0.1:8033/reporter-preview`. It is not ChatGPT, stores no real report,
and uses no credentials or providers. On 2026-11-21, browser inspection at 375/960 px
verified editing, exact text, a disabled submit before confirmation, literal injection
strings with no rendered images, and a lost-receipt recovery: two attempts / one
synthetic report. Narrow body scroll/client widths were both 375 px. The review and
receipt screenshots are in `benchmarks/data/34/`.

After merge/deployment, refresh the real ChatGPT connection and test opening without
submitting, editing/reviewing, cancellation, unknown date, ambiguous/unresolved location,
safe optional links and a confirmed synthetic incident in an isolated test environment.
Verify one receipt/document/job, private storage, PII moderation and no published
synthetic intelligence. Test an uncertain network outcome and retry with the same key.
Do not submit a fake incident to production to pass this gate. PR 33's active explorer,
delayed completion and narrative gate also remains open. No tunnel or deployment is
enabled by this source change; the customer-facing review gate is not yet passed.

Protocol reference checked 2026-11-21:
[OpenAI MCP Apps UI guidance](https://developers.openai.com/plugins/build/chatgpt-ui).
