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
