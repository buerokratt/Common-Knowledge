# llm-module ingest contract — `ingest_schema_version` 1

> **Status: PROPOSED.** Nothing here has been agreed with the llm-module owner
> yet (Open items 1, 5 and 7 in the task breakdown, which are questions 1, 2
> and 4 in [§13](#13-open-questions-for-the-llm-module-owner)). This document is the
> proposal to take to that conversation. Until it is signed off, the exporter's
> `LlmModuleSink` is built and tested against it (against a stub of this
> contract, `tests/llm_module_stub.py`) but **cannot be selected**:
> `CONTENT_SINK=llm_module` is refused at startup while `AGREED_TERMS.status`
> is `proposed`. `LLM_MODULE_ALLOW_PROPOSED_CONTRACT=true` overrides that, for
> development against the stub only.
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
POST {LLM_MODULE_BASE_URL}/ingest/v1/collections/{collection}/documents
```

Every path in this contract starts `/ingest/v1/collections/{collection}/`.
`{collection}` is the exporter's `LLM_MODULE_COLLECTION`:

- **It is the physical collection, never a retrieval alias.** The cutover
  swap and its rollback move the alias that retrieval reads through
  ([`llm-module-cutover.md`](llm-module-cutover.md)). If the exporter wrote
  through that alias, a rollback would point its writes at the old corpus.
- **It is a new collection, beside the live one.** `contextual_chunks_azure`
  and `contextual_chunks_aws` hold points chunked by the llm-module's own
  chunker and keyed on `document_hash`. None of them can be mapped to one of
  ours, so the exporter refuses those names at startup. It also refuses
  anything the deployment lists in `LLM_MODULE_PROTECTED_COLLECTIONS`, which
  is where the alias goes.
- **If the collection does not exist**, answer the whole request with `404`,
  never an item-level one. The exporter then fails the push and the delete
  outright instead of reading "already gone".
- 1–64 characters of `A-Z a-z 0-9 _ -`.
- Either the llm-module creates it on first write, or its owner creates it
  before the bootstrap ([§13](#13-open-questions-for-the-llm-module-owner)
  question 15).

The exporter records the collection in its manifest. Pointing it at another
collection with the same manifest fails the run instead of claiming
documents are already published there.

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

Note what follows from this table, and see [§13](#13-open-questions-for-the-llm-module-owner)
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
still be our `chunk_id` after enrichment**. Enrichment changes the text, so
a point id derived from enriched text would bring back exactly the
re-keying this section forbids. Whether enrichment stays is settled before
the cutover (task L22, [§13](#13-open-questions-for-the-llm-module-owner)
question 7).

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
{collection}:{run_id}:{document_id}:p{part_index}[:r{round}]:{digest}
```

- `{collection}` is the path's collection ([§1](#1-endpoint)). The body does
  not name it, so without it the same run, document and part sent to two
  collections, such as a re-bootstrap beside the first, would share a key,
  and a key cache shared across collections would answer the second as a
  replay and never write it.
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

*(The task breakdown proposes `{run_id}:{document_id}`. The collection,
part, round and digest segments are this document's refinement, for the
reasons above.)*

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

**The answer to a merge is exactly one item, `record`** (task L25). Its
status covers every point of the document:

- `2xx` means every point now carries the new record. The document enters
  the exporter's manifest.
- Any other status means **no** point changed. The document stays out of the
  manifest and the whole merge is sent again: in the same run if the status
  is retryable, otherwise on the next hourly run.
- **A merge answered point by point is refused.** If the response carries
  items for chunk ids, the exporter cannot tell an all-or-nothing merge from
  one that landed on 8 of 12 points. It treats the answer as untrustworthy
  ([§3](#3-response)): the document is not published and is sent again on
  the next run.

The reason for this rule: if a half-applied merge entered the manifest, it
would stay half-applied for good. The next run finds the hashes matching and
sends nothing, so the document's passages disagree about its own title,
indefinitely.

**Not yet enabled.** The exporter will only send this once the llm-module
confirms it supports it ([§13](#13-open-questions-for-the-llm-module-owner),
question 3). Until then a metadata change is sent as a full re-push of the
document, and the exporter's run report counts each one.

## 9. Deletes

Every delete uses the same headers as an upsert (§1), the same
`Idempotency-Key` scheme (§6, with `del` in place of the part), and the same
item-by-item response (§3). A delete item's status means:

| Item status | The exporter treats it as |
|---|---|
| `2xx` | deleted |
| `404`, `410` | **already gone — a success**. A replayed delete must not fail because the first one worked |
| `409`, `422`, `503` | retryable: resent alone, up to two more rounds |
| anything else | failed. The exporter journals it and retries on a later run |

"Already gone" is an **item** status only. Answer a delete request with an
item-by-item `200`/`207` even when nothing it names exists — including a tail
delete (§9.2) for a document you hold nothing of: every item `404`. A `404`
for the **whole request** reads as a wrong base URL or route, so the exporter
treats it as failed rather than as "deleted". Reading it as success would let a
misconfigured URL report every deletion done while doing none.

### 9.1 Whole documents

```
POST {LLM_MODULE_BASE_URL}/ingest/v1/collections/{collection}/documents/delete
```

<!-- example: document-delete-request -->
```json
{
  "ingest_schema_version": 1,
  "agency_id": "agency-1",
  "document_ids": [
    "6f1c2c1e-1f7a-4a36-9a51-2d3b8c4e7f10",
    "0d2e4f60-8a1b-4c3d-9e5f-7a8b9c0d1e2f"
  ]
}
```

<!-- example: response -->
```json
{
  "ingest_schema_version": 1,
  "items": [
    {"ref": "6f1c2c1e-1f7a-4a36-9a51-2d3b8c4e7f10", "status": 200},
    {"ref": "0d2e4f60-8a1b-4c3d-9e5f-7a8b9c0d1e2f", "status": 404}
  ]
}
```

One item per `document_id`, keyed on it. For each document, **delete every
point first and the record last**, so no point is ever left without its
record. A document whose points could not all be deleted is a failed item,
and **its record stays**.
At most 500 ids go in one request, and fewer if the body would pass
`LLM_MODULE_MAX_REQUEST_BYTES`.

### 9.2 Tail chunks

When a document shrinks, its new chunks are upserted first, and then the
chunks past the new end are deleted. The record stays.

```
POST {LLM_MODULE_BASE_URL}/ingest/v1/collections/{collection}/documents/{document_id}/chunks/delete
```

<!-- example: chunk-delete-request -->
```json
{
  "ingest_schema_version": 1,
  "agency_id": "agency-1",
  "document_id": "6f1c2c1e-1f7a-4a36-9a51-2d3b8c4e7f10",
  "chunks": [
    {"chunk_id": "c7d8e9f0a1b2c3d4e5f60718293a4b5c6", "ordinal": 2},
    {"chunk_id": "c1a2b3c4d5e6f708192a3b4c5d6e7f809", "ordinal": 3}
  ]
}
```

One item per `chunk_id` — the point id (§4). The `ordinal` is there so a
log on your side can say which passage went.

### 9.3 A whole agency

```
POST {LLM_MODULE_BASE_URL}/ingest/v1/collections/{collection}/agencies/{agency_id}/delete
```

<!-- example: agency-delete-request -->
```json
{"ingest_schema_version": 1, "agency_id": "agency-1"}
```

<!-- example: deleted-documents-response -->
```json
{
  "ingest_schema_version": 1,
  "deleted_document_ids": ["6f1c2c1e-1f7a-4a36-9a51-2d3b8c4e7f10"]
}
```

Every point and record whose `agency_id` matches. An empty list means there
was nothing to delete. **This needs `agency_id` stored as a filterable field
on every point (§4, §13 question 4).** Until that is confirmed the exporter never
sends it.

## 10. The stale sweep

After a run that saw the whole corpus and held nothing back, the exporter
can ask for content it no longer accounts for to be removed: documents it
deleted in a run that then failed to say so, or that an earlier deployment
left behind.

```
POST {LLM_MODULE_BASE_URL}/ingest/v1/collections/{collection}/agencies/{agency_id}/sweep
```

<!-- example: sweep-request -->
```json
{
  "ingest_schema_version": 1,
  "agency_id": "agency-1",
  "before": "2026-10-26T10:17:00Z",
  "keep_document_ids": [
    "6f1c2c1e-1f7a-4a36-9a51-2d3b8c4e7f10",
    "0b8a7d0e-5c1d-4f0e-8b2a-9a1c3e5d7f21"
  ]
}
```

Delete every document of `agency_id` — its points and its record — whose
`synced_at` is **earlier than** `before` **and** whose `document_id` is **not**
in `keep_document_ids`. Answer with `deleted-documents-response` (§9.3).

- **The keep list is the safety, not an optimisation.** The exporter does not
  resend a document that has not changed, so a live document can carry a
  `synced_at` from weeks ago. `synced_at < before` alone would delete it.
  `keep_document_ids` is every document the exporter's committed manifest
  says it has published.
- Compare `synced_at` and `before` as **timestamps** (RFC 3339, UTC), not as
  strings.
- A keep list is never split across requests: each piece would delete what
  the others keep. If one does not fit `LLM_MODULE_MAX_REQUEST_BYTES`, the
  exporter does not sweep. It never sends an empty keep list.
- **Enforce the keep list inside the delete itself** — as part of the
  filter, not as a check afterwards. Deleting a document that is in the keep
  list is a contract violation. The exporter checks the answer and fails the
  sweep loudly if it happens, but it can only notice: by then the content is
  gone, and its manifest claims documents the destination no longer holds.

Like the agency delete, **this needs a filterable `agency_id`**, and the
exporter does not send it until that is agreed.

## 11. Read-back

```
GET {LLM_MODULE_BASE_URL}/ingest/v1/collections/{collection}/documents/{document_id}
```

<!-- example: probe-response -->
```json
{
  "ingest_schema_version": 1,
  "document_id": "6f1c2c1e-1f7a-4a36-9a51-2d3b8c4e7f10",
  "record_present": true,
  "chunk_count": 2,
  "chunk_ordinals": [0, 1]
}
```

`404` when nothing is held for the document. `chunk_ordinals` is optional;
`chunk_count` alone is enough to be useful. This is what lets the exporter
check, before a first run, that the destination is really empty, and lets a
reconcile report "clean" rather than "unverifiable". It is optional: without
it the exporter degrades to trusting its own manifest, and says so.

## 12. The cutover, and what it does not touch

The full plan, commands and sign-off record are in
[`llm-module-cutover.md`](llm-module-cutover.md). Two of its rules belong in
this contract because they bind both sides.

**The order is fixed (task L21).**

1. This contract is agreed and the exporter ships.
2. The exporter bootstraps the whole corpus into a **new** collection
   ([§1](#1-endpoint)). The live collection keeps serving and stays
   unchanged, and the llm-module's existing pipeline keeps it current.
3. Your retrieval benchmark is run against your chunks and ours, on the same
   corpus, and the result is recorded with a decision (task L20).
4. The retrieval alias is swapped to the new collection.
5. After an agreed settling period, **and only then**, the llm-module
   removes its chunker, `document_loader.py`, `dataset_download.py`,
   `diff_identifier/`, `processed-metadata.json` and the DVC remnants.
6. The old collection is dropped by a named owner (task L23). Neither the
   exporter's sweep nor its purge can reach it.

Removing the chunker before step 3 is the intuitive order and the wrong one.
It deletes the fallback and the only possible comparison in the same commit.

**The hourly ZIP job stays (task L24).** The Global Classifier still
consumes `zipped/{agency}.zip` through CKB's `client/data/*` endpoints, and
detects change through `agency.data_hash`. The llm-module no longer pulling
the ZIP is not a reason to stop producing it. Without the job, `data_hash`
freezes and the Classifier goes stale silently. Nothing in this contract or
the cutover changes `trigger_zipping`, `pipeline/zip.yml`, the
`zip_dirty` / `is_zipping` pair, `data_hash`, `zipped_data_url` or
`client/data/*`.

## 13. Open questions for the llm-module owner

Each answer maps to one field in `AGREED_TERMS`
(`exporter/sinks/llm_module_contract.py`) or to a setting. A field changes only
when its question is answered.

**Two numberings.** The exporter's code, its startup messages and the task
breakdown cite the breakdown's *Open items*. The last column gives that number
for each question here, so a message that says "Open item 7" lands on
question 4.

| # | Question | What it decides | Current assumption | Open item |
|---|---|---|---|---|
| 1 | Which embedding model embeds our chunks, and what is its token limit? | `CHUNK_PROFILE` — a 1200-character Estonian chunk against a tight token budget is silently truncated | None — an `llm_module` deployment must set `CHUNK_PROFILE` explicitly | 1 |
| 2 | Will you implement this endpoint, request and response (§1–§3)? | Everything | This proposal | 5 |
| 3 | Can you merge metadata without content and without re-embedding (§8), **all-or-nothing per document**, answered with the single `record` item? | `supports_metadata_only_merge` — `True` only if both halves are yes | `False` — full re-push | 8 |
| 4 | Will points be keyed on our `chunk_id`, with `agency_id` filterable (§4)? | Whether edits and deletes work at all; `agency_id_filterable`, which questions 11 and 12 need | Required | 7 |
| 5 | What concurrency and request rate will you accept? | `max_concurrency` | `2` parallel documents | 9 |
| 6 | What request-size ceiling will you accept? | `LLM_MODULE_MAX_REQUEST_BYTES` | 8 MiB | 9 |
| 7 | Is there an enrichment step between chunk and embed, and will it stay? | Retrieval quality after the cutover | Unknown | 11 |
| 8 | Who owns the collection swap and its rollback at cutover, and does retrieval read through an alias that can be switched atomically? | The cutover plan's swap and rollback commands (§12) | Unknown — an atomic Qdrant alias switch is proposed | 6 |
| 9 | Which item statuses can clear on a resend (§3)? Is a `422` item ever transient, or always a bad chunk? | `_RETRYABLE_ITEM_STATUSES` — a deterministic failure classed retryable is re-embedded every hour and never reported as permanent | `409`, `422`, `503` retryable; everything else permanent | 5 |
| 10 | Will you implement document and tail deletes (§9.1, §9.2), points before the record, with `404` for "already gone" **per item**, never for the whole request? | Whether deletions reach you at all | Required | 5 |
| 11 | Will you implement the agency delete (§9.3)? | `supports_agency_scoped_delete` — and with it a purge | `False` | 7 |
| 12 | Will you implement the keep-list sweep (§10), enforcing the keep list inside the delete itself? | `supports_stale_sweep` | `False` — no sweep runs | 7 |
| 13 | Can you expose the read-back probe (§11), and does it list ordinals? | `supports_read_back`, `read_back_lists_ordinals` — reconcile "clean" vs "unverifiable", and the first-run check | `False` | 10 |
| 14 | How many ids may one delete request name? | `DELETE_BATCH_IDS` | 500 | 5 |
| 15 | Will you route every request by the `{collection}` path segment (§1), and is a new collection created on first write or by you beforehand? | `LLM_MODULE_COLLECTION`; whether the bootstrap needs a create step | Required; created on first write | 6 |

`AGREED_TERMS.status` becomes `agreed` — and `CONTENT_SINK=llm_module` becomes
selectable without the development flag — once questions 1, 2, 4, 10 and 15
have recorded answers.
