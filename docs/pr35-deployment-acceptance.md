# PR 35 deployment and acceptance — 2026-11-22

Deployed merged `develop` release `c292b4ba085e689a07fa2eb6dddb776a567c443b`
(PR #50) through the existing Tailscale push deployment. The encrypted pre-release
backup completed with `Result=success` and exit status 0. API and worker run the
same immutable image and are healthy; migration `0023` is applied with no model drift.

**Result: core acceptance passed; final UI signoff remains pending one small fix.**
The toolbar's overview totals were captured at unlock and stayed stale after a
mutation or list refresh. Fresh tables and audit entries were correct. The fix on
`codex/pr-35-admin-acceptance` refreshes overview and table together, including the
post-command reload, while preserving the lock/generation fence. It has not been
merged or deployed. Merge it into `develop`, deploy that merged release, and check
the totals after a synthetic action before marking the complete admin UI gate passed.

## Live production checks

- `/health` and `/ready` return 200. The dashboard shell's script/style hashes match
  its CSP; it uses `no-store` and contains no admin token.
- Unauthenticated admin reads and commands return 401 with no caching. Authenticated
  overview, audit and all six item queues return 200; bounded detail reads pass.
  Whitelisted projections omit raw storage paths, submission hashes and lease tokens.
  The real admin token stayed inside the API container and was never printed or
  passed to the browser.
- The filtered Caddy listener used by Funnel returns 404 for `/admin`,
  `/v1/admin/overview`, `/docs` and `/ready`. No public admin route, firewall rule or
  tunnel policy was added. Public MCP discovery still returns twelve tools and two
  UI resources. This was protocol verification, not a new ChatGPT host UI test.
- Production retained **32 claims, 3 hypotheses, 5 research roots, 0 reports and
  0 admin actions** before/after acceptance. Tests did not initiate real research,
  submit a real report, create production jobs or call a paid provider.

## Isolated deployed-code acceptance

A temporary Python process inside the deployed API container imported that release's
unchanged application modules. It used a new, explicitly created PostgreSQL schema,
`checkfirst=False` table creation and a table-count/schema assertion before seeding
synthetic data. It did not share production tables. The normal application lifespan
was replaced with this fixture's session factory and a no-worker queue adapter;
this was not a production worker or paid-provider test.

Access used a temporary SSH loopback forward to a private container port. No host
port was published and no Caddy/Funnel route was added. Browser login used a separate
synthetic token. The fixture contained two synthetic hypotheses, one held claim,
one held report, and failed process/research jobs; it contained no real incidents.

Observed in actual browser controls:

1. Wrong-token login was rejected; valid synthetic login loaded private queues.
2. Evidence inspection showed actual location, held/sensitive status and provenance.
3. Review/Edit preserved the proposed action; commit remained disabled until the
   separate confirmation checkbox was checked.
4. A confirmed same-location evidence move created an audit receipt, archived the
   original attachment and linked its replacement. The source became inactive after
   losing its last support. The held claim stayed held/sensitive at the target.
5. Attempting to accept the held report was rejected. Edit/review of a new rejection
   decision then succeeded with revision 1. The blocked decision left no audit action.
6. The audit view displayed the accepted operations and their existing domain events.
   Lock/clear removed private records and returned to the empty password form.

HTTP checks against that isolated instance additionally verified:

- A priority adjustment replay returned the same receipt; changing its body with
  the same key and writing against an obsolete state both returned 409.
- Priority changes did not create an active root/job or research lease.
- A failed research-job reset returned 409.
- Reissuing the failed process job created exactly one queued job, with attempt 0;
  the failed original retained its three attempts. Exact replay returned the same
  replacement job.
- The isolated ledger ended with four accepted admin actions. Its six audit rows
  included the move and moderation domain events; those are not extra decisions.

## Cleanup, validation and limits

The temporary server shut down gracefully, its exact schema was dropped, temporary
container files were removed, and the SSH forward was closed. The schema's absence
and unchanged production counts were verified afterward. Readiness remained healthy.
Synthetic screenshots/results remain only in the ignored local `.tools/pr35-live/`
review directory; no fixture routes or data remain deployed.

The overview-refresh repair passes **25 JavaScript tests**, including a DOM-flow
regression proving changed totals appear after Refresh and late responses cannot
repopulate a locked view. **12 focused Python tests** verify the packaged page/CSP,
authorization and command validation; Ruff lint/format checks pass. The previous
release's full regression evidence remains 1,023 Python tests passed, 10 skipped.
No broader Python behavior changed in this repair.

Live acceptance did not exercise real private image bytes, provider-backed job
completion, every graph operation, or a real emerging-candidate decision. Their
existing automated/domain tests are separate evidence; do not claim those actions
were performed in production. The [PR 33 explorer terminal-rendering gate](pr34-deployment-acceptance.md)
remains deferred/unverified at the user's request. PR 36 has not started.
