# DigitalOcean 2 GiB sizing experiment — 2026-11-03

**Verdict: the $12 Droplet is a plausible target for a constrained MVP, but the
current full feature set is not safe under its memory budget without changes.**
Ordinary reads, real embeddings and queued reports fit. Concurrent legal image
uploads repeatedly killed the API. The 4 GiB control survived that workload even
when CPU allowance stayed at one core. The earlier 4 GiB recommendation is therefore
supported for the current implementation, but 4 GiB is not an intrinsic requirement
for every operation or necessarily the eventual optimized MVP minimum.

DigitalOcean currently lists Basic Regular at $12/month for 1 vCPU / 2 GiB / 50 GiB
SSD and $24/month for 2 vCPU / 4 GiB / 80 GiB SSD, before applicable tax. The $18
2-vCPU / 2-GiB option does not add RAM. [Official prices](https://www.digitalocean.com/pricing/droplets).

## What was actually measured

This was **local Docker Desktop/WSL emulation on a Core i5-14600K**, not a purchased
DigitalOcean VM. PostgreSQL 17/pgvector, Redis/arq, Caddy, the API and worker shared
one cgroup: 1536 MiB aggregate memory, one logical CPU/quota and zero swap. This
leaves an **estimated 512 MiB** of the nominal 2 GiB for Ubuntu, Docker and host
services. The 4 GiB control used 3584 MiB for the stack, with both 2-CPU and 1-CPU
trials. Per-container limits were not mistaken for a whole-machine limit.

Both Python processes loaded the real pinned `BAAI/bge-small-en-v1.5` model. R2 was
represented by an off-budget local MinIO process. Sarvam/search/fetch responses were
synthetic, with simulated delays; actual local parsing, language detection, SQL,
validation, scoring and embedding inference ran normally. No production data or
paid provider calls were used. The proxy used HTTP, so TLS cost is not measured.

Initial corpus: 50 locations, 1,000 patterns, 5,000 claims/documents, five supporting
observations per pattern. Growth corpus: 10,000 patterns and 50,000 claims/documents.
Stored size-test vectors were placeholders; runtime matching used real embeddings.
The process-cold model test did not flush the host's filesystem cache.

| Workload under the 2 GiB budget | Measured result |
| --- | --- |
| 100 persisted-data reads, one client | 100/100 HTTP 200 with findings; p50 203 ms, p95 489 ms |
| 100 persisted-data reads, five clients | 100/100 with findings; p95 1.97 s |
| First semantic match / 20 warm serial matches | 1.33 s first match; warm p95 528 ms; all matched |
| Five queued report jobs during reads | All processed; no processing error codes; read p95 1.48 s |
| 100 reads during synthetic on-demand research | All returned findings; p95 1.09 s |
| Loaded API/worker idle | 1,269–1,339 MiB including page cache; about 887 MiB peak anonymous + kernel + shared memory in that interval |
| Encrypted backup of growth corpus | 2.71 s; about 16 MiB encrypted dump; no OOM |
| One allowed 10-megapixel PNG upload | HTTP 201, 2.17 s; memory reached the imposed cap but no OOM |
| Five simultaneous allowed 10-megapixel PNG uploads | **5/5 HTTP 502; API OOM-killed and restarted**, reproduced twice |
| Same image burst, 4 GiB / 2 CPU control, models warm | 5/5 HTTP 201; sampled stack peak 1,821 MiB |
| Same image burst, 4 GiB / **1 CPU** memory-only control | 5/5 HTTP 201; slowest 2.31 s; sampled stack peak **1,926 MiB** |

There were no OOM kills in the text/embedding/report/growth/backup phases. There
were two OOM kills across the two low-memory media trials, each accompanied by an
API restart. Swap remained zero. Resource sampling was every 250 ms; kernel peak
and OOM counters were also inspected. HTTP 200 with `unavailable` was counted as a
functional failure, not reported as success.

## Failures that a larger Droplet did not fix

- At the growth corpus, ordinary pattern search/summary probes took approximately
  1.08 s / 0.70 s on one CPU. The `recent` endpoint exceeded the ten-second bound
  on **both** configurations; exploratory unbounded queries exceeded 40 seconds.
  This is a query-cost/CPU finding, not evidence of RAM exhaustion. Profile the
  occurrence/public-evidence SQL and its execution plan before release.
- On the same growth corpus, three-client semantic-match tests returned `unavailable`
  for 10/20 requests on 2 GiB and 11/20 on the 4 GiB control. The embedding wrapper
  uses a nonblocking lock and returns `embedding_busy`; the service collapses this
  into `embedding_unavailable`. Both configurations had enough memory for serial
  matches. The lock/admission behavior is the leading code-level explanation;
  exact exception causes were not instrumented per request. Add bounded waiting
  or explicit backpressure rather than expecting RAM alone to fix this.
- Synthetic on-demand research ended partial (`research_incomplete`) after its
  configured call budget; fixture claims omitted locations and required review.
  It exercised fetching/parsing/checkpoints, but is not proof of a successful real
  research round or real provider latency. The separate report pipeline did
  complete claim application and real worker embeddings.

At half a CPU quota on the growth corpus, 20 serial search/summary reads all passed
with p95 3.27 s; ten serial matches all matched with p95 4.34 s. This is a sensitivity
test, not a promise that it reproduces DigitalOcean CPU steal. Basic shared CPU can
vary. [DigitalOcean plan guidance](https://docs.digitalocean.com/products/droplets/concepts/choosing-a-plan/).

## Estimates and resulting decision

The 512 MiB OS/Docker allowance is an estimate, not measured guest-OS usage. Loaded
text-work memory shows that 2 GiB can be workable; it does not justify claiming a
4 GiB hard minimum. Conversely, the memory-only image control reached 1,926 MiB
including cache, or about 1,560 MiB for anonymous + kernel + shared memory. Adding
the assumed 512 MiB host allowance leaves no useful 2 GiB safety margin. Some file
cache is reclaimable, so total cgroup memory is not the same as irreducible RAM.
This is why both cache accounting and actual OOM outcomes are reported.

One hundred requests/day averages one every 864 seconds, far below the measured
ordinary-read rate. That supports the smaller plan for low-concurrency text work;
it does not rule out five uploads arriving together. Future MCP/admin code, TLS,
patching and real provider/network behavior also need headroom and a real-host
test. The experiment was not a multi-day leak/soak test.

The growth database was about 295 MiB. Linear extrapolation of this synthetic
corpus suggests roughly 6 KiB per additional claim/document/evidence bundle, not
an exact production storage forecast. With raw objects in R2, 50 GiB SSD appears
adequate initially. Retained deployment archives/images and logs still need pruning.

For **current code**, choose the 4 GiB plan if deploying without memory safeguards.
To pursue **$12/month**, first bound expensive image processing concurrency/memory,
add matching backpressure, fix the large-`recent` query and repeat these tests.
Retain one API process, one worker job and the small DB pools. Serializing image
work is a proposed mitigation, not a tested fix or a guarantee of final 2 GiB safety.
Do not use swap as evidence that a memory budget passed. No Droplet was purchased.

## Evidence and reproduction

- [Request outcomes](../benchmarks/results/2026-11-03/request-results.json)
- [Resource phase summaries](../benchmarks/results/2026-11-03/resource-summary.json)
- [Compressed 2,605-sample CPU/memory trace](../benchmarks/results/2026-11-03/memory-cpu-trace.csv.gz)
- [Harness and limitations](../benchmarks/droplet_sizing/README.md)

Initial request summaries were captured from console output before interrupting
the oversized `recent` phase; no raw per-request timings were fabricated afterward.
The checked-in summaries disclose this. Bounded probes and media controls have
their saved JSON outcomes. Harness setup failures were corrected before measuring
application workloads and are not counted as capacity failures.
