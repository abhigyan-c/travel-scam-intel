# PR 31B — Grounded qualified geography

Branch `codex/pr-31b-grounded-location-resolution`, base/target `develop` after
PR 31A's squash merge `c99299f`. This PR repairs qualified place identity, not
extraction recall, acquisition, continuation or publication policy.

## Resolution policy

Existing exact names/aliases and caller country/type/scope restrictions remain
first. When an exact name is unknown, the resolver accepts one explicit English
qualifier: `Grand Palace in Bangkok`, `Grand Palace, Bangkok`, or `Bangkok,
Thailand`. Initial `at`, `near`, `outside`, `around` and `in` are recognized in a
qualified description. A literal name beginning with `The` is tried before an
optional article is removed. There is no fuzzy substring or embedding match.

The qualifier must resolve uniquely to an existing city/country within the caller's
scope. Known descendants reuse their real hierarchy. Unknown named landmarks can
be provisional POIs under an explicitly named known city. Inference is deliberately
narrow: a capitalized distinguishing name plus a supported landmark noun, at most
eight words, without administrative or multiple-place wording. Explicit caller
types retain the existing provisional-creation policy. Provisional records have
no invented coordinates, aliases or confirmation of geographic truth.

Generic references (`the old station`, `a local market`), unknown or ambiguous
parents, country conflicts, inverted hierarchy and sibling places outside the
caller's POI/area scope do not become guessed local records. Compound descriptions
with multiple qualifiers, such as `Grand Palace, Bangkok, Thailand`, currently
remain unresolved and require clarification. Unknown names outside the narrow
landmark grammar also remain unresolved unless the caller explicitly supplies a type.
An ambiguous parent is reported as unresolved with a reason; its city IDs are not
misrepresented as candidate landmark IDs.

For extracted claims, qualified resolution requires the location to occur verbatim
in its evidence quote, the quote to occur in the processed document, and supplied
text hash/quote offsets to remain valid. The search label alone cannot provide a
city or identify an unnamed landmark. Legacy exact-name claims without quotes keep
exact-name resolution; they do not gain inferred qualified geography.

Research and report processing share this behavior. Their calls permit provisional
creation, but provide no guessed place type: the source must explicitly qualify a
recognizably named landmark under a known city. The separate report-submission
context fallback for a missing narrative place is unchanged.

## Provenance and compatibility

`location-v2` records the original text and its full normalized name, plus additive
`method`, `component_text` and `qualifier_text` fields. The hierarchy identifies the
resolved city/POI; a provisional flag remains explicit. Original extracted location,
quote, extraction lineage, sensitivity and publication state are preserved.
No database migration, new provider, model call, geocoding service or credentials
are required. Sensitive claims still need review, even after their geography resolves.
Existing stored claims are not bulk-updated or approved: normal reprocessing uses
the new resolver. No production deployment/reprocessing is performed by this PR.

## Measurement and verification

The PR 31A corpus and provider recording are unchanged. Replay under `location-v2`
improves correct scoped geography **2/7 → 3/7**: the Grand Palace claim becomes
`Thailand → Bangkok → Grand Palace`, with the POI provisional when absent.
Grounded claim coverage remains **5/9** and eligible public-pattern coverage **2/6**.
The landmark's recorded sensitivity flag keeps it private. All negative/withholding
checks remain passing. Warm replay uses no additional model calls, and this PR made
no live provider requests. Replay call/token counters describe the old recording,
not new spending or live performance.

The new integration cases cover exact/qualified aliases, idempotent provisional
creation, generic references, ambiguous parents, country/hierarchy/scope conflicts,
stale quote/hash/offset checks, and both eligible/held end-to-end candidate behavior.
Validation: **879 passed, 10 skipped** with infrastructure enabled; Ruff lint/format
passed. `benchmarks/data/31b-replay.json` records the replay against committed
source `33a8df6`, including `location-v2` and its unchanged provider recording.
Valid-empty extraction misses, sensitivity classification quality and broader
location-expression coverage remain future work; this does not pass the 31F gate.
