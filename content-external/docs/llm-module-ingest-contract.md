# llm-module ingest contract — `ingest_schema_version` 1

> **Status: PROPOSED.** Nothing here has been agreed with the llm-module owner
> yet (Open items 1, 5 and 7 in the task breakdown). This document is the
> proposal to take to that conversation. Until it is signed off, the exporter's
> `LlmModuleSink` is built and tested against it but **cannot be selected** —
> `CONTENT_SINK=llm_module` still refuses at the factory.
>
> The code side of this contract is
> [`exporter/sinks/llm_module_contract.py`](../exporter/sinks/llm_module_contract.py).
> Every JSON example below is validated against those models by
> `tests/test_llm_module_sink.py`, so this document and the code cannot drift
> apart silently.

## What this is for

CKB's content-external pipeline becomes the **only chunker** in the retrieval
path. It reads cleaned documents from CKB, normalises and chunks them, decides
what changed since the last run, and pushes each changed document — record plus
chunks — to the llm-module, which embeds the chunks and upserts them into
Qdrant. The llm-module no longer downloads ZIPs, diffs, or chunks.

The exporter keeps its own manifest of what it has published, and commits it
only after the llm-module has acknowledged the push. A failed push is retried
on the next hourly run. Nothing on the llm-module side needs to remember sync
state.

## 1. Endpoint

```
POST {LLM_MODULE_BASE_URL}/ingest/v1/documents
```

| Header | Value |
|---|---|
| `Authorization` | `Bearer <token>` — the exporter reads it from Vault on every run |
| `Idempotency-Key` | see [§6](#6-idempotency) |
| `Content-Type` | `application/json; charset=utf-8` |

HTTPS only, with certificate verification. The exporter never follows a
redirect.

## 2. Request

One request carries **one document**: its record and some or all of its chunks.

<!-- example: request -->
```json
{
  "ingest_schema_version": 1,
  "agency_id": "agency-1",
  "document_id": "6f1c2c1e-1f7a-4a36-9a51-2d3b8c4e7f10",
  "source_base_id": "0b8a7d0e-5c1d-4f0e-8b2a-9a1c3e5d7f21",
  "content_origin": "cleaned",
  "source_url": "https://www.eesti.ee/et/haridus/oppetoetus",
  "raw_sha256": "9f2b5c6d8e1a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4",
  "content_sha256": "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809",
  "metadata_sha256": "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f",
  "chunk_count": 2,
  "synced_at": "2026-10-26T10:17:00Z",
  "source": {
    "source_url": "https://www.eesti.ee/et/haridus/oppetoetus",
    "page_title": "Õppetoetus"
  },
  "part": {"index": 0, "count": 1, "final": true},
  "chunks": [
    {
      "chunk_id": "c3f9a0d1b2c3d4e5f60718293a4b5c6d7",
      "ordinal": 0,
      "text": "Õppetoetust saab taotleda 3. detsembrini.",
      "char_start": 0,
      "char_end": 41
    },
    {
      "chunk_id": "c0a1b2c3d4e5f60718293a4b5c6d7e8f9",
      "ordinal": 1,
      "text": "Taotlus esitatakse eesti.ee portaalis.",
      "char_start": 42,
      "char_end": 80
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `ingest_schema_version` | This contract's version. The llm-module rejects a version it does not implement |
| `agency_id` | The CKB agency. **Must be stored on every point as a filterable field** — see [§4](#4-point-identity-and-payload) |
| `document_id` | CKB's `source_file.base_id`. Stable for the life of the document |
| `source_base_id` | The CKB source the document belongs to |
| `content_origin` | `edited` (a human-edited version) or `cleaned` |
| `source_url` | The citation a retrieval answer should link to |
| `raw_sha256`, `content_sha256`, `metadata_sha256` | Hashes the exporter's diff uses. Informational for the llm-module — **never use them as point ids** |
| `chunk_count` | How many chunks the document has in total, across all parts |
| `synced_at` | When this exporter run started. Stored on every point; it is what a later stale-content sweep will filter on |
| `source` | The document's metadata sidecar from CKB, verbatim. Full UTF-8 |
| `part` | Which part of a split document this is ([§5](#5-large-documents-split-into-parts)) |
| `chunks` | The chunks in this part, in ordinal order. **`null` means a metadata-only merge** ([§8](#8-metadata-only-merge)) |

Each chunk: `chunk_id` (the point id), `ordinal` (0-based position in the
document), `text` (already normalised — NFC, whitespace canonicalised), and
`char_start`/`char_end` (where the text sits in the normalised document;
neighbouring chunks may overlap).

Text is UTF-8 and is **not** escaped to ASCII: `õ ä ö ü š ž` arrive as
themselves.

## 3. Response

The llm-module answers with **one item for every ref it was sent**: the ref
`"record"` for the document record, and each `chunk_id`.

<!-- example: response -->
```json
{
  "ingest_schema_version": 1,
  "items": [
    {"ref": "record", "status": 200},
    {"ref": "c3f9a0d1b2c3d4e5f60718293a4b5c6d7", "status": 200},
    {"ref": "c0a1b2c3d4e5f60718293a4b5c6d7e8f9", "status": 503, "code": "embedder-busy"}
  ]
}
```

| HTTP status | Meaning |
|---|---|
| `200` | Every item was applied. Items are still required |
| `207` | Some items failed. Read each item's `status` |
| `400`, `401`, `403`, `413`, `422` | The whole request was refused. Nothing was applied |
| `408`, `429`, `5xx` | Try again later. `Retry-After` is honoured. Nothing was applied |

A whole-request status describes the request: `422` there means the request
itself is invalid, and the exporter does not resend it this run.

Item `status` uses HTTP codes, and the same number can mean something
different here:

| Item status | The exporter treats it as |
|---|---|
| `2xx` | applied |
| `409`, `422`, `503` | **retryable** — the exporter resends only the failed chunks, up to two more rounds |
| anything else | **permanent** for that document — reported as failed, retried on the next hourly run |

Note what follows from this table, and see [§10](#10-open-questions-for-the-llm-module-owner)
question 9:

- `422` on an item is retryable. If a chunk is *always* invalid, the document is
  resent, and its chunks re-embedded, every hour.
- `429`, `500`, `502` and `504` on an item are **permanent**. Use `503` for
  "busy, try again".

`code` is a short machine-readable reason: ASCII letters, digits and `-_.`, at
most 40 characters. **It must never contain document text.** The exporter puts
it in its run report; a `code` of any other shape is replaced with `-`.

A ref that is missing from `items` is treated as not applied, and resent with
the next round. A response that answers a ref twice, or answers a ref that was
never sent, cannot be trusted at all: the exporter stops that document for this
run, reports it as failed, and the next hourly run resends it.

The exporter reads at most twice `LLM_MODULE_MAX_REQUEST_BYTES` (and at least
1 MiB) of a response body. A longer body fails the document as permanent.

## 4. Point identity and payload

**This is the most important rule in the contract.**

- The Qdrant **point id is the exporter's `chunk_id`**, unchanged. Do not
  derive a point id from the chunk text or from a content hash.
- Store `agency_id`, `document_id`, `source_base_id`, `content_origin`,
  `source_url` and `synced_at` on every point's payload as **filterable**
  fields.

Why: `chunk_id` is computed from the chunk's *position*
(`agency_id`, `document_id`, `ordinal`), never from its text. So:

- editing one paragraph changes one chunk's text, and the upsert overwrites
  that one point in place. If points were keyed on a content hash instead,
  **every edit would re-key every chunk** of the document — a full delete,
  re-embed and re-insert for a one-word correction;
- deleting a document needs only its `document_id`. With content-hash keys the
  bytes needed to compute the hash are already gone, so the points become
  **impossible to address and are orphaned forever**.

Neither failure shows up in a happy-path integration test, which is why it is
written down here rather than discovered.

If the llm-module enriches chunks before embedding (the `contextual_chunks_*`
collection names suggest a context prefix or summary), the **point id must
still be our `chunk_id` after enrichment**.

## 5. Large documents: split into parts

A request body never exceeds `LLM_MODULE_MAX_REQUEST_BYTES` (8 MiB by
default; agree a ceiling the llm-module accepts). A document that does not fit
is sent as several requests:

- `part.index` counts from 0, `part.count` is the total, and `part.final` is
  `true` only on the last.
- **Every part carries the full document record**, followed by the next run of
  chunks in ordinal order. So a chunk never arrives without its record.
- Parts are sent in order, one at a time. If a part's record is not applied,
  the exporter stops and sends no further parts.
- **Do not skip a part because its `content_sha256` matches what you already
  hold.** Part 0 stores the new hash; parts 1 and later carry the same hash and
  still have to be applied.

If a later part fails, the parts before it **stay applied**. Until the exporter
resends the document (later in the same run for a retryable failure, otherwise
on the next hourly run), it is retrievable with new chunks from the parts that
landed beside old chunks from the parts that did not. Every point still
carries a record. Whole-document atomicity across parts is not promised.

The exporter never truncates a chunk. A document it cannot send fails as
**permanent** and nothing of it is sent, in two cases:

- its record plus a single chunk does not fit in one request, or, for a
  document with no chunks or a metadata-only merge, its record alone does not;
- its text cannot be encoded as UTF-8.

## 6. Idempotency

Every upsert must be **merge-or-create** keyed on `chunk_id` (and on
`document_id` for the record) — never insert-only. The exporter replays
requests in normal operation: transport retries, item retries, a re-run after
a crash, a bootstrap that resumes. A replay must overwrite, not duplicate.

`Idempotency-Key` is

```
{run_id}:{document_id}:p{part_index}[:r{round}]:{digest}
```

- `:r{round}` is present only when the exporter resends failed items.
- `digest` is the first 16 hex characters of the SHA-256 of the request body.

So:

- A transport retry of a request resends the same bytes, and therefore the same
  key.
- Two different bodies never share a key. That holds across the parts of one
  document, and also when an operator reruns a `run_id` after the content
  changed.
- An item resend gets a new key even when its body is byte-identical to the one
  that failed (a retried record resends the same chunks). If you cache replies
  by key, you will never be asked to answer a resend with the failure it is
  resending for.

*(The task breakdown proposes `{run_id}:{document_id}`. The part, round and
digest segments are this document's refinement, for the reasons above.)*

## 7. Atomicity rules

- **A request whose record is not applied applies none of its chunks.** Its
  chunk items report the same status as the record. This is what guarantees
  that no chunk is ever retrievable before its document's record.
- Within a request that applied its record, chunks may succeed or fail
  individually (207).

## 8. Metadata-only merge

When only a document's metadata changed, the exporter can send the record with
`"chunks": null`:

<!-- example: request -->
```json
{
  "ingest_schema_version": 1,
  "agency_id": "agency-1",
  "document_id": "6f1c2c1e-1f7a-4a36-9a51-2d3b8c4e7f10",
  "source_base_id": "0b8a7d0e-5c1d-4f0e-8b2a-9a1c3e5d7f21",
  "content_origin": "edited",
  "source_url": "https://www.eesti.ee/et/haridus/oppetoetus",
  "raw_sha256": "9f2b5c6d8e1a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4",
  "content_sha256": "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809",
  "metadata_sha256": "0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f1",
  "chunk_count": 2,
  "synced_at": "2026-10-26T11:17:00Z",
  "source": {"source_url": "https://www.eesti.ee/et/haridus/oppetoetus", "page_title": "Õppetoetus 2027"},
  "part": {"index": 0, "count": 1, "final": true},
  "chunks": null
}
```

<!-- example: response -->
```json
{"ingest_schema_version": 1, "items": [{"ref": "record", "status": 200}]}
```

The llm-module merges the new record fields onto **every existing point of the
document without re-embedding** and without the exporter resending chunk text.
The merge is **all-or-nothing per document**: one `record` item, applied to
every point or to none.

**Not yet enabled.** The exporter will only send this once the llm-module
confirms it supports it ([§10](#10-open-questions-for-the-llm-module-owner),
question 3). Until then a metadata change is sent as a full re-push of the
document, and the exporter's run report counts each one.

## 9. What stays out of this contract (later stages)

- **Deletes** — whole documents, tail chunks (`ordinal >= chunk_count` after a
  document shrinks), and a whole agency (task L9).
- **The stale-content sweep** — delete points where `agency_id = X and
  synced_at < run_start`, run only after a run that covered the whole corpus
  (task L10).
- **A read-back probe** — the chunk count or chunk ids held for a
  `document_id`, which lets the exporter verify the destination instead of
  reporting it "unverifiable" (task L17).
- **The cutover** — loading into a new collection beside the existing one,
  comparing retrieval quality on your benchmark, swapping, and only then
  removing the llm-module's own chunker (tasks L19–L23).
- **The hourly ZIP job stays.** The Global Classifier still consumes
  `zipped/{agency}.zip`. The llm-module no longer pulling it is not a reason to
  stop producing it (task L24).

## 10. Open questions for the llm-module owner

Each answer maps to one field in `AGREED_TERMS`
(`exporter/sinks/llm_module_contract.py`) or to a setting. A field changes only
when its question is answered.

| # | Question | What it decides | Current assumption |
|---|---|---|---|
| 1 | Which embedding model embeds our chunks, and what is its token limit? | `CHUNK_PROFILE` — a 1200-character Estonian chunk against a tight token budget is silently truncated | None — an `llm_module` deployment must set `CHUNK_PROFILE` explicitly |
| 2 | Will you implement this endpoint, request and response (§1–§3)? | Everything | This proposal |
| 3 | Can you merge metadata without content and without re-embedding (§8)? | `supports_metadata_only_merge` | `False` — full re-push |
| 4 | Will points be keyed on our `chunk_id`, with `agency_id` filterable (§4)? | Whether edits and deletes work at all | Required |
| 5 | What concurrency and request rate will you accept? | `max_concurrency` | `2` parallel documents |
| 6 | What request-size ceiling will you accept? | `LLM_MODULE_MAX_REQUEST_BYTES` | 8 MiB |
| 7 | Is there an enrichment step between chunk and embed, and will it stay? | Retrieval quality after the cutover | Unknown |
| 8 | Who owns the collection swap and its rollback at cutover? | Cutover plan | Unknown |
| 9 | Which item statuses can clear on a resend (§3)? Is a `422` item ever transient, or always a bad chunk? | `_RETRYABLE_ITEM_STATUSES` — a deterministic failure classed retryable is re-embedded every hour and never reported as permanent | `409`, `422`, `503` retryable; everything else permanent |
