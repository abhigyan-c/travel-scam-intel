# DigitalOcean $12 sizing experiment

The question is whether the existing single-server stack can fit 2 GiB RAM and
one shared vCPU, not whether an empty FastAPI app can start. This benchmark uses
real PostgreSQL/pgvector, Redis/arq, Caddy, API and worker, including the real pinned
BGE embedding model in both Python processes. It never uses production credentials.

## Method

- A single cgroup v2 parent limits the **aggregate** five-service stack to 1536 MiB,
  one CPU quota and logical CPU 0, with swap disabled. The remaining 512 MiB of a
  hypothetical 2 GiB Droplet is reserved for Ubuntu, Docker and host services.
- A synthetic MinIO process represents off-box R2 and is outside this group, as
  are the load generator and cgroup sampler. No model downloads occur in requests.
- LLM/search/fetch boundaries are deterministic simulations with 0.5-second LLM
  and search delays and 0.1-second fetch delays. Local HTML processing, language
  detection, schema validation, database transactions and embedding inference run
  normally. External inference is not hosted on the Droplet in production either.
  This does **not** measure real provider latency, rate limits or quality.
- Seed 50 locations, initially 1,000 patterns and 5,000 supporting claims/documents,
  then grow to 10,000 patterns and 50,000 claims/documents. Each pattern has five
  observations and each document is about 1 KiB. Stored vector columns contain
  deterministic placeholder vectors for space/index load; matching computes real
  embeddings. This synthetic corpus is not a quality evaluation.
- Exercise serial reads, bursts of five readers, cold/warm semantic matching,
  concurrent matching, five report jobs, on-demand research, and corpus growth.
  Record returned domain statuses as well as HTTP status codes.
- Sample aggregate memory, anonymous/file/kernel accounting, CPU usage/throttling,
  swap and OOM counters every 250 ms. The controller's peak counter additionally
  captures spikes between samples. Do not add PostgreSQL process RSS values:
  shared pages would be double-counted.

## Decision rules

OOM kills/restarts, failed jobs from resource pressure, or matches returning
`unavailable` count against suitability even with HTTP 200. Report partial results
from explicit application limits separately from capacity failures. Assess steady
and burst latency separately; 100 requests/day averages one request every 864 s,
but does not constrain bursts. Leave a margin for unimplemented MCP/admin code and
operational work rather than using every byte of RAM.

This is an emulation on Docker Desktop/WSL with a Core i5-14600K, **not a benchmark
of a purchased DigitalOcean VM**. CPU quota is an upper bound; actual shared-vCPU
hardware/steal, network latency, storage and the full guest OS can differ. Record
the measured facts and estimates separately. Repeat on a real Droplet before
turning a local result into a production guarantee.

## Reproduction

Build `travel-scam-sizing:20261103` from the current Dockerfile. The pinned model
must already be in `.tools/embedding-cache`, and the local development MinIO image
must be built. On the Docker Linux host create a fresh cgroup:

```sh
mkdir /sys/fs/cgroup/travel-scam-sizing-20261103
echo 1610612736 > /sys/fs/cgroup/travel-scam-sizing-20261103/memory.max
echo 0 > /sys/fs/cgroup/travel-scam-sizing-20261103/memory.swap.max
echo '100000 100000' > /sys/fs/cgroup/travel-scam-sizing-20261103/cpu.max
echo 0 > /sys/fs/cgroup/travel-scam-sizing-20261103/cpuset.cpus
```

From the repository root run `python -m benchmarks.droplet_sizing.run`. It creates
only a dedicated Compose project, synthetic secrets and ignored artifacts under
`.tools/droplet-sizing-20261103-run3`. Use a fresh output path/data volume to repeat.
The test HTTP ports bind only to loopback. The runner retains containers/data for
inspection; clean up only the named experiment project after collecting results.
Creating/reading cgroups requires host Docker privileges; never point the seeder
at a real database or run this automatically in production.

Then run these explicit follow-ups:

```sh
docker build -t travel-scam-sizing-backup:20261103 -f benchmarks/droplet_sizing/backup.Dockerfile .
python -m benchmarks.droplet_sizing.probe
python -m benchmarks.droplet_sizing.media_probe
python -m benchmarks.droplet_sizing.media_probe --memory-only
python -m benchmarks.droplet_sizing.summarize
```

The media test deliberately exercises OOM risk in the disposable project. It warms
the API model before both configurations and also tests 4 GiB with **one CPU** to
isolate memory from the 2-CPU control. Generated 10-megapixel PNGs are within the
current accepted dimensions/byte limits; no real user data is uploaded. Supplementary
probes cancel only active queries in this isolated database after a ten-second
client deadline. Counts are closed-loop clients, not 100 simultaneous arrivals.

The first exploratory run had object-store permissions/arq harness setup failures;
those were corrected before measuring workloads. Its first unbounded grown-corpus
read phase was interrupted after long queries exceeded client timeouts. Initial
request summaries were retained from console output, not reconstructed per-request
timings. The final runner checkpoints request summaries and uses the bounded probe
for corpus growth. No successful large-`recent` latency percentile is claimed.

The checked-in [measurement evidence](../results/2026-11-03/request-results.json)
and compressed resource trace contain synthetic outcomes/counters only.
See [the assessment](../../docs/digitalocean-sizing-experiment.md).
