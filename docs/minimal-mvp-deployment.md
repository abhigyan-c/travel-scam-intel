# Minimal MVP deployment decision — 2026-10-31

Status: the single-server/no-load-balancer cost constraint and **OVHcloud as the
hosting provider** are confirmed by the user (2026-10-31). Region, final SKU, billing
term and checkout/renewal price remain to be confirmed. On 2026-11-01 the user
confirmed R2 and Bitwarden resources ready; VPS provisioning is pending.
This supersedes the original multi-service AWS release target. It authorizes planning,
not provisioning, spending, exposing the local machine, or declaring a release complete.

## Recommended starting point

Run one small **x86-64 Linux VPS** with Docker Compose. Start with **2 vCPU, 4 GB RAM**
and at least **40 GB SSD**, then validate that memory envelope with the actual model
and workload. OVHcloud is the selected provider; confirm its entry VPS at checkout.
Cloudflare R2 Standard is the proposed private object store and off-server backup
destination. First test HTTPS directly on the VPS public IP; a domain is a fallback.
Caddy terminates HTTPS on that
same machine and forwards to one API process. There is no separate load balancer.

At about 100 user requests/day, always-on services and memory for the database and
local embedding model dominate infrastructure sizing. The request count alone does
not establish peak concurrency or research cost. Four GB is a planning minimum, not
a measured capacity guarantee. A 2 GB machine is not the default while both API and
worker can load the local embedding model. If profiling shows inadequate headroom,
move to 8 GB instead of changing model semantics or relying on swap for normal work.

```text
Internet / ChatGPT
        |
        v
One VPS: Caddy HTTPS -> FastAPI REST / MCP / admin UI
        |                    |
        |               PostgreSQL + pgvector
        |               Redis / arq queue
        |                    |
        +------------- one arq worker
                         + existing 15-minute scheduler

API / worker -> private R2 objects
Nightly database backup -> separate private R2 backup bucket
```

The current code already uses PostgreSQL/pgvector, Redis/arq, Docker and an
S3-compatible boto3 storage boundary. Preserve these interfaces. MinIO remains the
local development store; the production profile would use R2 directly, avoiding a
second object-storage process and local object-disk capacity on the VPS.

## Provider comparison and pricing evidence

Checked 2026-10-31. Prices below are advertised starting prices, not a binding
checkout quote; verify region, billing term, renewal price, taxes and IPv4 before
ordering. Do not select an annual/prepaid promotion just to match a headline price.

| Option | Published baseline | Assessment |
| --- | --- | --- |
| OVHcloud entry VPS | India page advertises 2 vCore / 4 GB / 40 GB from ₹420/month before GST; 4 vCore / 8 GB / 75 GB from ₹810 | First checkout candidate for a permanent low-cost deployment. Regional pricing/availability must be confirmed. |
| Hetzner CX23 | Official price-adjustment table lists €5.49/month excluding IPv4 and VAT; public entry-plan page currently marks it unavailable | Alternative if actually orderable; do not base the release on unavailable capacity or older advertised prices. |
| AWS Lightsail | Public-IPv4 Linux bundle: 4 GB / 2 vCPU / 80 GB at $24/month | Simple AWS fallback using the same single-VM design, but a higher baseline than the proposed entry VPS. |
| DigitalOcean Basic Droplet | 512 MiB $4/month; 1 GiB $6; 2 GiB / 1 vCPU $12; 4 GiB / 2 vCPU / 80 GiB $24 | The low entry price buys less RAM. Compare the 4 GiB plan against OVH's 4 GB quote; credits or a different checkout quote may change the effective cost. |
| Render free tier | Free PostgreSQL expires after 30 days; free services lack persistent disks and free background-worker instances | Useful for a temporary demo, not the target for this persistent API/worker/database stack. |

Sources: [OVHcloud India](https://www.ovhcloud.com/en-in/vps/),
[Hetzner pricing revision](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/),
[Hetzner entry-plan availability](https://www.hetzner.com/cloud/cost-optimized/),
[Lightsail prices](https://aws.amazon.com/lightsail/pricing/),
[DigitalOcean Droplet prices](https://www.digitalocean.com/pricing/droplets),
[Render free-service limits](https://render.com/docs/free).

The proposed hosting envelope is **roughly ₹500–₹1,000/month**, assuming the entry
or next-smallest VPS is available near those advertised prices and object usage is
small. This is a planning envelope, excluding domain registration, Serper/Sarvam,
tax/region differences and any paid storage overage; it is not a guaranteed total bill.
If checkout materially exceeds it, compare providers again before purchase.

R2 Standard currently includes 10 GB-month storage, 1 million Class A and 10 million
Class B operations monthly, with no internet egress charge. Standard storage above
the allowance is $0.015/GB-month; operation charges can also apply. Documents, images
and retained backups all consume storage. Do not assume the free allowance lasts
forever or delete provenance to remain inside it.
[R2 pricing](https://developers.cloudflare.com/r2/pricing/).

R2 documents boto3 usage and the conditional writes we need; `us-east-1` aliases to
its `auto` region. Still verify our actual client operations, checksums, private media,
permissions, timeouts and immutable-write behavior against a real bucket before release.
[boto3 guidance](https://developers.cloudflare.com/r2/examples/aws/boto3/),
[S3 compatibility](https://developers.cloudflare.com/r2/api/s3/api/).

Choose the cheapest suitable region after checking account eligibility and the quote.
Europe is acceptable for the cost-first proposal if there is no India-only residency
requirement; Mumbai/Singapore is preferable if similarly priced or regionally required.
No residency requirement is inferred from the user's timezone.

## Minimal operating profile

- One API process and one worker job at a time initially; bound CPU threads and DB
  connection pools. Expose these deployment knobs where currently hardcoded.
- Pre-download the pinned BGE model during setup; persist its cache. No GPU and no
  local hosting of Sarvam 105B: Sarvam and Serper remain external APIs.
- Keep the scheduler in the existing worker, without EventBridge, Lambda or another
  scheduler service. Proactive research stays opt-in with a deliberately small budget.
- Preserve cached reads, background enrichment, partial results, provenance and all
  moderation protections. Do not remove the worker or turn research into a blocking
  web request just to fit a hosting free tier.
- Use host-persistent database/Redis volumes, automatic service restarts and rotated
  application/container logs. Use a small external uptime check and disk/backup-age
  checks rather than deploying a large monitoring stack.
- Use a production Compose profile and minimal Terraform/cloud-init for the chosen
  VM/firewall, plus versioned deployment/backup/restore scripts. Reproducibility does
  not require managed AWS services or a large provisioning framework.

The user selected an IP-first HTTPS approach on 2026-10-31: defer buying a domain
and first test `https://<VPS-IP>/mcp` as the ChatGPT MCP connection. Configure a
publicly trusted certificate valid for the VPS IP with automated renewal. Let's
Encrypt now offers IP certificates valid for 160 hours (roughly six days), so
renewal must be monitored. Caddy's default local certificate for an IP address is
not sufficient; explicitly configure a supported IP-certificate ACME workflow.
[IP certificates](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability),
[Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).

OpenAI documents a public HTTPS endpoint requirement but does not explicitly
confirm acceptance of IP-literal MCP URLs. Treat this as a live compatibility gate,
not a confirmed integration. Register the endpoint as a custom MCP connection in
ChatGPT and verify tool discovery and a real call. If ChatGPT rejects the IP URL,
use a DNS hostname pointing directly to the same VPS with trusted HTTPS. A domain
purchase is conditional on that outcome, not a prerequisite to starting PR 38.
[ChatGPT connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt).

Only HTTPS/HTTP and restricted SSH should be reachable publicly. PostgreSQL and
Redis stay on the private Compose network. R2 buckets stay private; media continues
through authorized application routes. The proposed production secrets-manager
integration below replaces the earlier suggestion to keep all production credentials
in a persistent server `.env`. Local development `.env` usage is unchanged.

### Proposed production secrets management

Use hosted **Bitwarden Secrets Manager Free**, selected and provisioned by the user. Its
current free plan supports two users, three projects and three machine accounts,
which covers this single-operator deployment without another server process.
[Plan limits](https://bitwarden.com/help/secrets-manager-plans/).

Store provider keys, database credentials, R2 application/backup credentials and the
admin token centrally. Use project-scoped read-only machine access; keep the human
vault/master password off the VPS. The VPS still needs a protected bootstrap machine
token, stored outside Git/images and inaccessible to ordinary application containers.
The secrets manager does not remove that bootstrap trust or protect a compromised
root account from secrets already in use.

Fetch secrets through the supported CLI during deployment and boot, not on each
request. Deliver per-service read-only files using Compose secrets, preferably backed
by a restricted runtime directory in memory. Add file-based application settings
support in the deployment slice; the existing code currently expects environment
settings. Verify actual file ownership/read permissions for each container user.
[Machine permissions](https://bitwarden.com/help/machine-accounts/),
[Compose secret delivery](https://docs.docker.com/compose/how-tos/use-secrets/).

Ordinary Compose secret files are bind mounts, not automatically an encrypted vault.
Avoid printing secret exports in shell traces, logs, `compose config` output or CI.
Refresh/recreate affected services after coordinated credential rotation; changing
a vault value alone does not rotate a database password or a running process's copy.
Running processes need no per-request vault access. New deployments/cold boots fail
closed if required secrets cannot be fetched; test that behavior and retain a secure
operator recovery procedure. Do not promise enterprise auditing or automatic provider
rotation as part of the free-tier integration.

The server is a **single point of failure**. A restart or deployment can cause a
short outage; there is no automatic failover or zero-downtime promise. We operate
patches, database maintenance and recovery ourselves. These are the main tradeoffs
for the smaller fixed bill.

Nightly encrypted, consistent PostgreSQL dumps go to an off-server backup bucket
with separate credentials/retention. Back up required configuration securely and
retain the existing raw-object keys referenced by restored database records. A
nightly-only database policy has an initial recovery-point target of up to 24 hours;
agree that tradeoff before live traveler submissions. Provider VM snapshots alone
are not the database recovery plan. Demonstrate restoration onto a replacement VPS
before calling this a release; measure recovery time rather than promising an SLA.

## Research cost is a separate decision

One hundred cached reads can be cheap. One hundred previously unseen destinations
can each cause searches, page fetches, extraction and enrichment. The server bill
does not cap these provider charges. Existing PR 30 daily reservations apply to
proactive jobs, not all user-triggered research and continuations.

Before broad public access, choose a conservative research profile and determine
whether a global daily resource quota must also cover on-demand work, retries and
enrichment. When work is deferred, return persisted findings and honest research
state, not invented scams or a false completed-empty result. Monetary caps and their
numeric values remain a separate user decision; no dollar ceiling is invented here.

## Changes to the remaining implementation plan

The concrete account/access checklist is in
[PR 38 deployment prerequisites](deployment-prerequisites.md).

The original ECS/Fargate, ALB, RDS, ElastiCache, Secrets Manager, CloudWatch and AWS
scheduled-trigger deployment is no longer the MVP target. An eventual scale-up can
reuse the same interfaces, but it is not a prerequisite for release.

- PR 35: complete the minimal admin interface; no cloud dependency.
- PR 36: establish quality fixtures locally; no cloud dependency.
- PR 37: profile within the chosen 4 GB envelope, configure single-job concurrency,
  add the required public-exposure controls and keep monitoring lightweight.
- PR 38: replace AWS multi-service provisioning with the single-VPS Compose/TLS/R2
  profile, migrations, backups, restore drill, rollback and release checks.
- PR 31 remains deferred. Once an HTTPS preview exists, resume PRs 31–34 and their
  real ChatGPT read/report gates. The MCP adapter itself still has to be implemented;
  deploying the current backend does not complete PR 31.

The user authorized the early PR 38 hosting foundation on 2026-11-01. Prepare it
locally while the VPS provisions, then establish a trusted HTTPS preview before
resuming PRs 31–34. Complete the remaining admin, evaluation and hardening work
and the final PR 38 release gate afterward. Keep all existing PR
numbers and review gates. A preview is not the final release, and no new numbered
feature PR is implied by this planning document.

Deployment is a manually triggered GitHub Actions push: build the selected commit,
transfer its image and deployment files over pinned-host SSH, fetch runtime secrets
on the VPS, migrate and restart with health checks. No registry or repository key is
required on the VPS. See [the foundation runbook](../deploy/README.md) for implemented
configuration and outstanding live gates.

The configurator URL visible when the provider was selected contained
`pricing=upfront12`. Provider selection does not itself confirm a 12-month upfront
purchase. Check the selected billing term and renewal total before ordering.

Before provisioning, confirm the OVH checkout price and billing term, region, the IP-first HTTPS test plan, the
single-server downtime/recovery tradeoff and the initial provider-usage policy.
