# Release 0.1.0 assessment

Update, 23 November: the [database repair and current validation](database-release-repair.md)
supersede the original database-image remediation blocker below. Historical scan
results remain preserved. Residual upstream findings and public-launch gates remain;
the owner's request to merge code into main is not a production deployment approval.

Assessment date: 22 November 2026. Candidate starts from `develop` commit `c1633ca`.
Preparation branch: `codex/release-0.1.0-readiness`, targeting `develop`.
First release PR: **`develop` → `main`**, kept draft until prerequisites are met.

## Scope and release notes

The first MVP provides English location research using Serper and Sarvam 105b,
local embeddings/pgvector retrieval, source provenance and geographically scoped
candidate patterns. It includes bounded report watches, a ChatGPT explorer,
confirmed anonymous incident reporting, private moderation, quality evaluation,
operational limits and a single-Droplet deployment with encrypted R2 backups.

This preparation adds detailed user/admin guides, public product/support/privacy/
terms pages, a narrow public proxy allowlist, a domain-verification route and
portable publishing metadata. The runtime is moved from Debian Bookworm to Trixie
and build-only uv/pip installers are excluded from the serving image after the
container audit revealed findings beyond the Python lockfile. Available Debian
updates are applied at build time, uv is updated to 0.13.9 in the build stage,
and ONNX Runtime/Hugging Face telemetry is explicitly disabled for serving.

There are no database schema changes in this preparation. Version remains `0.1.0`.
Public directory approval, deployment and a release tag are separate actions;
none is implied by opening the release PR.

## Security conclusion

**This is a bounded assessment, not a guarantee of no vulnerabilities. Production
is not cleared for public launch.** The live host still runs
`7d9cfecd3cd039d7c679ed5239c3566a58b250fb` (PR 35), with Pillow 12.3.0,
certifi 2026.7.22 and opentelemetry-api 1.45.1. Develop contains the corresponding
dependency fixes (13.0.0, 2026.10.18 and 1.45.2), but a merge does not update the
running image. Deploy and verify the approved candidate before public launch.

Relevant advisories:
[Pillow](https://github.com/advisories/GHSA-8g5g-8vmr-9782),
[certifi](https://github.com/advisories/GHSA-6q48-6c7w-9gw2),
[OpenTelemetry](https://github.com/advisories/GHSA-mf5v-w3w3-f6cg).

### Application and dependency checks

- Gitleaks 8.30.1, official checksum-verified binary: Git history scan with redaction
  found no matches. This does not certify ignored credentials, provider accounts
  or every possible secret format.
- pip-audit 2.10.0: initial scan of all 96 locked registry packages returned no
  known advisories. A refresh regenerated from the current lockfile also passed after retrying a
  transient PyPI timeout.
- Bandit 1.8.6 under Python 3.12: no parse errors, zero high findings, two medium
  and five low findings. No suppression was added to conceal findings.
- Existing regressions exercise admin authorization, private evidence controls,
  URL acquisition/SSRF defenses, sensitive-error redaction, confirmation and
  idempotency, untrusted-content handling, ingress deadlines and shared quotas.
  Passing those cases is not an independent penetration test or proof against
  novel prompt injection, provider compromise or abuse.

### Static finding triage

| Finding | Context and disposition |
| --- | --- |
| B615, `app/embeddings.py` | The download uses constant full revision `aa8f8b060edb00e03bfdd08813a2949946c8ba55` and a file allowlist. Serving uses local files. Scanner does not resolve the pinned variable; no unpinned request-time model download was found. |
| B310, `deploy/check_services.py` | URL begins with fixed `https://api.cloudflare.com`; operator metadata supplies path components, not a caller-controlled scheme/host. This is an explicit operator check, not a public fetch API. Vendor HTTPS/redirect behavior remains trusted. |
| B404/B603, two deployment modules | Subprocess argument lists run known administrative CLIs without a shell; exporter validates the project UUID. Operator environment and executable integrity remain trusted. |
| B607, `deploy/export_secrets.py` | `bws` resolves through the host PATH. Keep system PATH/executable directories root-owned and unwritable by application/deployment users. This remains a documented operational assumption, not a hardened absolute-path allowlist. |

### Container audit and mitigation

The initial Bookworm image scan with Trivy 0.76.2 reported 371 OS-package findings
(2 critical, 99 high, 144 medium, 116 low, 10 unknown), six pip findings and four
uv/Rust dependency findings. Counts represent package/advisory pairs, not unique
exploitable application defects. They were not silently excluded with ignore files.

The critical SQLite advisory has a fixed Trixie package. Debian notes that the
Bookworm zlib/MiniZip advisory does not affect the built binary package; that is a
specific vendor applicability note, not a reason to waive all OS findings.
See [SQLite vendor status](https://security-tracker.debian.org/tracker/CVE-2025-7458)
and [zlib vendor status](https://security-tracker.debian.org/tracker/CVE-2023-45853).
The final patched Trixie image passed its runtime smoke test. Its unfiltered scan
reports **zero critical, 50 high, 74 medium, 79 low and seven unknown** OS findings;
there are **no findings with a listed distro fix** and no Python-package findings.
The 50 high entries represent **12 unique CVEs**, not 50 independent remote attacks.
Neither `--ignore-unfixed` nor an ignore file was used. This is a reduction, not
zero vulnerabilities. A release reviewer must assess the residual applicability;
these findings are not silently approved by this document.

Final image ID: `sha256:d84c1f1454e4396a92999f95c4b9e1c5b540beeb98d05083f158ab2791c7d42d`.
The Python base digest is pinned in the Dockerfile. Apt security updates can change
future builds; retain the actual deployed image digest and rescan each build.

Residual high-finding assessment (inferences from vendor prerequisites and the
configured workload, not exploit testing):

| CVEs | Applicability assessment |
| --- | --- |
| CVE-2026-76642, 78408, 78409, 78410 | util-linux privileged mount/nsenter operations. Utilities remain present, but the application does not invoke them; production runs non-root with all capabilities dropped, no-new-privileges and a read-only root. Do not grant privileged mode, host namespaces or operator shell execution to untrusted users. |
| CVE-2026-54369 | Privileged libacl pathname operations with attacker-controlled paths. No such application operation was identified; the same privilege constraints matter. |
| CVE-2025-69720 | ncurses infocmp parsing. The binary exists but is not called by the application. Do not process untrusted terminal databases through it. |
| CVE-2026-119373, 119374, 119379 | SQLite FTS5/session parsing of crafted databases or changesets. The app uses PostgreSQL and exposes no SQLite database/query import; SQLite remains in the Python base and must be reassessed if usage changes. |
| CVE-2026-124709 | OpenSSL QUIC server stream handling. This application serves HTTP through Uvicorn behind Caddy/Funnel, not an OpenSSL QUIC server. This inference does not audit the separate host proxies. |
| CVE-2026-16742 | systemd-homed managed-user escalation. `systemd-homed` is absent from the application image; the scanner maps the source package to installed systemd libraries. |
| CVE-2026-9538 | Perl Archive::Tar processing. No Archive/Tar.pm was found in the base Perl library tree and the app does not invoke Perl archive processing. |

Vendor prerequisite examples: [util-linux](https://security-tracker.debian.org/tracker/CVE-2026-78410),
[ACL](https://security-tracker.debian.org/tracker/CVE-2026-54369),
[SQLite](https://security-tracker.debian.org/tracker/CVE-2026-119373),
[OpenSSL](https://security-tracker.debian.org/tracker/CVE-2026-124709).

The configured PostgreSQL/pgvector and Redis images were also scanned:

| Image | Observed findings and action |
| --- | --- |
| `pgvector/pgvector:0.8.6-pg17-trixie`, digest `724a4041…` | 14 critical, 284 high, 324 medium, 204 low, 21 unknown across OS and gosu records. Pulling the current tag returned the same digest. Includes fixable PostgreSQL 17.11/OS advisories and gosu's Go dependencies. **Release blocker: prepare a patched compatible database image and exercise backup/restore and vector queries before rollout.** |
| Earlier cached Redis digest `858f009f…` | Five high, 17 medium, two low OS findings. No service was restarted by scanning/pulling. |
| Current Redis digest `7f7d30b7…` | Zero critical/high, one medium zlib finding (`CVE-2026-4738`, scanner lists 1.4-r0 as fixed). Compose now pins this reviewed digest so a rollout cannot silently reuse the older tag. Remaining vendor applicability/patch availability must be reviewed. |

The [full compact scan inventory](security/release-0.1.0-vulnerabilities.json)
contains every reported package/advisory entry, including lower severities and
the original image results. Counts do not establish exploitable code paths, but
fixable database-server findings cannot be dismissed because the port is private.
No production database image or data was changed during this assessment.

A newer official `pgvector/pgvector:0.8.7-pg17-trixie` candidate was also pulled
and scanned (digest `89947206f52eac3a9df9b663df7e76987cbdc623be6576b3a524953d568700d7`).
It is **not configured or deployed**: the extension/database upgrade needs a
backup/restore and vector-query compatibility gate. Its scan contains {'LOW': 176, 'HIGH': 151, 'MEDIUM': 207, 'UNKNOWN': 17, 'CRITICAL': 2}
package/advisory matches, including 120 with listed fixes. The full inventory
retains these results; changing a version tag alone is not security clearance.

The Ubuntu kernel, Docker daemon, host Caddy/Tailscale binaries and cloud account
permissions have not had a comprehensive vulnerability audit here. Run their
vendor updates/advisory checks as part of ongoing operations. Do not interpret
application-image results as entire-host clearance.

### Live boundary checks

Read-only checks over the authorized network found:

- UFW active; SSH allowed on `tailscale0`, public HTTP/HTTPS for the filtered
  service and ACME, UDP Tailscale transport. Application 8000, filtered proxy 8080
  and Caddy admin 2019 listen only on loopback; no public PostgreSQL/Redis listener.
- Hostname `/health` 200; `/admin`, `/ready`, `/docs`, `/openapi.json` and
  `/v1/admin/operations` 404. MCP initialization and listing returned 200 with
  twelve tools and boolean effect annotations. No research or reports were created.
- New product/policy paths currently return 404 in production: they are prepared
  source, not published policies. Tests from an operator connected to the tailnet
  do not independently establish every public relay region is reachable.
- R2 public-domain toggles were previously confirmed disabled by the owner;
  the management token could not independently inspect that setting. This
  assessment does not mislabel that earlier confirmation as a fresh provider audit.

## Functional validation

- Python suite with local PostgreSQL/Redis/MinIO: **1,070 passed, 10 skipped**.
- Updated Redis digest regression: **549 passed, 10 skipped** (infrastructure,
  operational controls, public pages and embeddings).
- UI logic: **25 passed**. These are not actual ChatGPT rendering acceptance.
- Quality replay: execution, thresholds and baseline comparison passed; no
  regressions. The baseline includes known imperfect matching, not 100% accuracy.
- Ruff lint/format passed. Public pages have six new tests, including hashed-style
  CSP, read-only routes, headers, HEAD behavior and no directory serving.
- Final Linux image build and non-root/read-only smoke passed: database/Redis/object
  readiness, four HTML pages, twelve MCP tools, worker imports and a real offline
  384-dimensional embedding. No live provider call or report submission was made.
- Actual local Caddy probe passed product/health GET/HEAD, POST rejection, exact
  plain-text challenge, MCP proxying and private-route denials. No host proxy change
  was deployed. Caddy configuration adaptation also passed.
- GitHub token can read/create PRs but Actions history returns 403. The owner explicitly waived agent verification of remote CI. Remote CI is not
  claimed passed from local checks; inspect required checks before merging.

## Merge, deploy and launch checklist

1. Merge the preparation PR into `develop` after review and green CI. This updates
   the existing draft `develop` → `main` release automatically; do not release the
   preparation branch as a substitute for `develop`.
2. Review the final aggregate release diff, container findings and required checks.
   Complete database-image remediation and unresolved security repairs/triage
   before removing draft status. Do not deploy the stale database tag merely
   because the application image was hardened.
3. Merge the release only when approved. Dispatch the deployment workflow for the
   exact approved ref and validate the active SHA/versions; merging alone does not
   deploy. Install/validate the new host Caddy configuration separately.
4. Verify public allowlist/private denials, actual ChatGPT desktop/mobile flows,
   queue outcomes and a representative recovery including R2 provenance objects.
5. Confirm off-host backup-passphrase recovery and configure/test external alerts.
   Shared app/backup credential scope and indefinite retention remain disclosed
   limitations. Never imply rejected/hidden records have been physically erased.
6. Complete the [publishing runbook](publishing-readiness.md), actual demo, publisher
   and domain verification, platform review and explicit publication.

Detailed operating instructions: [user guide](user-guide.md),
[admin guide](admin-guide.md). A draft release PR is reviewable preparation;
it is not approval to merge, deploy, tag or publicly launch.
