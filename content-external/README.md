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
| `CONTENT_WORK_DIR` | **yes — no default** | Path to this service's own working volume — holds the run lock, the deletion journal, the last-run record and the dead-letter record (`_dead_letter/`). Must never be under a `scrapped-data` tree (rule 1), asserted at startup against both the configured value and its resolved real path. There is deliberately no fallback: one would let a deployment that forgot this variable write to a path that is not the mounted volume and keep running. Compose and the chart both set `/var/lib/content-external` |
| `VAULT_ADDR` / `VAULT_TOKEN_PATH` / `VAULT_SECRET_PATH` | no | Vault Agent sidecar, credentials fetched per task. `VAULT_SECRET_PATH` has no default — the Stage-H Azure value is `blob/connections/azure_blob/content` |
| `CONTENT_SINK` | no (default `object_store`) | `object_store` \| `llm_module` — resolved once, in one factory, at run start |
| `EXPORT_PAGE_SIZE` | no (default `1000`, minimum `1`) | Documents per Resql keyset page. Enumeration still returns the complete list or fails. The default comes from the 50k comparison of 1,000 / 5,000 / 20,000 in [RUNBOOK.md](RUNBOOK.md#with-pagination--measured-2026-10-21). |
| `CKB_RESQL_TIMEOUT` | no (default `30`, seconds, greater than `0`) | Timeout for each Resql call: the watermark and every document page. A timed-out query keeps running in Postgres, so lower `EXPORT_PAGE_SIZE` rather than raising this — see [RUNBOOK.md](RUNBOOK.md#signs-the-ceiling-is-being-hit). For the same reason a read timeout is never retried, while connection errors, 429 and 5xx are (F8) |
| `CKB_S3_CONNECT_TIMEOUT` / `CKB_S3_READ_TIMEOUT` | no (defaults `5` / `60`, seconds, greater than `0`) | Timeouts for each attempt to read from CKB's own bucket. Throttles, 5xx, connection errors and timeouts are retried by botocore's standard mode, up to 3 attempts in all (F8); a read that still fails fails the run, and the next tick is the retry |
| `CHUNK_PROFILE` | required for `llm_module`; optional for `object_store` (default `azure_native`) | Chunk geometry preset: `azure_native` (target 1200 / overlap 200 / min 200 / max 2000 chars) or `compact` (450 / 80 / 120 / 700). Overlap is capped at min − 1 so no chunk can fall wholly inside the one before it, which makes `azure_native`'s effective overlap 199. Pinned per deployment alongside the sink, because whoever embeds the chunks decides what fits: on `llm_module` a chunk over the embedder's token budget is silently truncated. An unknown name is refused at startup. Changing it re-chunks the whole corpus on the next run via the chunker fingerprint — and on `llm_module` re-embeds it, so do it as an announced, operator-driven run. A new preset is a reviewed row in `exporter/core/constants.py`, never four separate settings |
| `CONTENT_EXTERNAL_STORE_BACKEND` | no (default `s3`) | `s3` \| `azure_blob` — backend beneath the `object_store` sink |
| `EXTERNAL_S3_ENDPOINT_URL` / `EXTERNAL_S3_BUCKET_NAME` | for a run on `object_store` + `s3` | The destination: any S3-compatible endpoint (SeaweedFS, MinIO, AWS), chosen by URL alone. Path-style addressing. Not checked at startup, so `/health` comes up before a destination exists. A run refuses and names every missing variable |
| `EXTERNAL_S3_ACCESS_KEY_ID` / `EXTERNAL_S3_SECRET_ACCESS_KEY` | for a run on `object_store` + `s3` | The destination's **own** credentials. They are passed to boto3 explicitly and never come from its default chain, which would pick up `AWS_*`: those are CKB's read-only keys and are never used to write (rule 6). A blank value is refused rather than defaulted. They need `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` and `s3:ListBucket` on the destination bucket: every delete first HEADs the key to tell `deleted` from `missing`, and HEAD is authorised as a GET. Without `ListBucket`, a missing key answers 403 and is reported as an error, never as absent. The bucket must not be CKB's own (`S3_BUCKET_NAME` on `S3_ENDPOINT_URL`), and no prefix may contain a `scrapped-data` segment (rule 1). In the chart the credentials come from a Secret (`contentExternal.externalStore.secret`) |
| `EXTERNAL_S3_REGION` | no (default `us-east-1`) | Region to sign with. S3-compatible stores ignore the value |
| `EXTERNAL_S3_SUPPORTS_BATCH_DELETE` | no (default `true`) | Set `false` for a store whose `DeleteObjects` is missing or partial. Deletes then go one key at a time, with the same per-key outcome |
| `MANIFEST_STORE_BACKEND` / `MANIFEST_STORE_ENDPOINT_URL` / `MANIFEST_STORE_BUCKET` / `MANIFEST_STORE_PREFIX` | required for `llm_module`; optional for `object_store` (defaults to the sink's own store/prefix) | Where the per-agency manifest is committed. The `llm_module` sink has nowhere to hold it, so these must be set explicitly for that deployment — startup fails otherwise. `MANIFEST_STORE_BACKEND` is `s3` \| `azure_blob` \| `local`, or blank to inherit. An explicit `s3` manifest store authenticates with `EXTERNAL_S3_ACCESS_KEY_ID` / `EXTERNAL_S3_SECRET_ACCESS_KEY` / `EXTERNAL_S3_REGION` (on `llm_module` there is no object-store destination, so those keys are the manifest store's), checked at startup. They are sent to `MANIFEST_STORE_ENDPOINT_URL`, so on an `object_store` deployment with a separate manifest bucket they must be valid on both endpoints. Its bucket must not be CKB's own. `azure_blob` is refused at startup until Stage H. Any `MANIFEST_STORE_*` value set beside an empty backend is ignored, and startup logs a warning naming it |
| `MANIFEST_STORE_ALLOW_LOCAL` | no (default `false`) | Development only. `MANIFEST_STORE_BACKEND=local` puts the manifest on `CONTENT_WORK_DIR` and is refused without this flag — see the warning below |
| `LLM_MODULE_*` (base URL, timeouts, batch caps, auth path) | required for `llm_module` | The llm-module sink's wire contract. `LLM_MODULE_BASE_URL` must be `https://`. `LLM_MODULE_VAULT_SECRET_PATH` names the KV v2 secret holding `{"token": …}` (compose: `content-external/llm_module`), read through `VAULT_TOKEN_PATH` on every operation. Not used by the `object_store` sink |
| `LLM_MODULE_COLLECTION` | required for `llm_module` | L19: the **physical** collection this deployment writes to, in every request path (`/ingest/v1/collections/{collection}/…`). A new one beside the live corpus, never the retrieval alias, and never `contextual_chunks_azure` / `contextual_chunks_aws` (refused at startup). 1–64 of `A-Z a-z 0-9 _ -`. Part of the manifest's sink_id (`llm_module:<collection>`), so changing it against an existing manifest fails the run; a re-bootstrap into another collection also takes a fresh `MANIFEST_STORE_PREFIX`. See [docs/llm-module-cutover.md](docs/llm-module-cutover.md) |
| `LLM_MODULE_PROTECTED_COLLECTIONS` | no (default empty) | Comma-separated names `LLM_MODULE_COLLECTION` may never be, case-insensitive, on top of the built-in `contextual_chunks_*`. Put the **retrieval alias** here, and any other live collection on that llm-module: startup refuses them, and so does the sink factory |
| `LLM_MODULE_ALLOW_PROPOSED_CONTRACT` | no (default `false`) | Development only. `llm_module` is refused at startup while the ingest contract is PROPOSED; this lets it run against the stub regardless |
| `STALE_SWEEP_ENABLED` | no (default `false`) | L10. After a committed run that saw the whole corpus and held nothing back, ask the destination to delete what was synced before the run and is not in the committed manifest's keep list. Never changes a run's outcome; reported as `stale_sweep=` (`disabled`, `refused`, `not_supported`, `failed`, `done`) |
| `EXPORT_CONCURRENCY` | no (default `4`, max `16`) | Documents prepared and published in parallel: across documents, never within one, so a document's record still lands before its chunks. The run uses the lower of this and the sink's declared ceiling (`object_store`: 16; the llm-module's is its owner's to state), and logs when it is capped. Peak memory is `EXPORT_CONCURRENCY × MAX_DOCUMENT_BYTES × 3` plus overhead, asserted at startup against the container limit. A concurrent run commits the same manifest bytes as a sequential one |
| `SINK_FAILURE_ABORT_THRESHOLD` | no (default `25`) | Consecutive **permanent** upsert failures (a refused credential, a missing bucket, a wrong base URL) after which the run aborts with nothing committed and reports `failed` / `SystemicSinkFailure`. A published document resets the count, and retryable failures never count. One permanent failure is a document's problem: it is held and retried. This many in a row is the destination's |
| `DIFF_ON_MISSING_MANIFEST` | no (default `first_run`) | `first_run` treats a missing manifest as a bootstrap. `fail` refuses it. Flip to `fail` once the bootstrap is done. Even under `first_run`, a first run refuses a destination that already holds the agency's content (see *Running an export*) |
| `BOOTSTRAP_CHECKPOINT_EVERY` | no (default `250`) | On a first run, commit the manifest every N published documents, with the watermark withheld, so a killed bootstrap resumes |

### Reading CKB's bucket

Set `S3_ENDPOINT_URL`, `S3_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY` and `AWS_REGION` for CKB's own bucket. Compose takes
these from the root `.env`; the chart uses `contentExternal.sourceBucket`,
with the access key and secret supplied through its `secret` reference.
The client is created on the first document read, so enumeration needs no
bucket credentials. Missing endpoint, bucket or credentials fail that read.

Use a dedicated key with **`s3:GetObject` only**. Enumeration comes from
Resql, so no `s3:ListBucket` permission is needed. Without it, S3 can answer
403 for a missing object; that is indistinguishable from denied access and
therefore fails the run. A definitive object 404 is handled as missing.

These are a different bucket and credentials from `EXTERNAL_S3_*`, which
publishes the output. Reusing file-processing's keys is acceptable for
**local development only**. The reader always uses its own boto3 client
and never calls file-processing (Global Classifier rule 6).

### The startup validation matrix

Every combination below that is not **OK** fails **at startup**, with a message naming the variable to
set. Not at first commit: a run that discovers a missing manifest store after
publishing 2,000 documents has published them with no record of having done
so, which is indistinguishable from never having run.

| Configuration | Result |
|---|---|
| `object_store`, no `MANIFEST_STORE_*` | **OK** — inherits the sink's own store, bucket and prefix. Zero new configuration for this deployment |
| `llm_module`, no manifest store | **refuses to start** — names `MANIFEST_STORE_BACKEND` |
| `llm_module`, no `LLM_MODULE_BASE_URL`, no `LLM_MODULE_COLLECTION` or no `LLM_MODULE_VAULT_SECRET_PATH` | **refuses to start** — names whichever is missing |
| `llm_module` with `LLM_MODULE_COLLECTION` set to `contextual_chunks_*`, or not one plain path segment | **refuses to start** — L19: bootstrap beside, never in place |
| `MANIFEST_STORE_BACKEND=local` without `MANIFEST_STORE_ALLOW_LOCAL=true` | **refuses to start** |
| `MANIFEST_STORE_BACKEND=azure_blob` | **refuses to start** — names Stage H, which builds that backend |
| `llm_module` with no explicit `CHUNK_PROFILE` | **refuses to start** — names `CHUNK_PROFILE`; the default was chosen for a destination that embeds its own chunks |
| `llm_module` while the ingest contract is PROPOSED | **refuses to start** — names Open items 1, 5 and 7. `LLM_MODULE_ALLOW_PROPOSED_CONTRACT=true` overrides it, for development against the stub only |
| `CHUNK_PROFILE` not a known preset (any sink) | **refuses to start** — lists the valid presets |

One more check is implied by the second row: an explicitly-set manifest store must
be **complete** (`s3` needs a bucket, an endpoint URL and the `EXTERNAL_S3_*`
keys, `azure_blob` needs a container), or `MANIFEST_STORE_BACKEND=s3` on its own would satisfy the matrix
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
  "sink_id": "object_store",
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
    "consecutive_busy": 0,
    "error_type": "StoreError",
    "run_observed_full_corpus": false
  }
}
```

`sink_id` is the destination's identity as the manifest records it, and it is
the same value every `content-external run` and `content-external error`
line carries as `sink=`. On `object_store` it equals `sink`. On `llm_module`
it is `llm_module:<LLM_MODULE_COLLECTION>`, which tells you which collection
this deployment writes to at a cutover swap or rollback (L19).

`error_type` is the exception class behind a `failed` run, and only that: this
endpoint is unauthenticated. The sanitised message is in the
`content-external error` log line (below).

`run_observed_full_corpus` is true only for a run that classified every row.
A `--limit` run, an aborted run and any `busy` or `failed` run report false.
It is `null` on a record written before the field existed.

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
because an export runs in a spawned process and the CLI is a different process
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
  docs_unchanged=340 duration_seconds=251.400 error_type=- full_corpus=true \
  outcome=success run_id=r-2026-09-16T17 sink=object_store upsert_deferred=0 \
  upsert_failed_permanent=1 upsert_failed_retryable=0 upsert_published=15
```

The `upsert_*` fields count every upsert by its outcome. A `success` with
non-zero failures is a run whose failed documents keep their previous
manifest entry and are retried next tick. The report names each one in
`publish_failures`, with its status, or with `rechunk_count_mismatch` for a
document held because re-chunking its unchanged content disagreed with the
published count. `full_corpus` is `run_observed_full_corpus`.

Stable prefix, sorted `key=value` fields, every bucket present even at zero —
so an alert rule is a log query and not a code change, and two runs diff
line-for-line. `failed` lands at `ERROR` and everything else at `INFO`,
including `busy`, which is by design not an error. The startup config echo
follows the same convention under `content-external config`.

A run that fails **before** the export can report itself — a configuration
refusal, the agency check, a factory error — adds one more line, under
`content-external error`, with the exception type and its sanitised message.
It is recorded as a `failed` run too, so `/health` and the alert rule see it.
Nothing is written to CKB: cleaning's `send_error` posts to Ruuter's
`reports/logs/add`, which belongs to a scrape's run report, and this service
is read-only against CKB.

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

## Running an export

> Both entry points below read CKB through Stage E's document source: the
> Resql enumeration and read-only S3 reads of CKB's bucket. A run therefore
> needs `CKB_RESQL` reachable and the `AWS_*` / `S3_*` read keys set, as well
> as a configured destination.

**The production surface is the HTTP endpoint**, which CronManager calls
through Ruuter (Stage G):

```bash
curl -s -X POST localhost:8125/export_agency_async \
  -H 'Content-Type: application/json' \
  -d '{"agency_base_id":"…","run_id":"r1"}'
# {"status":"started","agency_base_id":"…","run_id":"r1"}
```

It starts the export in its own process and returns at once. `run_id` is
generated when omitted. The outcome is not in the response: read it from
`/health` or the `content-external run` line. A second export while one is
running reports `busy`. If the export this service started last is still
alive, the response itself says `{"status":"busy",…}` and no process starts,
so a misfiring cron cannot pile up children; otherwise the run lock in the
child decides, and the `busy` shows up in `/health`. Either way it is
recorded and counted in `consecutive_busy`.
`POST /drain_deletions` answers 501 until Stage J builds the drain.

**The CLI is an operations tool**: the first end-to-end run, a bootstrap
checked on a slice, a re-export by hand.

```bash
python -m exporter.run_sync --agency-id … [--run-id …] [--force] [--limit N] \
  [--accept-existing-content] [--accept-rechunk] [--output-json PATH|-]
```

The report is JSON on stdout (or the file); logs go to stderr. Exit codes are
`0` for `success`/`unchanged`/`busy`, `1` for `failed`, and `2` for a usage or
configuration error. CLI runs take the same lock and show up in `/health`.

- **`--limit N`** reads at most N documents. It never truncates the
  enumeration, because a row left out would read as deleted. Rows past the cap
  are held (`skipped`, reason `limit_reached`) with their published state
  untouched. **The corpus watermark is withheld**, so the next full run still
  walks every row, with Gate 2 sparing the unchanged ones at zero reads. This
  holds on an incremental run too, not only a bootstrap. Without it,
  `--limit 25` would record the full watermark and the next tick would
  report `unchanged` while every other changed document stayed unpublished.
  The report says `run_observed_full_corpus: false`. The flag is false only
  when the cap actually held a row back: a `--limit` larger than the reads
  the run needed skipped nothing, so it counts as a full run.
- **Sweeps need a clean full run.** Every sweep (Stage J's orphan sweep and
  `run_purge`, the llm-module's `synced_at` sweep) must check the run through
  `exporter/services/corpus_guard.py`. It refuses unless the run succeeded,
  `run_observed_full_corpus` is true, and `documents_held_back` is 0. A
  held-back document is one that was not published, had a tail delete left
  unconfirmed, or was skipped by a size guard. It keeps its old state and old
  `synced_at`, so a sweep would delete it as stale.
- **Gate 1 makes a quiet tick cheap.** When the agency's watermark, live
  document count and chunker fingerprint all match the committed manifest,
  the run ends `unchanged` after one watermark query and one manifest read:
  no enumeration, no S3 reads, no commit. It still walks the rows when a
  deletion journal is unclosed or awaiting its mirror, since only a walking
  run settles those. An `unchanged` run reports
  `run_observed_full_corpus: false` and never drives a sweep.
- **`--force`** bypasses Gate 1 and walks every row. A chunker-geometry
  change never needs it.
- **Stuck documents are dead-lettered.** A document skipped for a fault of its
  own — over `MAX_DOCUMENT_BYTES` or `MAX_CHUNKS_PER_DOCUMENT`, undecodable, a
  sidecar that is missing, too large or does not parse, an uncitable
  `source_url` — never fails the run. It is listed in
  `$CONTENT_WORK_DIR/_dead_letter/<agency_id>.json` with its reason, when it
  was first and last seen, and how many runs in a row found it. The report's
  `dead_lettered_count` and the run line's `dead_lettered=` give the size. It
  leaves the record when a run publishes, deletes or finds it unchanged.
  Rows that are only not ready yet, uploaded files and `--limit` skips are
  not faults and are never listed. See
  [RUNBOOK.md](RUNBOOK.md#dead-lettered-documents).
- **A first run checkpoints.** With no previous manifest, the manifest is
  committed every `BOOTSTRAP_CHECKPOINT_EVERY` published documents (default
  250), with the watermark withheld. A killed bootstrap then resumes where it
  stopped instead of starting over. An incremental run never checkpoints.
- **A first run refuses a populated destination.** With no manifest, the run
  first checks whether the destination already holds this agency's content.
  On a listable sink it lists; otherwise it reads back the first 25
  enumerated documents. If any content is there, the run fails with
  `NonEmptyDestination` and publishes nothing. In a mature deployment a
  missing manifest means something deleted it, and a cold start would
  republish the corpus **and permanently orphan every document CKB deleted
  while it was gone**. So `DIFF_ON_MISSING_MANIFEST=first_run` cannot do that
  damage even if the flip to `fail` was forgotten. The report's
  `first_run_probe` says how the check came out:
  - `empty`: the check found nothing.
  - `populated`: the check found content, and the run was refused.
  - `resumed`: a bootstrap of this service's own died before its first
    checkpoint.
    - A marker at `{CONTENT_WORK_DIR}/_bootstrap/{agency}.inprogress` exists
      from the first upsert to the first commit, and the log line names the
      run that wrote it.
    - It may exist only while no manifest does. Every run that finds a
      manifest removes it first, and that run **fails** if it cannot.
    - A lost volume loses the marker, which refuses: the safe direction.
  - `overridden`: see the next bullet.
  - `unverifiable`: a sink that can neither list nor read back. The config
    flip is then the only defence.
- **`--accept-existing-content`** lets a first run publish into a populated
  destination anyway: it overwrites what it publishes and keeps the rest. Use
  it only when that content is known to be safe to overwrite.
  - It is CLI only. The HTTP endpoint, and so the cron, can never set it.
  - Such a run never writes a marker and removes any it finds, because the
    destination's content is no longer all this service's own. If it fails,
    the retry needs the flag again.
- **A re-chunk on the llm-module sink needs `--accept-rechunk` (L18).** A
  `CHUNK_PROFILE`, `CHUNKER_VERSION` or `NORMALISER_VERSION` change re-chunks
  every document, and on a sink that re-embeds what it receives that is a
  full corpus re-embed on someone else's service. While any committed
  document is on another geometry, a run without the flag fails
  (`RechunkNotAcknowledged`, `rechunk: refused`) having sent nothing — every
  tick, until an operator runs it by hand. A `--limit` slice does not unlock
  the cron: the rest of the corpus is still pending. CLI only. Procedure:
  [RUNBOOK.md](RUNBOOK.md#changing-chunk-geometry-on-the-llm-module-sink).

**Interrupting a run.** On SIGTERM or Ctrl-C the export stops submitting
documents and waits for the ones already in flight. One stuck S3 call can
take up to about 3 × (5 s connect + 60 s read). A container stop grace period
shorter than that ends in SIGKILL. That is safe: nothing commits before the
end, the lock dies with the process, and every upsert is replayed next tick.

Retries: Resql calls back off with full jitter on connection errors and
408/429/5xx, honouring `Retry-After` up to 120 s. Past that, the call fails
and the next tick is the retry. S3 calls use botocore's own standard-mode
retries, deliberately not doubled.

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
| Batch delete | yes, or per-key fallback | batches of up to 500 document ids, one item each; tails by point id | `DeleteOutcome` is identical either way — a missing batch API stays a performance note |
| Stale sweep (`STALE_SWEEP_ENABLED`) | not supported — the ordinal tail rule (Stage J) | `synced_at < run start` **and not in the manifest's keep list**, once the far side agrees it (Open item 7) | Reported as `stale_sweep=not_supported`; never improvised |
| Agency delete (purge) | prefix delete | only once points carry a filterable `agency_id` (Open item 7) | `NotSupported` |
| Re-chunk (a fingerprint change) | a longer run | a full corpus **re-embed** | On the llm-module sink the run is refused until an operator passes `--accept-rechunk` (RUNBOOK) |
| Metadata-only merge | 1 small blob write, 0 chunk writes | merge on N chunk payloads, `content` not resent | If unsupported, `metadata_changed` degrades to a full re-push and a re-embed. Always reported, never hidden |
| Atomic per document | no — publish write order provides it | when the document fits one request; a larger one is split into ordered parts, **each carrying the full record** | No chunk is ever retrievable before its document's metadata, regardless of sink |
| Holds the manifest | yes | no | Manifest store is configured separately for `llm_module` |
| Read-back for reconcile | via `list` | only if the llm-module exposes one | Reconcile reports **"unverifiable"** rather than "clean" |

So: on the `llm_module` sink, `list`-dependent operations (reconcile,
orphan sweep) are expected to report degraded results. That is the sink's
contract, not a bug in this service.

### Destinations in code — two layers

- **`exporter/sinks/base.py` — `ContentSink`.** The coordinator's only
  destination interface: `upsert_document`, `delete_documents`,
  `delete_chunks`, `delete_agency`, `sweep_stale`, `read_document`,
  `holds_agency_content` (the first-run probe, needs `can_list`), plus
  declared `SinkCapabilities`. Those include `max_concurrency`, the sink's own
  ceiling on parallel documents. A sink is bound to one agency when it is
  built. Any capability a sink has not declared raises `NotSupported`; it
  never silently does nothing.
- **`exporter/stores/base.py` — `ExternalStoreClient`.** A key-value view of
  an object store (put / get / exists / list / delete). Only the object-store
  sink and the manifest store use it, never the coordinator.

Two rules an operator reading a report should know:

- **A delete reports per item**: `deleted`, `missing`, `deferred` or
  `failed`. `missing` means "already gone" and counts as success, so a
  replayed delete never fails. `deferred` and `failed` are what the
  deferred-deletion journal keeps.
- **"Absent" and "error" are never confused.** A store read returns nothing
  only when the object definitively does not exist. Every other failure,
  including a 403 that looks like a missing object, is an error, and the run
  fails having committed nothing.

`exporter/sinks/factory.py` is the only place `CONTENT_SINK` is resolved, and
`exporter/stores/factory.py` the only place a store backend is. Each run
builds them; startup does not. `object_store` builds `ObjectStoreSink` over
the `s3` backend (`exporter/stores/s3_store.py`). `azure_blob` (Stage H) and
a `local` manifest store (D19) each refuse, naming the task that builds
them. `llm_module` builds `LlmModuleSink` (`exporter/sinks/llm_module_sink.py`
— push, split parts, per-item partial failure, idempotency keys, deletes,
the keep-list sweep, read-back), carrying the run id its Idempotency-Keys
name and a credential read from Vault on every operation (L11) — but only
once the contract is agreed, or with the development flag.

The llm-module wire contract is
[`docs/llm-module-ingest-contract.md`](docs/llm-module-ingest-contract.md),
with its models in `exporter/sinks/llm_module_contract.py`. It is a
**proposal** until the llm-module owner agrees it. What the far side has
promised lives in one constant, `AGREED_TERMS`, which starts at the safe
value for each item: status `proposed` (so the sink is refused at startup),
no metadata-only merge (a metadata change is re-pushed in full and counted
as `metadata_full_repush_count`), no agency delete, no stale sweep, no
read-back, and at most 2 documents in parallel. Each changes only when the
contract document records the owner's answer.

**Credential (L11).** `LLM_MODULE_VAULT_SECRET_PATH` names a KV v2 secret
holding `{"token": "..."}`, read with the token Vault Agent keeps at
`VAULT_TOKEN_PATH`. In compose that agent is `vault-agent-content`, behind
the `llm-module` profile (`docker compose --profile llm-module up -d`), and
`vault/init.sh` creates its role and a `REPLACE_ME` placeholder at
`content-external/llm_module` — deliberately outside `llm/connections/*`,
which other services' policies can read. A placeholder is refused, never
sent. The chart runs no Vault Agent for any service; a cluster deployment
supplies the token file itself (see `charts/ckb/values.yaml`).

**Against the stub, on a laptop (L15).** `tests/llm_module_stub.py` is the
contract as a FastAPI app. The sink refuses `http://`, so serve it over TLS
and trust its certificate:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj /CN=localhost \
    -keyout stub.key -out stub.crt
uv run uvicorn --app-dir tests llm_module_stub:app --port 8443 \
    --ssl-keyfile stub.key --ssl-certfile stub.crt
# then, for the exporter:
CONTENT_SINK=llm_module LLM_MODULE_ALLOW_PROPOSED_CONTRACT=true \
LLM_MODULE_BASE_URL=https://localhost:8443 LLM_MODULE_COLLECTION=ckb_chunks_v1 \
REQUESTS_CA_BUNDLE=$PWD/stub.crt ...
```

**The cutover (L19–L25)** — bootstrapping beside the llm-module's existing
corpus, the retrieval comparison, the swap and its rollback, and dropping the
old corpus — is planned in
[`docs/llm-module-cutover.md`](docs/llm-module-cutover.md). The hourly ZIP
job is not part of it and keeps running (L24).

The stub serves a well-behaved llm-module; its failure scripting is for the
tests, which run it in-process with no network.

What the object-store sink does and doesn't touch:

- **It writes `metadata.json` before any chunk.** It deletes a document's
  chunks before its `metadata.json`, and keeps the `metadata.json` whenever a
  chunk delete fails.
- **It parses listings and never counts them.** A pseudo-directory entry
  from a gateway is ignored.
- **`delete_agency` removes `documents/` only.** When the manifest store is
  inherited, `manifest.json` and `_control/` share this prefix. They belong
  to the manifest store and survive a purge.
- **`sweep_stale` is not supported.** On a listable store, orphans are swept
  by the ordinal tail rule; the `synced_at` sweep is for the llm-module sink.

What the llm-module sink does on delete and sweep:

- **Document deletes go in batches, one item per document.** The far side
  deletes a document's points before its record. `404` is "already gone" and
  counts as success; a retryable item is resent alone, and whatever is still
  failing is journalled for a later run.
- **A tail goes as point ids**, rendered from the ordinals the diff hands
  over. The record stays.
- **The sweep carries a keep list.** `STALE_SWEEP_ENABLED=true` (off by
  default) runs it after a committed run that saw the whole corpus and held
  nothing back. It asks the far side to delete what was synced before this
  run started **and** is not among the documents the committed manifest
  lists. An unchanged document is never re-sent, so it keeps an old
  `synced_at` while being live — the keep list is what spares it. A keep
  list is never split and never empty. The outcome is `stale_sweep=` in the
  run line and report; it never changes the run's outcome.

### Does this store work? — the conformance suites

`tests/test_store_conformance.py` asserts the store contract, and
`tests/test_sink_conformance.py` the sink contract. Both always run against
in-memory fakes. Point them at a real S3-compatible endpoint, with an
existing bucket, to accept a new store:

```bash
docker run -d -p 9000:9000 minio/minio server /data          # or:
docker run -d -p 8333:8333 chrislusf/seaweedfs server -s3
export CONFORMANCE_S3_ENDPOINT_URL=http://localhost:9000
export CONFORMANCE_S3_BUCKET=conformance
export CONFORMANCE_S3_ACCESS_KEY_ID=...
export CONFORMANCE_S3_SECRET_ACCESS_KEY=...
uv run pytest tests/test_store_conformance.py tests/test_sink_conformance.py -v
```

Without those variables the live variants skip. `s3-batch` and `s3-per-key`
run the same endpoint with and without `DeleteObjects`. A store is accepted
when every variant passes.
