# Database release repair — 23 November 2026

The release now builds and transfers an immutable `travel-scam-postgres:COMMIT_SHA`
alongside the application image. Activation checks both images before stopping
services. Restore drills use the selected release's patched database image.

The image preserves Debian/glibc, PostgreSQL major 17, pgvector 0.8.6 and the
postgres UID. Signed Debian updates bring PostgreSQL to 17.12; the build rejects
versions below 17.12. gosu 1.20 is rebuilt from checksum-verified source with
Go 1.27.4 and patched Go dependencies. Production has not been changed.

Validation completed:

- 1,070 Python tests passed, 10 skipped, on the patched database and FastEmbed 0.9.1.
- 25 UI tests passed; quality replay passed without baseline regressions.
- Isolated 17.11 → 17.12 data-volume upgrade, HNSW/vector queries, collation-version
  check and restoration of an old-version dump all passed. The reproducible test
  is `python -m deploy.check_database_upgrade IMAGE` and is included in CI.
- Linux application readiness, twelve MCP tools, worker imports and actual offline
  embeddings passed in a non-root/read-only container.
- Current locked Python dependencies passed pip-audit with no known advisories.
- Final database Trivy scan has no listed fixable vulnerabilities and no gosu
  findings. Full unfiltered evidence: [inventory](security/database-repair-inventory.json).

## Residual risk and deployment boundary

The OS scan still reports 1 critical, 123 high, 120 medium, 159 low and 16 unknown
package/advisory matches. These are not a count of proven application exploits.
The critical item is **CVE-2026-6653 in libxml2**, for which Debian Trixie currently
provides no fix. Debian classifies it as a minor issue; it requires crafted XML
parsing. The application uses parameterized PostgreSQL operations and does not
expose arbitrary SQL or SQL/XML parsing, but this is a reachability assessment,
not a library patch or a guarantee against exploitation. Keep database access
private and revisit this assessment if SQL/XML capabilities or database access change.
See the [Debian advisory](https://security-tracker.debian.org/tracker/CVE-2026-6653).

This repair closes the available database-server/OS/helper patching and upgrade
validation work. It does **not** claim a vulnerability-free release. The owner has
requested the code be merged into main; merging is separate from deploying or
publicly launching. Keep residual security review, production backup/recovery,
policy publication, ChatGPT acceptance/demo and platform approval on the launch
checklist. No deployment or public launch is performed by merging these PRs.

Before deployment, create and verify a fresh encrypted backup. Retain both old
images. Do not automatically downgrade a running database after failure. For a
local restore drill, explicitly set `POSTGRES_IMAGE=travel-scam-postgres:COMMIT_SHA`;
on the VPS the script can derive that tag from `active-release`. A data-volume
upgrade must retain PostgreSQL major 17 and the existing volume mount.
