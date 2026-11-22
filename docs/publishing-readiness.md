# Public ChatGPT publishing readiness

Prepared 22 November 2026 for version 0.1.0. **Not submitted, approved or published.**

## Intended listing

| Field | Prepared value |
| --- | --- |
| Name | Travel Scam Intelligence |
| Subtitle | Sourced travel scam research |
| Requested publisher | abhigyan-c; platform identity verification is still required |
| Category | Travel |
| Audience | All supported countries, English only |
| Commerce | Free; no purchases or payments |
| Connection | `https://travel-scam-prod.tailbbf557.ts.net/mcp`, authentication None |
| Contact | abhigyanchakraborty61@gmail.com |

Listing text: Research reported travel scams by location, compare a suspicious
situation with existing patterns, inspect source evidence and geographic scope,
and submit an anonymous incident report after review and confirmation. Results
can be partial, undated or low confidence. Candidate findings are allegations,
not verified incidents or estimates of personal risk. No safety guarantee.

The portable source under `publishing/travel-scam-intelligence/` connects the existing
MCP service. It does not create a duplicate private app or contain credentials.
Unknown/unverified listing URLs and the demo recording are deliberately absent
from its manifest. It is an **incomplete submission draft**, not a ready-to-upload
package. The local app version and live production version currently differ.

## 1. Publish and verify the four prepared pages

Source pages live in `app/public/`. Your confirmed policy choice is indefinite
retention with manual support requests, not an automatic deletion promise. Review
the prepared terms/privacy text before making it the public policy; technical
accuracy does not establish compliance with every jurisdiction's legal obligations.

The release serves these exact application paths:

| Manifest field | Intended path on the MCP hostname | State |
| --- | --- | --- |
| websiteURL | `/about` | Prepared in source; not deployed/verified |
| supportURL | `/support` | Prepared in source; not deployed/verified |
| privacyPolicyURL | `/privacy` | Prepared in source; not deployed/verified |
| termsOfServiceURL | `/terms` | Prepared in source; not deployed/verified |

Deploy the reviewed app image first. Install the reviewed `deploy/Caddyfile` into
the host's Caddy configuration while preserving its existing `VPS_IP` environment
and certificates. Validate with `caddy validate --config /etc/caddy/Caddyfile`
in the service's configured environment before reloading Caddy. Preserve a copy
of the preceding configuration for rollback. Verify both the IP listener and
Funnel listener after the reload. Never point Funnel at unfiltered port 8000.

Fetch all four HTTPS pages from outside the tailnet and inspect their content,
navigation, support address and privacy disclosure without login. Confirm `/admin`,
`/v1/admin/operations`, `/ready`, `/docs`, `/openapi.json`, media paths and unknown
paths remain inaccessible publicly. Only then write the four full verified URLs
into `extensions.com.openai.interface` and rebuild the package. A source HTML file
or HTTP 200 for the wrong page does not satisfy this requirement.

## 2. Finish real host acceptance and the demo

The reporting form and exact-submission retry have prior user-observed acceptance.
The five-minute watch produced a final ChatGPT narrative. However, the actual
explorer's autonomous terminal rendering was deferred: generated narrative cards
are not evidence that the app view updated. Recheck desktop and mobile on the
release being submitted, including keyboard access, long text and error states.

Use the review cases in the manifest. They cover five distinct supported flows:
sourced trip research, existing evidence with geographic scope, semantic situation
comparison, recent patterns with date limitations, and a report draft without
submission. Three negative prompts test unsupported bookings, emergency dispatch
and guaranteed safety. **All eight exact submission cases are Not run against this
candidate**; historical related tests are context, not a fresh pass.

Record a real walkthrough in ChatGPT, not a mocked video:

1. Start a screen recorder and a clean chat with the existing development connection.
   Keep unrelated chats, account details and secrets off screen.
2. Ask the Bangkok report prompt. Show actual tool calls and the app view; leave it
   visible through the terminal state. Show a partial outcome honestly if returned.
3. Open one finding and its evidence. Explain actual location, candidate status,
   missing dates and that confidence is not a probability.
4. Open the incident form, type a clearly synthetic draft, Review and Edit it, then
   stop before confirmation/submission. Do not insert synthetic incidents into the
   public dataset for a demo.
5. Ask an unsupported booking request and show that no booking is claimed.
6. Stop and play back the recording, checking readability, real results and secrets.
   Host it at a reviewer-accessible HTTPS destination and verify playback without
   your private account. Save that verified link as `review.demo_recording_url`.

Computer assistance can demonstrate the visible flow if desired, but screenshots
alone are not video capture. Until the recorded file and public playback are
verified, the demo remains incomplete. No demo URL has been invented.

## 3. Verify publisher and MCP domain

The requested display name is `abhigyan-c`; the OpenAI portal's verified individual
or business identity controls what can actually appear publicly. Complete that
verification and inspect the saved listing name. Do not attest that a GitHub
username alone constitutes a verified publisher.

Use the exact domain challenge supplied by the portal. The new Caddy configuration
permits only GET/HEAD on `/.well-known/openai-apps-challenge`, serving the file:

`/var/lib/travel-scam/openai-verification/.well-known/openai-apps-challenge`

Create its root-owned parent directories readable by Caddy and install the exact
public challenge text as a readable file (for example 0644), without HTML wrappers.
Do not place account tokens, API keys or credentials there. An absent challenge
correctly returns 404. Verify the HTTPS body from outside the tailnet and complete
the portal check. This route is prepared; the domain is **not verified yet**.
Owning a tailnet hostname does not itself prove the portal accepts its verification.

## 4. Build, inspect and submit only when ready

Before building a submission ZIP, verify the public MCP endpoint is reachable and
serves the expected tools. Inspect boolean read-only, destructive and open-world
annotations against each tool's effects; read tools must not silently submit data.
Use the production endpoint, not the isolated historical acceptance fixture.

Package root `plugin.json`, `mcp.json` and referenced `assets/` files. Exclude
`.env`, Git data, local tools, credentials, `.app.json` and non-null `apps` bindings.
Keep the selected version and manifest identity stable. Validate listing lengths,
the exact Travel category, the actual icon PNG dimensions/size, five positive and
three negative cases, the four verified pages and the verified demo recording.
Inspect the final ZIP inventory, not just the source directory. No submission-ready
ZIP is claimed while these fields and external checks are outstanding.

After preparation, use the [OpenAI plugin portal](https://platform.openai.com/plugins)
under the intended verified publisher to create a draft, connect/scan the anonymous
MCP server and run the actual review cases against that saved version. Record
Passed/Failed/Blocked outcomes with observable tool arguments and UI evidence.
Credentials are not needed for this anonymous service; do not give reviewers the
admin token. Complete legal/policy attestations personally. Draft upload, submission
for review and publication of an approved release are separate actions.

## Launch gates still open

- Deploy and verify the patched release; production still runs PR 35 as observed
  on 22 November. Complete the release security/CI and recovery gates.
- Configure and test external alert delivery; assess shared anonymous quota abuse
  and capacity before directory-wide exposure.
- Publish/verify policies, complete actual host UI acceptance and demo recording.
- Verify publisher and domain, finish manifest fields/package validation and
  platform connection/scans/review. Approval cannot be guaranteed.
- Publish the approved version explicitly. All supported countries is the requested
  targeting; account, country, plan, workspace and host eligibility still apply.

Official references checked during preparation:
[submission](https://developers.openai.com/plugins/deploy/submission),
[review](https://developers.openai.com/plugins/deploy/app-review),
[guidelines](https://developers.openai.com/plugins/plugin-guidelines),
[ChatGPT availability](https://help.openai.com/en/articles/11487775-connected-apps-in-chatgpt).
