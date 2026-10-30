# llm-module cutover — L19–L25

> **Status: PLAN, not executed.** The ingest contract is agreed
> ([`llm-module-ingest-contract.md`](llm-module-ingest-contract.md),
> `AGREED_TERMS.status = "agreed"`, recorded 2026-10-28 on an assumption;
> written sign-off pending), so step 1's code side is done. Still needed from
> the llm-module owner before step 2: §13 q1 (the embedder's token limit,
> which sets `CHUNK_PROFILE`), q7 (enrichment, §5) and q8 (the swap owner,
> §2). The commands below stay templates until those answers fill them in.
> Fill them in **before** step 2 runs, not after.

The llm-module chunks documents itself today. After the cutover it will not:
this pipeline becomes the only chunker on the retrieval path, and the
llm-module embeds the chunks we push. Its existing Qdrant corpus cannot be
carried forward. It was built with different chunk geometry, without NFC
normalisation, and with points keyed on `document_hash` instead of our
coordinate `chunk_id`. **No id mapping exists, and none can.** So this is a
re-index, not a migration, and the only real question is how to run it
safely. The reasoning is in the task breakdown,
[Chunking consolidates into this pipeline](../../content-external-pipeline-task-breakdown.md#chunking-consolidates-into-this-pipeline).

## 1. The order (L21)

Each step has a gate. Do not start a step until the one before it has passed
its gate.

| # | Step | Owner | Gate to pass before the next step |
|---|---|---|---|
| 0 | Retrieval reads through an **alias**, not a collection name | llm-module | `GET /collections/aliases` shows the alias pointing at the live collection, and retrieval answers through it |
| 1 | The exporter ships against the agreed contract | CKB | Conformance suite green against staging (Stage L *Done when*) |
| 2 | **Bootstrap beside** into a new physical collection (L19) | CKB | Bootstrap report read; live collection unchanged ([§2](#2-bootstrap-beside-l19)) |
| 3 | Hourly incremental runs keep the new collection current | CKB | Two consecutive `success` / `unchanged` runs |
| 4 | **Retrieval comparison** on their benchmark, their chunks against ours (L20) | llm-module | Result recorded **with a decision** ([§4](#4-retrieval-comparison-l20)) |
| 5 | **Swap** the alias to the new collection | named in §13 q8 | Verification 14–17 pass after the swap ([§7](#7-the-hourly-zip-job-stays-l24)) |
| 6 | Settling period | both | Agreed length has passed with no rollback |
| 7 | **Only now:** the llm-module removes its chunker, `document_loader.py`, `dataset_download.py`, `diff_identifier/`, `processed-metadata.json` and the DVC remnants | llm-module | — |
| 8 | Drop the old collection (L23) | named owner | [§6](#6-dropping-the-old-corpus-l23) |

**Why step 7 comes after step 4, not before it.** Removing their chunker
first is the obvious order, and it is wrong. The same commit would delete the
fallback that rollback relies on and the only point at which the two
chunkers can be compared. Until step 7, rollback is a single alias move,
because their old pipeline is still keeping the old collection current.
After step 7, rollback means re-bootstrapping. That is why the settling
period comes before step 7.

## 2. Bootstrap beside (L19)

**Configuration.** Set this on the exporter deployment. Everything else
stays as Stage L configured it.

```ini
CONTENT_SINK=llm_module
LLM_MODULE_COLLECTION=ckb_chunks_v1          # the NEW physical collection
LLM_MODULE_PROTECTED_COLLECTIONS=ckb_retrieval   # the alias, + any live name
MANIFEST_STORE_PREFIX=content-manifests/ckb_chunks_v1
CHUNK_PROFILE=<pinned from §13 q1>
LLM_MODULE_CHUNK_PROFILE_CONFIRMED=<the same preset name>
DIFF_ON_MISSING_MANIFEST=fail                # from the start; see below
```

- `LLM_MODULE_CHUNK_PROFILE_CONFIRMED` repeats `CHUNK_PROFILE` once it has
  been checked against the embedder's token limit (§13 q1). Startup refuses
  `llm_module` until the two match. `CHUNK_PROFILE` alone cannot show the
  check happened, because compose and the chart set it for every
  deployment.
- `LLM_MODULE_ENABLE_METADATA_MERGE` stays unset for the bootstrap (§8).

- `LLM_MODULE_COLLECTION` names the **physical** collection, never the
  alias. The swap and the rollback both move the alias, so if the exporter
  wrote through it, a rollback would point its writes at the old corpus.
  Startup refuses `contextual_chunks_azure` and `contextual_chunks_aws`
  itself. Put the **alias** and any other live collection in
  `LLM_MODULE_PROTECTED_COLLECTIONS`, and startup refuses those too. Only
  the deployment knows those names.
- The collection is part of the manifest's sink_id
  (`llm_module:ckb_chunks_v1`). Pointing an existing manifest at a
  different collection makes the run **fail** (`error_type=SinkMismatch`).
  It never reads as already published, and never as a first run. One
  manifest prefix per collection keeps that guard quiet in normal
  operation.
- Before the first run, confirm the target in `GET /health`. Its `sink_id`
  must read `llm_module:ckb_chunks_v1`. The run line and the error line
  carry the same value.
- `DIFF_ON_MISSING_MANIFEST` defaults to `first_run`. Set it to `fail` in
  the deployment from the start, and override it **only on the slice
  command below**. With `fail` in place, a later lost manifest fails the run
  loudly instead of treating it as a first run. The first-run probe is not
  enough on its own: it reads back only the first 25 rows, so a destination
  that holds none of those reads as `empty`.

**Commands.** Run them inside the exporter container, by hand, with the cron
(Stage G) still disabled:

```bash
# A slice first. Only this command creates the manifest, so only this
# command may run as a first run.
DIFF_ON_MISSING_MANIFEST=first_run \
  python -m exporter.run_sync --agency-id <agency> --limit 25 --output-json slice.json

# Check: outcome success, publish_failed_count 0, first_run_probe "empty"
# (read-back is agreed, §13 q13; a failed read-back fails the run instead),
# and the manifest now exists under MANIFEST_STORE_PREFIX.

# The full bootstrap, with the deployment's DIFF_ON_MISSING_MANIFEST=fail:
# it continues from the slice's manifest, so the documents the slice
# published are not sent again. If that manifest is somehow missing, this
# run fails rather than starting over unchecked. Lower EXPORT_CONCURRENCY
# for this run if the llm-module asked for it (§13 q5): every chunk is
# embedded.
python -m exporter.run_sync --agency-id <agency> --output-json bootstrap.json
```

Then enable the cron.

**Check that the live collection is unchanged.** Record the point count and
a snapshot id of the live collection before step 2 and after it. They must
be identical, apart from any changes the llm-module's own pipeline made in
the meantime.

**Write the swap and the rollback down now, before the bootstrap runs.**
Here is a template, assuming retrieval reads through the alias
`ckb_retrieval` (step 0; the names come from §13 q8). Qdrant applies all of
one request's alias actions atomically, so retrieval never sees a moment
with no alias.

`<old>` is the collection the alias points at **today**:
`contextual_chunks_azure` or `contextual_chunks_aws`. They are partitioned
by embedding provider, so it is whichever provider this deployment
retrieves from. Read it from the first command below, and record it in the
table. Never assume it.

```bash
# BEFORE — record what the alias points at now; that is <old>
curl -s "$QDRANT/collections/aliases" -H "api-key: $QDRANT_API_KEY"

# SWAP — retrieval moves to the new collection
curl -sf -X POST "$QDRANT/collections/aliases" \
  -H "api-key: $QDRANT_API_KEY" -H 'Content-Type: application/json' -d '{
  "actions": [
    {"delete_alias": {"alias_name": "ckb_retrieval"}},
    {"create_alias": {"alias_name": "ckb_retrieval", "collection_name": "ckb_chunks_v1"}}
  ]}'

# ROLLBACK — retrieval moves back to <old>
curl -sf -X POST "$QDRANT/collections/aliases" \
  -H "api-key: $QDRANT_API_KEY" -H 'Content-Type: application/json' -d '{
  "actions": [
    {"delete_alias": {"alias_name": "ckb_retrieval"}},
    {"create_alias": {"alias_name": "ckb_retrieval", "collection_name": "<old>"}}
  ]}'

# AFTER either one — confirm the alias points where it should
curl -s "$QDRANT/collections/aliases" -H "api-key: $QDRANT_API_KEY"
```

The `delete_alias` action assumes the alias exists, which is what step 0
establishes. If step 0 has not been done, these commands are not the swap.
`-f` makes a refused request exit non-zero instead of looking like it
succeeded. Drop the `api-key` header only if this Qdrant runs without
authentication.

| | Value |
|---|---|
| Alias retrieval reads through | |
| Old (live) collection `<old>`, as read from the alias | |
| New collection (`LLM_MODULE_COLLECTION`) | |
| `LLM_MODULE_PROTECTED_COLLECTIONS` includes the alias | yes / no |
| Swap and rollback owner (§13 q8) | |
| Both commands dry-run on staging (Verification 28) | date: |

**Neither command touches the exporter.** It keeps writing to the physical
new collection through a swap, a rollback and a re-swap. That is harmless,
and it keeps the new collection current for the next attempt.

## 3. If the comparison regresses: re-bootstrap, never in place

The usual fix is a different chunk profile (B11): a third validated preset,
if neither existing one fits the embedder. Do not re-chunk the bootstrapped
collection in place. Bootstrap again **beside it**.

**First, settle v1.** The exporter's work directory (the run lock, deletion
journals, the bootstrap marker and the dead-letter record) is keyed by
agency, not by collection, so v1 and v2 share it. Before re-pointing, the
**last run against v1 must have ended `success` or `unchanged`**, which
means no bootstrap is half-done and no deletion journal is still open. If
one is still open, v2's first committed run closes it, and v1's pending
deletes are abandoned. An interrupted v1 bootstrap marker is replaced by
v2's, so v1 could no longer resume. Then:

```ini
LLM_MODULE_COLLECTION=ckb_chunks_v2
MANIFEST_STORE_PREFIX=content-manifests/ckb_chunks_v2   # fresh — keep v1's manifest
CHUNK_PROFILE=<new preset>
LLM_MODULE_CHUNK_PROFILE_CONFIRMED=<new preset>
```

Then repeat §2 and §4. The new prefix is required, not a convenience. On the
old prefix, the run fails with `SinkMismatch`, which is the guard working.

**v1 is frozen from the moment the exporter moves to v2.** Nothing writes to
it any more, so it falls behind CKB with every hourly tick. Keep it and its
manifest as the **comparison baseline** until v2 has passed §4. It is **not
a swap target** as it stands. If the decision is to swap to v1 after all,
catch it up first: put `LLM_MODULE_COLLECTION`, `MANIFEST_STORE_PREFIX`
**and `CHUNK_PROFILE`** (with its confirmation) back to v1's values, and run
once. The sink_id and
the chunker fingerprint then match v1's manifest, so it is an ordinary
incremental run that sends only what changed while v1 was frozen. If the
profile were left at v2's, the run would be refused as an unacknowledged
re-chunk of the whole of v1 (L18). Once v2 is chosen, drop v1 the same way as §6: by a named owner,
never by a sweep.

## 4. Retrieval comparison (L20)

This is the acceptance gate for the whole consolidation, and it **cannot be
repeated** once step 7 has run. Run the llm-module owner's existing
benchmark against both collections, on the same corpus, at the same time.

| Field | Value |
|---|---|
| Date | |
| Agency, CKB live document count | |
| Exporter manifest `committed_at` / last run_id | |
| `CHUNK_PROFILE` and chunker fingerprint (from the `content-external config` log line) | |
| Embedding model and token limit (§13 q1, Open item 1) | |
| **Same embedding model, same filters and same retrieval settings on both sides?** | yes / no — if no, what differs |
| Enrichment applied to our chunks? (§5) | |
| Old collection: documents / points held | |
| New collection: documents / points held (documents should equal the manifest's count) | |
| **Both collections current at comparison time?** Old: last update by their pipeline. New: last exporter run `success`/`unchanged` | |
| Benchmark name and version | |
| Query set: size, and where the queries came from | |
| `k` (results per query) and any re-ranking | |
| Metric(s) — theirs (old collection) | |
| Metric(s) — ours (new collection) | |
| **Decision**: swap / change profile and re-bootstrap (§3) / stop | |
| Signed off by (CKB, llm-module) | |

A comparison only measures the chunkers when everything else is equal. If
one collection is behind the other, if the embedders or filters differ, or
if the query set is too small to separate the two, the number reflects that
difference instead. That is why those rows exist. A regression is a
measurement, not a failure. It is also the most likely place an unnamed
enrichment step shows up (§5).

## 5. What happens between chunk and embed (L22)

The collection names `contextual_chunks_*` suggest a step between chunking
and embedding: a context prefix, a summary, or a rewrite. Today that step
sits next to the chunker that is being removed. Before step 4, record which
of these is true:

| | Decision |
|---|---|
| Is there an enrichment step? (§13 q7, Open item 11) | |
| If yes: does it **stay, applied to our chunks**, or is it **removed**? | |
| If it stays: confirmed that the point id is still our `chunk_id` after enrichment (contract §4) | |

Either answer is acceptable. Losing the step by accident is not: a retrieval
drop traced back to a step that vanished with someone else's chunker is an
expensive way to find it.

## 6. Dropping the old corpus (L23)

After the settling period, the old collection is deleted by an **operator
action with a named owner**, as a separate change:

| | Value |
|---|---|
| Owner | |
| Settling period ends | |
| Rollback window formally closed by | |
| Dropped on | |

Nothing in the exporter will ever do this, and that is deliberate. The old
collection is not in any manifest this pipeline wrote, so `run_purge` and
the stale sweep know nothing about it. The exporter cannot even be pointed
at it, because startup refuses `contextual_chunks_*` and anything in
`LLM_MODULE_PROTECTED_COLLECTIONS`. If nobody owns the
drop, the old collection becomes permanent double storage that no one is
sure is safe to remove.

## 7. The hourly ZIP job stays (L24)

**This cutover changes nothing about the ZIP.** The llm-module stops pulling
`zipped/{agency}.zip`, but the Global Classifier is a separate consumer of
that artifact and is unaffected. `trigger_zipping` (`"0 0 */1 * * ?"`),
`pipeline/zip.yml`, the `zip_dirty` / `is_zipping` pair, `agency.data_hash`,
`agency.zipped_data_url` and the three `client/data/*` endpoints all keep
running unchanged.

The risk here is a plausible inference rather than a careless mistake. After
the cutover the ZIP has one consumer left, and "the llm-module doesn't pull
it any more" reads like "nothing pulls it any more". If the job is removed or
disabled, `data_hash` freezes, and the Classifier reports "no sync required"
indefinitely, with no error anywhere. `tests/test_classifier_safety.py`
fails if the job disappears from `DSL/CronManager/scheduler.yml` or changes
schedule.

**Re-run [Verification 14–17](../../content-external-pipeline.md#integration--global-classifier-regression)
after the swap (step 5).** The ZIP must still build hourly, `data_hash` must
still move, and `client/data/import` must still resolve to a byte-identical
file set.

## 8. Metadata-merge atomicity (L25)

Settled in the contract,
[§8](llm-module-ingest-contract.md#8-metadata-only-merge). A merge is
all-or-nothing per document, and its answer is the single `record` item. A
merge that is not acknowledged keeps the document's old manifest entry, so
the next run sends the whole merge again. A merge answered point by point is
refused.

§13 q3 is agreed, but on an assumption, and one failure stays invisible to
the exporter: a far side that answers the `record` item `200` without
applying the merge. The manifest would then vouch for metadata the
destination does not hold, and nothing would retry it. So **the exporter
sends no merge unless the deployment sets
`LLM_MODULE_ENABLE_METADATA_MERGE=true`**. Until then, every metadata change
is a counted full re-push, which is always correct and costs a re-embed. Set
the flag only after the llm-module owner confirms the merge is implemented
all-or-nothing. Before switching it on, check on staging that a metadata-only
edit shows up in the collection.

## Gate checklist — before the swap (step 5)

This mirrors the production-readiness checklist,
[Before the llm-module cutover swap](../../content-external-pipeline-production-readiness.md).

- [x] Contract agreed: `AGREED_TERMS.status = "agreed"`, §13 q2, 4, 10 and 15 answered (recorded 2026-10-28 on an assumption; **written sign-off pending**)
- [ ] §13 q1 answered, `CHUNK_PROFILE` pinned from it and confirmed in `LLM_MODULE_CHUNK_PROFILE_CONFIRMED`; q7 and q8 recorded (§5, §2)
- [x] L25: merges are off by default (`LLM_MODULE_ENABLE_METADATA_MERGE` unset). Turn them on only after the far side's implementation is confirmed and checked on staging (§8)
- [ ] §2 bootstrap complete; live collection unchanged; `/health` `sink_id` names the new collection
- [ ] The alias is in `LLM_MODULE_PROTECTED_COLLECTIONS`, and `DIFF_ON_MISSING_MANIFEST=fail` is set on the deployment
- [ ] Swap and rollback written down (§2) and **both exercised on staging** (Verification 28)
- [ ] §4 comparison recorded with a decision, **while both chunkers still exist** (Verification 29)
- [ ] §5 enrichment decision recorded
- [ ] Settling period length agreed, and the §6 owner named
- [ ] After the swap: Verification 14–17 pass (Verification 30, L24)
