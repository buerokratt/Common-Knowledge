# Content External Service

Reads CKB's cleaned corpus, chunks it, and publishes it to an external
retrieval destination — an object store (S3-compatible / Azure Blob) or the
llm-module, chosen per deployment. See
[content-external-pipeline.md](../content-external-pipeline.md) for the full
design and
[content-external-pipeline-task-breakdown.md](../content-external-pipeline-task-breakdown.md)
for the stage-by-stage build plan.

## ⚠️ Global Classifier safety — read this first

This service shares a database and an S3 bucket with the Global Classifier's
data path (the hourly ZIP job at `GET /ckb/client/data/import`). Six rules
keep that path intact. Each has a **silent** failure mode if violated — there
is no error, no log line, just a Classifier that quietly goes stale.

| # | Rule | What breaks if violated |
|---|---|---|
| 1 | **Never write anything under `uploads/scrapped-data/`** | Whatever is written there gets zipped into the Classifier's payload on the next hourly run. This service's work directory is a separate volume, never a subdirectory of the scraped tree |
| 2 | **Never write `zipped/`** | Corrupts or races the artifact the Classifier downloads |
| 3 | **Never write any `agency_management.agency` column** — in particular `data_hash`, `zipped_data_url`, `zip_dirty`, `is_zipping` | `data_hash` is the Classifier's change-detection token. Writing it makes the Classifier skip a real change or re-pull unnecessarily |
| 4 | **Never call `agency/update-zip-dirty` or `agency/update-data-hash`** | Same as (3), one indirection away |
| 5 | **Never claim `is_zipping`** | `pipeline/zip.yml` selects on `zip_dirty = TRUE AND is_zipping = FALSE`. An agency held by this service would be skipped by the zip drain, and the Classifier's ZIP would silently go stale. This service owns no flag in the `agency` table |
| 6 | **Read S3 with its own boto3 client, not through file-processing's HTTP API** | file-processing both builds the ZIP and mints the Classifier's presigned URLs. It is squarely in the Classifier's path and must not take extra load from this service |

Any change to this service should be checked against these six rules before
it is checked against anything else.

## Setup

### Docker (matches how CKB runs it)

```bash
docker compose up content-external
```

Builds from [`Dockerfile`](Dockerfile) using the repo root as build context
(root `pyproject.toml` + `uv.lock`, `content-external/` copied in), the same
pattern as `cleaning/`.

### Local development

```bash
uv sync --frozen --extra content-external --group dev
CONTENT_WORK_DIR=/tmp/content-external-work \
  uv run uvicorn exporter.api.app:app --reload --port 8125
```

`CONTENT_WORK_DIR` has no default and the service refuses to start without
it — see the note in the table below.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `CONTENT_WORK_DIR` | **yes — no default** | Path to this service's own working volume — holds only the run lock and the deletion journal. Must never be under a `scrapped-data` tree (rule 1), asserted at startup against both the configured value and its resolved real path. There is deliberately no fallback: one would let a deployment that forgot this variable write to a path that is not the mounted volume and keep running. Compose and the chart both set `/var/lib/content-external` |
| `VAULT_ADDR` / `VAULT_TOKEN_PATH` / `VAULT_SECRET_PATH` | no | Vault Agent sidecar, credentials fetched per task. `VAULT_SECRET_PATH` has no default — the Stage-H Azure value is `blob/connections/azure_blob/content` |
| `CONTENT_SINK` | no (default `object_store`) | `object_store` \| `llm_module` — resolved once, in one factory, at run start |
| `CONTENT_EXTERNAL_STORE_BACKEND` | no (default `s3`) | `s3` \| `azure_blob` — backend beneath the `object_store` sink |
| `MANIFEST_STORE_BACKEND` / `MANIFEST_STORE_ENDPOINT_URL` / `MANIFEST_STORE_BUCKET` / `MANIFEST_STORE_PREFIX` | required for `llm_module`; optional for `object_store` (defaults to the sink's own store/prefix) | Where the per-agency manifest is committed. The `llm_module` sink has nowhere to hold it, so these must be set explicitly for that deployment — startup fails otherwise. `MANIFEST_STORE_BACKEND` is `s3` \| `azure_blob` \| `local`, or blank to inherit |
| `MANIFEST_STORE_ALLOW_LOCAL` | no (default `false`) | Development only. `MANIFEST_STORE_BACKEND=local` puts the manifest on `CONTENT_WORK_DIR` and is refused without this flag — see the warning below |
| `LLM_MODULE_*` (base URL, timeouts, batch caps, auth path) | required for `llm_module` | The llm-module sink's wire contract. `LLM_MODULE_BASE_URL` must be `https://`. Not used by the `object_store` sink |

### The startup validation matrix

Four combinations fail **at startup**, with a message naming the variable to
set. Not at first commit: a run that discovers a missing manifest store after
publishing 2,000 documents has published them with no record of having done
so, which is indistinguishable from never having run.

| Configuration | Result |
|---|---|
| `object_store`, no `MANIFEST_STORE_*` | **OK** — inherits the sink's own store, bucket and prefix. Zero new configuration for this deployment |
| `llm_module`, no manifest store | **refuses to start** — names `MANIFEST_STORE_BACKEND` |
| `llm_module`, no `LLM_MODULE_BASE_URL` or no `LLM_MODULE_VAULT_SECRET_PATH` | **refuses to start** — names whichever is missing |
| `MANIFEST_STORE_BACKEND=local` without `MANIFEST_STORE_ALLOW_LOCAL=true` | **refuses to start** |

A fifth check is implied by the second: an explicitly-set manifest store must
be **complete** (`s3` needs a bucket and an endpoint URL, `azure_blob` needs a
container), or `MANIFEST_STORE_BACKEND=s3` on its own would satisfy the matrix
while naming a store that cannot be addressed.

### Manifest-store inheritance is all-or-nothing

The switch is whether `MANIFEST_STORE_BACKEND` is set.

- **Blank** → the manifest store *is* the object-store sink's own store, and
  the endpoint, bucket **and prefix** are all inherited. The rest of
  `MANIFEST_STORE_*` is ignored in its entirety.
- **Set** → every field comes from `MANIFEST_STORE_*`, and it must be complete.

> **`MANIFEST_STORE_PREFIX=content-manifests` is a recommendation, not an
> always-applied default.** It is the sensible value for an `llm_module`
> deployment, which has to set the backend explicitly anyway. It is *not*
> applied when the store is inherited — if it were, an `object_store`
> deployment that configured nothing would silently relocate its manifest out
> from under `CONTENT_EXTERNAL_PREFIX` and stop being byte-identical to a
> deployment predating the two-sink split.

> **⚠️ `MANIFEST_STORE_BACKEND=local` is for local development only.** The
> danger is not that a filesystem manifest can be lost — it is what being lost
> *looks like*. A re-provisioned volume reads as a **first run**: the entire
> corpus is republished, and every document CKB deleted while the manifest was
> gone is **permanently orphaned**, because it is absent from the database rows
> *and* absent from the manifest, so it can never classify as `deleted`. That
> state must be reachable only by a deliberate act, never by a routine volume
> event — which is why it takes a second flag.

Full validation rules are in `exporter/api/config.py`; the `@model_validator`
methods on `Settings` are the matrix, one per row.

## Observability — how to tell this is working

This is an **hourly job**, and *"the next tick is the retry"* is symmetric: a
wrong base URL, a rotated-away credential or a query past its timeout produce
`failed` every hour, indefinitely, exactly as a transient network blip
produces it once. Two things make the difference visible.

**1. `GET /health` reports the last run.**

```bash
curl -s localhost:8125/health | jq
```

```json
{
  "status": "ok",
  "sink": "object_store",
  "store_backend": "s3",
  "chunk_profile": "azure_native",
  "work_dir": "/var/lib/content-external",
  "last_run": {
    "outcome": "failed",
    "run_id": "r-2026-09-16T17",
    "agency_id": "…",
    "finished_at": "2026-09-16T17:04:11Z",
    "duration_seconds": 251.4,
    "consecutive_failures": 3,
    "consecutive_busy": 0
  }
}
```

`last_run` is `null` until a run completes — an hourly job that has never run
is not the same as one that ran and succeeded.

> **`/health` stays 200 even after a failed run, deliberately.** Both probes
> point at it, so a 503 would restart the pod — which fixes neither a wrong
> base URL nor a rotated credential, and the resulting `CrashLoopBackOff`
> would hide the one log line that says why. **Read `last_run.outcome`, not
> the status code.** A startup refusal (bad config, the A14 lock self-test,
> the A16 memory budget) leaves no listener at all, which the probes *do*
> surface.

The record lives in `{CONTENT_WORK_DIR}/last_run.json` rather than in memory,
because an export runs in a forked process and the CLI is a different process
again — an in-memory value would read `null` forever in production. It is one
record, overwritten; **history outliving the container is deferred work**, not
this.

**2. One greppable line per terminal outcome.**

```bash
docker logs content-external | grep 'content-external run'
```

```
content-external run agency_id=… deletions_recorded=0 docs_content_changed=4 \
  docs_deleted=1 docs_metadata_changed=0 docs_new=12 docs_skipped=3 \
  docs_unchanged=340 duration_seconds=251.400 outcome=success \
  run_id=r-2026-09-16T17 sink=object_store
```

Stable prefix, sorted `key=value` fields, every bucket present even at zero —
so an alert rule is a log query and not a code change, and two runs diff
line-for-line. `failed` lands at `ERROR` and everything else at `INFO`,
including `busy`, which is by design not an error. The startup config echo
follows the same convention under `content-external config`.

Counts never carry document text: ids, hex hash prefixes, counts and
durations only. On the llm-module sink the body of a failed request *is*
Estonian government document text, so this is a data-protection rule and not
a tidiness one.

### The alert thresholds to agree with ops

| Signal | Threshold |
|---|---|
| `last_run.consecutive_failures` | **≥ 2** — one failure is the design working as intended |
| Time since the last `success` or `unchanged` | **> 6 hours** |
| `last_run.consecutive_busy` rising | The export is taking longer than the cron interval. **Lengthen the interval** — nothing in the design depends on its value |

`consecutive_busy` matters because `busy` is correctly not an error: if runs
start exceeding the hourly interval, every tick reports `busy`, detection
latency stops being bounded by the interval, and genuine contention (a drain
colliding with an export) becomes indistinguishable from normal operation.

> A `busy` outcome deliberately **does not** clear the failure streak. A run
> that did not happen is no evidence that whatever was failing has stopped, so
> a drain colliding with an export cannot mask a real failure streak.

## Deployment constraints — never scale this service

The export takes **one whole-run lock** on `CONTENT_WORK_DIR`, and
`drain_deletions` takes the same one. That lock is the only thing preventing a
drain from deleting a key an export has just re-published, and it is an
`flock` — **sound only within one kernel**.

Three facts follow, asserted in three places because one is easy to bypass:

| Fact | Where it is asserted |
|---|---|
| `replicas: 1` | `charts/ckb/templates/content-external-deployment.yaml` |
| `strategy: Recreate` — never `RollingUpdate`, whose whole behaviour is to run two pods at once | same file |
| PVC is `ReadWriteOnce`, on a block or local-backed StorageClass | `values.yaml`, guarded by `ckb.contentExternal.accessMode` in `_helpers.tpl`, which **fails the render** on anything else |

And at startup the service **refuses to start** if it cannot take an exclusive
lock on `CONTENT_WORK_DIR` — so a volume that does not support locking is a
crashed container rather than a concurrency bug found months later.

> **Why `ReadWriteMany` is refused rather than warned about.** On NFS, EFS or
> CephFS, `flock` is advisory and routinely not honoured across nodes: two pods
> would both "acquire" the lock and run concurrent exports **with no error at
> all**. On `ReadWriteOnce` the second pod simply cannot mount — a broken
> deploy rather than corrupted state. RWO is the safe failure; RWX is the
> silent one. The service warns at startup if it detects its work directory on
> a network filesystem, but it cannot reliably detect this, which is why the
> chart guard exists.

In compose, the work directory is a local-driver named volume. Do not run
`docker compose up --scale content-external=N`.

## One agency per deployment, and why it is asserted

CKB enforces one agency per deployment already — agency creation is
existence-checked and returns `409 Conflict`. This service **asserts it
anyway**, because it is the one place where "one agency is N=1, not a special
case" is not true.

The scheduled trigger fans out from `list_agencies` and POSTs one export per
agency, and the export takes a **single whole-run lock**. With two agencies,
the second POST finds the lock held, reports `busy` and **exits 0** — every
hour, forever, with nothing logged as an error, because a concurrent run is by
design not a failure. The second agency would simply never be exported, and
nothing would say so.

The check runs in two places, deliberately asymmetric:

| | More than one agency | Resql unreachable |
|---|---|---|
| **At startup** | **refuses to start**, naming the count | **warns and serves** |
| **At the start of each export** | fails the run, loudly | fails the run |

Startup tolerates an unreachable Resql because a hard assert there would
couple this container's boot to Resql being up — which neither compose (this
service has no `depends_on`) nor Kubernetes guarantees — so a transient Resql
restart would become a `CrashLoopBackOff` here. A service that boots and warns
beats one that will not boot. At the start of a run the answer is
load-bearing and the run needs the agency list regardless, so nothing is
tolerated there.

> **If CKB ever supports more than one agency**, the fix is not to relax this
> assertion but to adopt the drain-and-recurse shape
> `DSL/Ruuter.internal/ckb/GET/pipeline/zip.yml` already uses — claim one
> agency at a time rather than fanning out against a shared lock. Note that
> its termination guarantee is a claimed flag on the `agency` row, and **rules
> 3–5 forbid this service writing any `agency` column**, so it would need a
> claim store of its own.

## Which sink is this deployment running?

Check `CONTENT_SINK` in this deployment's environment, or `GET /health`,
which reports the configured sink alongside the last run's outcome. The two
sinks are not equally capable — an operator reading a reconcile report needs
to know that a result like `unverifiable` is a **property of the sink**,
not a fault in this run.

### Sink parity — what each sink can and can't do

| Capability | Object-store sink | llm-module sink | Pipeline behaviour when absent |
|---|---|---|---|
| Per-document upsert | `metadata.json` + `chunks/NNNNN.json` | one request carrying the record + its chunks | The sink owns batching; the coordinator calls `upsert_document()` either way |
| Deterministic addressing | blob key from `ids.py` | `chunk_id` as the point id — a contract requirement | If the far side re-keys on a content hash instead, idempotency is void |
| `list` | yes | no | Reconcile degrades to manifest-trusted reporting; the orphan sweep uses `synced_at` instead of a listing |
| Batch delete | yes, or per-key fallback | per-document / per-agency delete | `DeleteOutcome` is identical either way — a missing batch API stays a performance note |
| Metadata-only merge | 1 small blob write, 0 chunk writes | merge on N chunk payloads, `content` not resent | If unsupported, `metadata_changed` degrades to a full re-push and a re-embed. Always reported, never hidden |
| Atomic per document | no — publish write order provides it | yes, when the document fits one request | No chunk is ever retrievable before its document's metadata, regardless of sink |
| Holds the manifest | yes | no | Manifest store is configured separately for `llm_module` |
| Read-back for reconcile | via `list` | only if the llm-module exposes one | Reconcile reports **"unverifiable"** rather than "clean" |

So: on the `llm_module` sink, `list`-dependent operations (reconcile,
orphan sweep) are expected to report degraded results. That is the sink's
contract, not a bug in this service.
