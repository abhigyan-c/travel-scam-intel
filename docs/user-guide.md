# Travel Scam Intelligence: user guide

Version 0.1.0 · Updated 22 November 2026 · English only

Travel Scam Intelligence helps you research reported travel scams, inspect their
sources and geographic scope, compare a suspicious situation with existing
patterns, and submit an anonymous incident report. The product is free and has no
purchases or payments. It is not an emergency service or a guarantee of safety.

## Getting access

The public ChatGPT listing is **not yet approved or published**. During the preview,
use a ChatGPT account/workspace that permits custom MCP connections. In its
developer/plugin settings, add **Travel Scam Intelligence**, select authentication
**None**, and use:

`https://travel-scam-prod.tailbbf557.ts.net/mcp`

Select that connection in a new conversation. Refresh or reconnect it after a
release if new tools or views are missing. Settings labels and availability depend
on your ChatGPT account and workspace. An intended worldwide listing does not
override ChatGPT's supported countries, plans, administrator policies or interface
restrictions. The developer connection is not proof of public directory approval.

## Your first travel report

Try this single prompt:

> Use Travel Scam Intelligence to check scams in Bangkok, Thailand. I am visiting
> next month. What should I keep in mind? Give me a sourced report. Do not submit an
> incident report.

Give the city and country, and distinguish a city from a specific attraction.
The service resolves the location before using its ID. If several places match,
provide the requested country or parent location. A provisional location means
the place record is newly created; it does not establish that scams occur there.

The report flow can start background research and follow the same run with bounded
waits. ChatGPT may finish with available partial results when a deadline, provider
failure or capacity limit is reached. There is no guarantee of a complete report
within five or ten minutes. The watch is limited to 600 seconds and 60 waits;
each server wait is short. A finished ChatGPT response does not promise a later
unsolicited message. Host behavior can differ from the app's own polling view.

For a later read without starting more work, say:

> Check the existing Bangkok, Thailand research with request_research=false.
> Show available findings and evidence without starting new research.

Use an existing report/run ID when one was returned. Repeatedly starting new
reports does not make the same work finish faster and consumes shared capacity.

## Understanding a result

Read the actual evidence and status together:

| Field | Meaning |
| --- | --- |
| Candidate | A published pattern with limited supporting evidence; not a verified incident. |
| Confidence | Evidence-ranking metric based on the available support. It is not the probability that an allegation is true or your chance of becoming a victim. |
| Exact scope | Attributed to the requested location. |
| Ancestor scope | Attributed to a parent, such as a country; not necessarily observed in your city. |
| Descendant scope | Attributed to a child, such as an attraction; do not generalize it citywide. |
| Provenance | Where the claim came from, its source/excerpt, attribution, dates when known and relation to the pattern. |
| Missing occurrence date | The service cannot place the alleged incident in time; zero recent dated reports does not mean the activity stopped. |
| Multiple evidence records | Not necessarily multiple independent sources. Two claims from one article do not corroborate one another independently. |

For example, a Grand Palace bird-feeding allegation appearing in a Bangkok search
remains specific to the Grand Palace. A Fiji-country taxi allegation appearing in
a Suva search does not become a Suva incident. Ask ChatGPT to fetch evidence for
each finding and cite the source. Follow links critically: a source can be wrong,
old, second-hand or unavailable.

Low-confidence eligible candidates may be visible with disclaimers. Manual review
is used for holds and moderation; publication does not mean a human verified the
allegation. Research can discover no usable new claims even for a popular city.

## Research status, coverage and freshness

| Status or outcome | How to interpret it |
| --- | --- |
| Queued / researching | Work is waiting or running; results can change. |
| Completed | The run reached its successful terminal state; inspect coverage and evidence separately. |
| Partial | Some work or findings are available, but limits/failures prevented full coverage. |
| Failed | The run did not succeed; inspect any retained results and error information. |
| research_incomplete | No complete answer is available yet; zero results is not a negative finding. |
| nothing_found | The completed search returned no publishable patterns; it is not a guarantee that no scams exist. |
| Stale / unknown freshness | The record lacks sufficiently recent successful research or a known success timestamp; it does not make every retained claim false. |

Run status, research coverage and freshness answer different questions. A search
can return useful evidence while a pattern has never had its own enrichment run.
Legacy fields can still describe that pattern as stale or never researched. Ask
which location, pattern and run each status describes instead of treating a
single status label as the validity of the entire report.

## Exploring and comparing

When ChatGPT opens the interactive explorer, use **Findings and precautions**,
Confidence and Scope filters to narrow the view. Open a finding to inspect its
evidence and location. Filtering changes what you see; it does not change the
underlying confidence. Pause/Resume controls govern the view's own updates.

ChatGPT's generated cards and narrative are separate from this app view. If a
card stays queued, check the actual app rather than assuming the server is still
running. Automatic terminal updates of the explorer remain a release acceptance
item; do not rely on that behavior until verified in your host.

To compare a situation:

> A driver near my Bangkok hotel refuses the meter and offers a fixed price.
> Compare this with existing patterns. Do not submit a report.

A match indicates similarity to stored information, not proof that a particular
person is committing fraud. Do not include personal identifiers, payment details
or sensitive information in the description. For recent patterns, ask for dates
and the distinction between recently added evidence and a recently occurring
incident. Unknown dates cannot support a reliable trend.

## Submitting an incident report

1. Ask to open the incident-report form for the location. Merely opening or editing
   a form does not submit it or start research.
2. Describe what happened in 20–6,000 characters. Separate what you observed from
   guesses. Choose personally experienced, witnessed or second-hand accurately.
3. Supply an incident date only if known; do not invent one or use a future date.
   Resolve the location as precisely as you can, or leave uncertainty explicit.
4. Optionally add supporting text and up to three HTTP/HTTPS source links. Links
   are not automatically fetched or verified on submission. The ChatGPT form
   supports text and links, **not image uploads**. The whole payload is bounded.
5. Use Review, check the details, return to Edit if needed, then explicitly confirm
   and submit using the form controls.

Reports are anonymous. Do not include contact details, passwords, payment data,
government identifiers or unnecessary identifying information about anyone.
Automated screening is imperfect; it is not permission to submit private data.

A receipt means the report was accepted for processing, **not verified or
published**. Processing and review have separate statuses. Eligible derived claims
may become public; sensitive or unresolved material may be held for review.

If the receipt is lost after submission, keep the form open. It freezes the exact
confirmed submission. Reconfirm and use **Retry exact confirmed submission** to
recover the existing receipt without creating a duplicate. Do not ask ChatGPT to
create another submission. Closing the view discards the draft and retry context;
it does not delete an already accepted report.

## Privacy, retention and help

The operator receives tool arguments sent to this service, rather than automatic
access to your whole ChatGPT conversation. Search queries go to Serper; selected
source/report-derived text can go to Sarvam for processing. Embeddings run locally.
The service stores normalized records in PostgreSQL and raw objects in private R2;
encrypted database backups are retained separately. See the prepared public
[privacy policy](../app/public/privacy.html) for recipients and limitations.

Reports, source evidence, raw objects, database history and encrypted backups have
**no automatic expiry and may be retained indefinitely**. There is no self-service
erase function. Closing a form or hiding a public finding is not data deletion.
Contact support for manual correction/removal requests; there is no guaranteed
deletion deadline, and backup restoration needs special care to preserve removals.

Email **abhigyanchakraborty61@gmail.com** with the problem, approximate time,
request/run/receipt ID if available and relevant public source URL. Never email
tokens, passwords or unnecessary private incident details. No response-time SLA
is promised. For immediate danger, seek appropriate local emergency assistance.

## Common problems

| Problem | Action |
| --- | --- |
| Connection fails | Check the exact HTTPS hostname, refresh the connection and verify your workspace permits it. The VPS IP is not the supported ChatGPT connection URL. |
| Only text appears | Open/expand the app view if available. Report the actual UI behavior separately from ChatGPT's summary. |
| Research ends with partial results | Read available evidence and limitations. Do not infer missing scams or ask the model to fill gaps. |
| 429 / allowance exceeded | Shared anonymous capacity is exhausted; wait and retry the same request. |
| 503 / temporarily unavailable | Retry later. Preserve exact confirmed report inputs and receipt context. |
| Incorrect location or source | Send a correction with the record/source ID and explanation. Do not submit fabricated replacement evidence. |

For operations and moderation, see the [admin guide](admin-guide.md).
