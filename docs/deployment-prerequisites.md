# PR 38 deployment prerequisites

This checklist uses the selected OVHcloud provider and the recommended R2/Bitwarden
services. Deployment code can be started before all items are ready; the live VPS
rollout needs the access and resources below. Do not provision managed databases,
managed Redis, a load balancer or an additional server.

## User-provided resources and access

1. **OVHcloud VPS-2:** selected on 2026-11-03 because VPS-1 availability is limited.
   Use x86-64 and plain Ubuntu Server 24.04 LTS. Record the actual CPU, RAM and disk
   allocation from the order; confirm datacenter, billing term and renewal total. Install an
   SSH public key and verify access from the deployment machine. Record public IP,
   SSH username and sudo access; keep the SSH private key on the deployment machine.
2. **Public HTTPS endpoint (IP-first):** hold off buying a domain. First configure
   `https://<VPS-IP>/mcp` with a publicly trusted certificate valid for that IP and
   automated renewal, then test adding the connection in ChatGPT. ChatGPT acceptance
   of an IP-literal URL remains unverified. If that test fails because the IP URL is
   unsupported, use a domain/DNS hostname pointing to the VPS instead. No paid
   certificate or separate admin hostname is required.
3. **Cloudflare R2:** enable R2 and create two private Standard-storage buckets,
   for example `travel-scam-objects-prod` and `travel-scam-backups-prod`. Create
   separate bucket-scoped S3-compatible credentials for application objects and
   backups. Record account ID, bucket names and the S3 endpoint from the dashboard.
   Keep public bucket access disabled. Cloudflare DNS hosting is not required merely
   to use R2.
4. **Bitwarden Secrets Manager Free:** create an organization and two projects,
   `prod-app` and `prod-backup`. Create read-only machine accounts/access tokens
   scoped to those projects, one for application secret retrieval and one for
   backup secret retrieval. Retain the owner login, MFA and recovery access yourself.
   The deployment machine needs secure access to the bootstrap tokens; the VPS does
   not need the human master password.
5. **Existing provider accounts:** keep Serper and Sarvam active with sufficient
   quota/credit for the planned live check. Their API keys have already been supplied
   in local `.env`; transfer production values into the secrets manager securely.
6. **Push deployment access:** enable GitHub Actions and configure its `production`
   environment with VPS host/user, a dedicated SSH deployment private key and a
   verified known-hosts entry. The VPS needs no GitHub repository credentials or
   paid registry. Deployments are manually triggered, not automatic on merge.

Readiness update (2026-11-01): both R2 buckets passed live signed read/write,
conditional-write and cleanup checks. Bitwarden project access and initialization
passed: eight app secrets and three backup secrets were created with user approval
and verified by readback. The supplied machine token accesses both projects and the
R2 key pair accesses both buckets; separate scoped credentials are not yet in use.
Return the machine account to read-only after initialization. Public-domain bucket
settings still need dashboard confirmation because the Cloudflare management token
cannot read them (HTTP 403). VPS provisioning remains pending.

OVH API credentials are needed only if we automate VM/firewall provisioning with its
API; an already-created VPS can be bootstrapped through SSH. DNS API credentials
are similarly optional if the DNS record is created manually.

## Created/configured during PR 38

We will install Docker Engine/Compose and Caddy; configure PostgreSQL/pgvector,
Redis, API, worker and scheduler; generate a production database password,
an admin token and backup encryption credentials; wire secret files and scoped
access; configure firewall rules, HTTPS, persistence, logging, backups, migrations
and service restarts; and run health, rollback and restore checks. The user does not
need to install those services on the VPS beforehand.

A ChatGPT account capable of adding the custom MCP connection is needed for PR 31's
later live gate, not to start the hosting foundation. No OpenAI API key is required
for this backend: the application uses Sarvam and local embeddings.

Before real submissions, agree the initial research-usage profile and the
single-server/nightly-backup recovery tradeoff. These are operating decisions,
not extra accounts or paid services to provision.

Keep secret values in Bitwarden or the ignored local `.env`/protected files. Share
nonsecret connection metadata such as hostname, IP, username, bucket/project IDs
and secret identifiers in chat; do not paste private keys, passwords or access tokens.

See [the deployment decision](minimal-mvp-deployment.md) for the architecture,
cost assumptions and final-release requirements.
