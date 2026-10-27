# Content External Service — runbook

Operator notes for running the exporter. Design and configuration are in
[README.md](README.md).

## Export query ceiling (#320)

Enumeration makes one watermark call and one document call per page, from
`exporter/services/document_source.py`, each with the `CKB_RESQL_TIMEOUT`
timeout (default 30 s). `CKB_RESQL` is the exporter's Resql base URL setting;
its default is in `exporter/api/config.py`.

- `GET $CKB_RESQL/agency/get_agency_export_watermark` (one row)
- `GET $CKB_RESQL/source_file/list_agency_export_documents` (up to `EXPORT_PAGE_SIZE` documents)

Both take the latest row per `base_id`, so their cost grows with **every
version row** of the agency, not with its document count. `source_file` gets a
new row on every status change. Document pages use an exclusive `base_id` cursor,
starting at `NIL_UUID` from `document_source.py`; a short page ends the listing.
An exact multiple of the page size requires a final empty page. All pages must
succeed before any documents are returned. Each call has the `CKB_RESQL_TIMEOUT`
timeout. Connection errors, 408/429 and 5xx are retried under F8 (up to 4
attempts, full jitter, `Retry-After` honoured), each logged as `Resql <query>:
attempt N of 4 failed`. A **read timeout is never retried**, for the reason in
[Do not retry immediately after a timeout](#do-not-retry-immediately-after-a-timeout):
the run fails and the next tick is the retry. Successful enumeration logs the
agency, page count (including an empty terminal page), and total row count.

Both queries also read `data_collection.source`, so that a deleted source
deletes its files (`source/delete_source.sql` marks only the source row). The
document query looks up each page row's latest source version with a
`LEFT JOIN LATERAL`. The watermark adds the newest *deleted* source version and
counts live files against their source's latest version. The current numbers
are in [With the source-deletion fix](#with-the-source-deletion-fix--measured-2026-10-22);
the two sections after it are the history that chose the page size.

### With the source-deletion fix — measured 2026-10-22

Measured on one machine against the queries committed before the fix (`old`)
and after it (`new`), so the effect of the fix is measured rather than
compared with another machine's numbers. Default page size 1,000.

| Queries | Source history | Warm EXPLAIN, watermark | Warm EXPLAIN, first / middle page | Resql watermark, 3 calls after restart | Resql per page: median / p90 / max | `list_documents()`, 9 back to back | Peak RSS | Timeouts |
|---|---|---|---|---|---|---|---|---|
| old | not read | 1.28 s | 0.050 / 0.056 s | 1.30 / 1.20 / 1.12 s | 0.087 / 0.134 / 0.383 s | 5.6–6.3 s | 189 MB | 0 |
| new | 1,650 versions per source | 0.77 s | 0.067 / 0.057 s | 0.85 / 0.68 / 0.66 s | 0.099 / 0.133 / 0.168 s | 5.8–6.6 s | 189 MB | 0 |
| new | 39,600 versions per source | 1.36 s | 0.112 / 0.057 s | 1.35 / 1.21 / 1.22 s | 0.095 / 0.153 / 0.187 s | 6.0–6.7 s | 189 MB | 0 |

At the 10k step the two are as close: warm watermark 0.53 s new against
0.47 s old, pages 0.07 / 0.06 s against 0.05 / 0.05 s, listings 1.1–1.5 s
against 1.0–1.5 s. At 5,000 per page (50k step), pages took 0.27 / 0.31 s new
against 0.32 / 0.24 s old, with no Sort in either.

Data and conditions:

- **Data**: `scripts/measure_export_queries.sql` with its defaults, the 10k
  then the 50k step. At 50k: 50,000 documents and 998,327 version rows in the
  measured agency, 1,787,246 `source_file` rows in all, the same shape as the
  tables below. Sources: 25 per agency, 1,650 versions each (about five
  source writes per scrape, one scrape a day), 206,250 `source` rows in all.
  One source in 25 ends deleted, so 4% of documents belong to a deleted
  source. The deep row reloaded sources at 39,600 versions each (an hourly
  scrape): 990,000 per agency, 4.95M rows and 5.2 GB in all.
- **Machine**: Windows 11, Docker Desktop 29.7.2 on WSL2 (16 CPUs, 15.5 GB),
  data on a named Docker volume. Another project's containers were running on
  the same engine. Postgres 14.1 with defaults (`shared_buffers` 128 MB,
  `work_mem` 4 MB, `/dev/shm` 64 MB). `VACUUM (ANALYZE)` on both tables after
  each load, as autovacuum would reach; with the 64 MB `/dev/shm` it needs
  `PARALLEL 0`. Resql from the local `resql:latest` image. This machine lists
  the same 50k agency about 5× faster than the one below, so compare rows
  within this table only.
- **Columns**: as in the sections below. Old Resql timings used a second Resql
  serving a copy of `DSL/Resql` with the two old files, and the committed
  `document_source.py`, which expects the old columns.

What the plans show:

- **Pages are unchanged in shape.** Every page is an ordered Index Scan on
  `idx_source_file_agency_base_updated` with no Sort. The source lookup is an
  Index Only Scan on `idx_source_base_updated_deleted_auto_scrapping_status`
  with no heap fetches: about 3 buffer hits and 4–11 µs per row at either
  source depth, so it does not grow with source history.
- **The watermark is faster at realistic depth.** Its latest-file step now
  feeds a join, and the planner runs that Sort in parallel (a Parallel Seq Scan
  and Gather Merge over 3 processes) where the old scalar subquery sorted in
  one. The two new source parts are Index Only Scans on
  `idx_source_agency_base_updated_deleted_url_subsector_scraped_st`: 2 ms and
  5 ms at 1,650 versions per source, 83 ms and 180 ms at 39,600.
- **What grows**: the watermark now also grows with the agency's source
  version rows, by about 0.6 s per million here. Page time does not.
- **The fix is visible in the results**: the new watermark's `liveCount` was
  45,633 against 47,542, and the new listing marked 4,367 rows deleted against
  2,458. The 1,909 extra are the deleted source's files.

### Before pagination — measured 2026-10-20, commit 1c483d3

| Step | Documents | Version rows (agency) | Table rows (all agencies) | EXPLAIN, document query | EXPLAIN, watermark | Resql, document call | Response | `list_documents()` | Peak RSS |
|---|---|---|---|---|---|---|---|---|---|
| 10k, before pagination | 10,000 | 204,758 | 1,001,977 | 1.25 s | 0.95 s | 1.50 s | 8.0 MB | 1.6–1.9 s | ~110 MB |
| 50k, before pagination | 50,000 | 989,753 | 1,786,972 | 8.1 s (8.1–22.0 s over 4 warm runs) | 2.1 s (2.1–8.9 s) | 8.2–9.7 s | 40.2 MB | 8.5–9.9 s | ~310 MB |

Sources and conditions:

- **Data**: synthetic, from `scripts/measure_export_queries.sql`. Version
  depth: 90% of documents have 5–15 versions, 10% have 16–200 (mean about 20).
  Latest version: about 5% deleted, 2% excluded, 2% `not_found`, 5% scraping
  or cleaning, the rest finished. URL columns are about 120 characters.
- **Other agencies**: four other agencies of 10,000 documents each (about
  200k rows each) were present at both steps. During the 50k step they stayed
  at 10k-step volume.
- **Columns**: EXPLAIN is the warm (third) `EXPLAIN (ANALYZE, BUFFERS)` of a
  prepared statement with a `varchar` parameter, as Resql sends it. Resql and
  `list_documents()` are wall-clock times for one call at a time, with a
  freshly started Resql and no other query running. Peak RSS is the Python
  process during `list_documents()`.
- **Machine**: a laptop running Rancher Desktop (2 CPUs, 4 GB VM), with the
  database data directory bind-mounted from macOS.
  - Postgres 14.1 with defaults: `shared_buffers` 160 MB, `work_mem` 4 MB,
    `random_page_cost` 4, `/dev/shm` 64 MB.
  - Resql built from `main` @ ce2fe1c, JVM heap ceiling about 980 MB.
  - Treat every number as indicative. Production hardware and settings
    differ.
- **Not measured**: the planned 200k step was skipped because the full ladder
  did not fit on the machine's disk (about 2.2 KB per row including indexes).

### With pagination — measured 2026-10-21

`EXPORT_PAGE_SIZE` defaults to **1,000**, with a minimum of 1. It is the only
size measured whose pages stayed well under the 15 s per-call budget in every
run, and whose first and middle page plans both use the index with no Sort.

The 50k step above was reloaded and measured on the same machine and settings,
with the paged query from the working tree on top of commit 55a61ad.

| Page size | Warm EXPLAIN, first / middle page | Resql per page: median / p90 / max | Response per page | Total `list_documents()`: run 1 (range of 9) | Peak RSS | Timeouts in 9 listings |
|---|---|---|---|---|---|---|
| 1,000 (default) | 0.64 s / 0.66 s. Index Scan on `idx_source_file_agency_base_updated`, no Sort. About 20k buffers per page, 18–19k of them read | 0.60 / 0.72 / 1.53 s, over 51 calls | 0.81 MB | 32.3 s (24.3–35.9 s) | 134 MB | 0 |
| 5,000 | 2.9 s / 2.8 s. Index Scan, no Sort. 98–101k buffers, 88–91k read | 3.7 / 5.9 / 7.5 s, over 11 calls | 4.0 MB | 31.4 s (31.4–58.9 s) | 156 MB | 0 |
| 20,000 | 9.8 s / 2.7 s. First: Index Scan, no Sort, 396k buffers. Middle: **Parallel Seq Scan and Sort**, external merge with about 83 MB on disk per worker | 9.8 / 17.5 / 27.9 s, over 3 calls | 16.1 MB | 40.3 s (19.7–56.8 s over the 8 that finished) | 218 MB | 1 |
| Single query, before pagination | 8.1 s. Seq Scan and Sort | 8.2–9.7 s, 1 call | 40.2 MB | 8.5–9.9 s single; back-to-back see the last column | ~310 MB | 7 |

**Watermark** (query unchanged): warm EXPLAIN 1.96 s. `MAX` is an Index Only
Scan; `live_count` is a Seq Scan and an external-merge Sort of all 989,753
version rows (40 MB on disk). Through Resql it took 3.2–4.5 s on the first call
after a Resql restart and 1.7–3.2 s after that. It is now the slowest single
call at the default page size.

Conditions:

- **Per-page time** is the wall-clock time of each `requests.get` inside
  `list_documents()`, including reading the body. The median, p90 and max
  leave out the empty terminal page.
- **Runs**: for each page size, Resql was restarted, the watermark was called
  three times, then nine `list_documents()` calls ran back to back in one
  Python process with no pause. Run 1 is the first listing after the restart.
  Sizes ran in the order 1,000, 5,000, 20,000, so the VM's file cache was
  warm from the previous size.
- **EXPLAIN**: the third `EXPLAIN (ANALYZE, BUFFERS)` of a freshly prepared
  statement with `varchar` parameters, per page size and cursor. The middle
  cursor is the agency's 25,000th `base_id`.
- **Peak RSS** is the Python process's maximum over its nine listings.

What the numbers show:

- **Pages are short and independent of agency size.** The first and middle
  pages cost the same. At 1,000 the slowest page took 1.53 s, 10% of the
  budget, and no back-to-back listing timed out, where 7 of 9 did before
  pagination.
- **The total listing is about 3× slower** than the single query: 24–36 s
  against 8.5–9.9 s. Each page reads every version of its documents from the
  heap in index order, about 20 per document. That adds up to roughly 1M
  buffer reads per listing, against one sequential pass over the table.
  Pagination bounds each call; it does not make enumeration cheaper.
- **5,000** needs fewer calls but no less time. Its pages reached 7.5 s and
  totals 59 s once back-to-back runs piled up.
- **20,000 breaks the plan.** For the middle page the planner sorts the
  whole agency again. The first page reached 27.9 s and one listing timed out.

### Ceiling and conclusion

- **Ceiling**: ≥ 50k documents (about 1M version rows) and ≥ 1M source
  version rows per agency, at the default page size; 200k documents not
  measured. The budget is 15 s per HTTP call, half of the default
  `CKB_RESQL_TIMEOUT` (30 s). On the 2026-10-20/21 machine the slowest call at
  50k was the watermark at 4.5 s (30%) and the slowest page 1.53 s. With the
  source-deletion fix, on the 2026-10-22 machine, the slowest call at 50k
  with 990k source versions was the watermark at 1.35 s (9%) and the slowest
  page 0.19 s. The fix added no measurable cost to a page and made the
  watermark faster at daily-scrape depth. This is not an extrapolation beyond
  either measured size.
- **Before pagination the margin at 50k was thin**, and this is what paging
  fixed:
  - The single document query took 9.9 s at most for single calls, 66% of
    the budget, and up to 22 s in the database with a cold VM file cache.
  - Back-to-back calls took 14.7–18.3 s through Resql.
  - 7 of 9 back-to-back `list_documents()` calls hit the 30 s timeout. A
    timed-out request leaves its query running in Postgres, and Resql
    does not cancel it, so the next call competes with it (see
    [Signs the ceiling is being hit](#signs-the-ceiling-is-being-hit)).
- **What still grows with the agency**: the watermark call and the total
  listing time both scale with the agency's version rows, and the watermark
  also with its source version rows. Page time does not.
- **Expected production size per agency is unknown.** It has been asked from
  the business side.
- **Re-check threshold**: re-measure before go-live (step 5 below) for any
  agency above 50,000 documents, about 1M version rows or about 1M source
  version rows, the measured range.
- **Total table size matters too.** The watermark's `live_count` reads the
  whole of `source_file`, so growth in other agencies also slows every
  agency's export.

## Re-measure before go-live (#320)

Run this against a **copy** of production-like data, never against production
itself, and use the target deployment's Postgres settings.

1. **Count the largest agency's rows** and the table total:

   ```sql
   SELECT agency_base_id, count(*) AS version_rows, count(DISTINCT base_id) AS documents
   FROM data_collection.source_file
   GROUP BY agency_base_id ORDER BY version_rows DESC LIMIT 5;

   -- The watermark also reads the agency's source history.
   SELECT agency_base_id, count(*) AS source_version_rows, count(DISTINCT base_id) AS sources
   FROM data_collection.source
   GROUP BY agency_base_id ORDER BY source_version_rows DESC LIMIT 5;
   ```

2. **EXPLAIN both queries as Resql runs them**, three times each, keeping the
   third. Replace `<agency>`. The query bodies are the two `.sql` files with
   their header comment removed and `:agency_base_id::UUID` written as
   `$1::UUID`. For documents also replace `:after_base_id::UUID` with
   `$2::UUID` and `:page_size::INTEGER` with `$3::INTEGER`. Use `NIL_UUID`
   from `document_source.py` for `<cursor>` on the first page, and the last
   `baseId` of the preceding page for a page near the middle of the agency.
   Check both cursors for each candidate `<page-size>`:

   ```sql
   PREPARE wm(varchar) AS
   WITH latest_sources AS (
       SELECT DISTINCT ON (s.base_id) s.base_id, s.is_deleted
       FROM data_collection.source AS s
       WHERE s.agency_base_id = $1::UUID
       ORDER BY s.base_id, s.updated_at DESC
   ),
   latest_files AS (
       SELECT DISTINCT ON (sf.base_id) sf.base_id, sf.source_base_id, sf.is_deleted
       FROM data_collection.source_file AS sf
       WHERE sf.agency_base_id = $1::UUID
       ORDER BY sf.base_id, sf.updated_at DESC
   )
   SELECT
       GREATEST(
           (SELECT MAX(sf.updated_at) FROM data_collection.source_file AS sf
            WHERE sf.agency_base_id = $1::UUID),
           (SELECT MAX(s.updated_at) FROM data_collection.source AS s
            WHERE s.agency_base_id = $1::UUID AND s.is_deleted = TRUE)
       ) AS max_updated_at,
       (SELECT COUNT(*) FROM latest_files AS lf
        LEFT JOIN latest_sources AS ls ON lf.source_base_id = ls.base_id
        WHERE lf.is_deleted = FALSE AND ls.is_deleted IS NOT TRUE) AS live_count;
   EXPLAIN (ANALYZE, BUFFERS) EXECUTE wm('<agency>');  -- run 3 times

   PREPARE docs(varchar, varchar, varchar) AS
   WITH page AS (
       SELECT DISTINCT ON (base_id)
           base_id, source_base_id, url, page_title, subsector, status,
           cleaned_data_url, cleaned_metadata_url, edited_data_url, edited_metadata_url,
           file_size, is_excluded, is_deleted, type, updated_at
       FROM data_collection.source_file
       WHERE agency_base_id = $1::UUID AND base_id > $2::UUID
       ORDER BY base_id, updated_at DESC
       LIMIT $3::INTEGER
   )
   SELECT p.*, COALESCE(ls.is_deleted, FALSE) AS source_is_deleted
   FROM page AS p
   LEFT JOIN LATERAL (
       SELECT s.is_deleted FROM data_collection.source AS s
       WHERE s.base_id = p.source_base_id
       ORDER BY s.updated_at DESC LIMIT 1
   ) AS ls ON TRUE
   ORDER BY p.base_id;
   EXPLAIN (ANALYZE, BUFFERS) EXECUTE docs('<agency>', '<cursor>', '<page-size>');
   -- Run 3 times for each cursor / page size, retaining the third plan.
   ```

   If these examples differ from the .sql files, the files win. Check the
   source lookup too: it should be an Index Only Scan on a `source` index
   leading with `base_id, updated_at DESC`, with no heap fetches after a
   vacuum.

3. **Time both calls end to end through Resql**, one call at a time, three
   times each:

   ```bash
   curl -s -o /dev/null --max-time 300 \
     -w 'watermark %{http_code} %{time_total}s %{size_download}B\n' \
     "$CKB_RESQL/agency/get_agency_export_watermark?agency_base_id=<agency>"
   curl -s --get --max-time 300 \
     --data-urlencode 'agency_base_id=<agency>' \
     --data-urlencode 'after_base_id=<cursor>' \
     --data-urlencode 'page_size=<page-size>' \
     -o <output-file> \
     -w 'documents %{http_code} %{time_total}s %{size_download}B\n' \
     "$CKB_RESQL/source_file/list_agency_export_documents"
   ```

4. **Compare** against the table above.
   - Is every page well under 15 s, including pages near the middle?
   - Do the warm page plans use `idx_source_file_agency_base_updated` with
     **no Sort**? Record shared hit/read and temporary buffer counts.
   - How do total listing time, buffer reads, response sizes and peak RSS
     compare with the old single-query row? Do not hide a slower total.

5. **Choose the page size** using the 1,000 / 5,000 / 20,000 comparison, then
   run nine back-to-back `list_documents()` calls and record timeouts. If a
   page approaches 15 s, reduce the page size and re-measure. Do not raise the
   timeout to compensate. Update the default and the tables from these results.

To reproduce the synthetic ladder on a **local, disposable** database only,
use `scripts/measure_export_queries.sql`. Never run it against a shared,
staging or production database: it inserts millions of rows into
`data_collection.source_file` and `data_collection.source`. Sources are
generated once per agency and kept by later steps; `-v source_versions=<n>`
sets their depth (default 1,650), and a deeper run needs their rows removed
first.

- It refuses to run without `-v measure_local_only=yes` and
  `-v measure_agency_id=<agency-uuid>`. Loading also requires
  `-v bucket=<bucket>`, the bucket name written into the generated `s3://` URLs.
- It also refuses an `<agency-uuid>` that has an agency row, or whose (or a
  derived agency's) `source_file` or `source` rows it did not generate, for
  load and cleanup alike. Cleanup deletes all of those agencies' rows, so pass
  a fresh UUID that no real agency uses.
- The four other agencies are derived from `<agency-uuid>`, so cleanup needs
  the same value to find them.
- `<db-container>`, `<db-user>` and `<db-name>` are the Postgres container,
  user and database; the local compose values are in `docker-compose.yml`.

```bash
# 10k step
docker exec -i <db-container> psql -U <db-user> -d <db-name> \
  -v measure_local_only=yes -v measure_agency_id=<agency-uuid> -v bucket=<bucket> \
  -v n_docs=10000 \
  -f - < content-external/scripts/measure_export_queries.sql

# 50k step, after the 10k step
docker exec -i <db-container> psql -U <db-user> -d <db-name> \
  -v measure_local_only=yes -v measure_agency_id=<agency-uuid> -v bucket=<bucket> \
  -v n_docs=50000 -v others_n_docs=10000 \
  -f - < content-external/scripts/measure_export_queries.sql

# Remove everything both steps created
docker exec -i <db-container> psql -U <db-user> -d <db-name> \
  -v measure_local_only=yes -v measure_agency_id=<agency-uuid> -v action=cleanup \
  -f - < content-external/scripts/measure_export_queries.sql
```

The generator's shape (version depth, outcome shares, history dates, URL
format) is a block of `\set` defaults at the top of the script. Those defaults
produced the tables above, and any of them can be overridden with `-v`. Every
generated value is hashed from the agency id, so a different `<agency-uuid>`
gives the same distributions but slightly different row counts than the
tables show.

After cleanup, verify the seed watermark has `maxUpdatedAt` at 12:00 and
`liveCount` 3, and that following the paged endpoint to a short page yields the
seed's four rows.

## Signs the ceiling is being hit

**What the operator sees:**

- **Exporter**: every run fails during enumeration with
  `DocumentSourceError: could not read Resql`. Its cause is
  `requests.exceptions.ReadTimeout … (read timeout=30.0)`. It fails the same
  way on every run.
- **resql-ckb log**: `AsyncRequestNotUsableException … Broken pipe` for
  `/ckb/source_file/list_agency_export_documents`. Resql finished after the
  exporter had already disconnected.
- **Postgres**: `pg_stat_activity` shows the export query still `active`
  after more than 30 s. The watermark waits on `BufFileRead` while its Sort
  spills to disk. A document page at the default size does not sort, so a
  slow page is reading the heap. Abandoned queries keep running,
  so each run adds load for the next one.

**What to do:**

1. Find the agency's version-row count (step 1 above) and compare it with the
   table.
2. Cancel export queries that have run longer than `CKB_RESQL_TIMEOUT`
   (30 s by default). Their exporter request has already timed out. Resql keeps its
   database connection open, so a client that has gone is not visible in
   `pg_stat_activity`; the age of the query is the signal:

   ```sql
   SELECT pid, now() - query_start AS age, pg_cancel_backend(pid)
   FROM pg_stat_activity
   WHERE state = 'active'
     AND (query LIKE '%retain removal and hold signals%'
          OR query LIKE '%advance the export watermark%')
     AND now() - query_start > interval '<CKB_RESQL_TIMEOUT> seconds'
     AND pid <> pg_backend_pid();
   ```

   Resql sends each query with its header comment, and the two patterns are
   the `description` lines of `list_agency_export_documents.sql` and
   `get_agency_export_watermark.sql`. No other file in `DSL/Resql` contains
   them. The file names themselves do not appear in the query text. If a
   description changes, update the pattern.
3. Re-measure as above. If a document page is over 15 s, reduce the page
   size and inspect its query plan. Raising `CKB_RESQL_TIMEOUT` only hides the problem until
   the next growth step.

## Dead-lettered documents

A document skipped for a fault of its own is **dead-lettered**: listed in
`$CONTENT_WORK_DIR/_dead_letter/<agency_id>.json` and never allowed to fail the
run, so one bad file cannot block the agency's other documents (E9). The run
line's `dead_lettered=` and the report's `dead_lettered_count` give the size;
a WARNING `document <id> dead-lettered: <reason>` is logged when one first
appears, and an INFO line when it leaves.

```json
{
  "agency_id": "…",
  "updated_at": "2026-10-22T09:00:04Z",
  "updated_by_run": "20261022T090000Z-1a2b3c4d",
  "documents": {
    "<source_file.base_id>": {
      "reason": "content_too_large",
      "first_seen_at": "2026-10-01T09:00:02Z",
      "last_seen_at": "2026-10-22T09:00:04Z",
      "last_run_id": "20261022T090000Z-1a2b3c4d",
      "runs": 504
    }
  }
}
```

It is the current set, not a history: a run that observed the whole corpus
rewrites it, a `--limit` run only adds and resolves, and a failed run leaves
it alone. A document leaves when a run publishes it, deletes it or finds it
unchanged. Nothing is lost if the volume is: the next complete run rebuilds it.

**Why it matters beyond the document.** A dead-lettered document is read again
on every run and withholds the corpus watermark, so while any document is
listed **Gate 1 cannot skip a quiet tick** and every run walks every row
(Gate 2 still spares the rest at zero reads). A persistent nonzero
`dead_lettered=` is therefore worth an alert, though not a page.

| Reason | Meaning | What to do |
|---|---|---|
| `content_too_large`, `sidecar_too_large` | Over `MAX_DOCUMENT_BYTES` (20 MiB) or the sidecar limit; refused on `ContentLength`, nothing downloaded | Check the cleaned file in CKB. If it is genuinely that large, raise `MAX_DOCUMENT_BYTES`, within A16's memory product |
| `too_many_chunks` | Cleaned text produces more than `MAX_CHUNKS_PER_DOCUMENT` chunks | Usually a cleaning fault (a page of repeated boilerplate); re-clean it in CKB |
| `content_undecodable` | Cleaned text is not UTF-8 | Re-clean it in CKB |
| `content_missing` | The row names a content object that is not in CKB's bucket | Re-scrape or re-clean the page; the row and the bucket disagree |
| `content_key_invalid`, `metadata_key_invalid` | The URL column is neither a bare key nor `s3://<S3_BUCKET_NAME>/…` | Check `S3_BUCKET_NAME` first, then the row |
| `sidecar_missing`, `sidecar_invalid_json`, `sidecar_undecodable`, `sidecar_not_an_object` | The metadata sidecar is absent or does not parse | Re-clean it in CKB |
| `invalid_source_url`, `uncitable_source_url` | No usable citation, or a presigned or credential-carrying one | Fix the page's URL in CKB; a signed URL is never published |

Each fix in CKB writes a new row version, so the next run reads the document
again and removes it from the record once it publishes. Raising a limit
instead needs no CKB change: the next run reads it again on its own.

## Do not retry immediately after a timeout

Resql does not cancel the query when the client disconnects. A retry
straight after a timeout therefore runs alongside the abandoned query, and
back-to-back calls stack. Before pagination, 7 of 9 back-to-back 50k
`list_documents()` calls hit the 30 s timeout, while single calls on an idle
database took 8.5–9.9 s. At the default page size none of 9 did, but at
20,000 one did, and its abandoned page slowed the next listing. After a
timeout, wait for the next run, or cancel the running export queries first
(step 2 above).

## Changing chunk geometry on the llm-module sink

**What triggers it.** A change to `CHUNK_PROFILE`, or a release that bumps
`CHUNKER_VERSION` or `NORMALISER_VERSION`, changes the chunker fingerprint.
Every document then re-chunks on its next run. On the object-store sink that
is a longer run. On the llm-module sink it is a **full corpus re-embed**: a
bill and a rate-limit event on the llm-module's side, and — because this
pipeline is the only chunker in the retrieval path — a change to the passages
the assistant answers from.

**What stops it from happening by accident (L18).** The llm-module sink
declares `rechunk_requires_ack`. While any committed document is on a
geometry other than the configured one, an export without `--accept-rechunk`
fails before it reads anything or sends anything:

```
content-external run ... outcome=failed error_type=RechunkNotAcknowledged rechunk=refused ...
```

It fails every tick until the re-chunk is done by hand. The HTTP endpoint
cannot pass the flag, so the cron never can. A `--limit` slice re-chunks some
documents and leaves the rest pending, so it does **not** unlock the cron.

**Procedure.**

1. **Announce it and agree a window** with the llm-module owner. State the
   corpus size: every document is re-embedded.
2. **Check the profile** against their embedder's token budget (Open item 1).
   Retrieval quality is measured, not assumed: the cutover comparison (L20)
   is the template.
3. Deploy the configuration change. Expect `rechunk=refused` on the next
   tick — that is the guard working, not an incident.
4. **A slice first:**
   `python -m exporter.run_sync --agency-id … --limit 25 --accept-rechunk`.
   Check the report (`rechunk: accepted`, `publish_failed_count: 0`) and spot
   check a few documents on the far side.
5. **The full run, by hand, at reduced concurrency:** set
   `EXPORT_CONCURRENCY` lower than usual for the run, then
   `python -m exporter.run_sync --agency-id … --accept-rechunk`. Documents a
   committed run (the step-4 slice) already re-chunked are not re-sent. A
   run that fails part way commits nothing, so its retry re-sends what it had
   pushed: harmless (the point ids are the same), but embedded again, so fix
   the cause before retrying.
6. The next cron tick should report `rechunk=-` and `success` or
   `unchanged`. Restore `EXPORT_CONCURRENCY`.

If a document cannot be re-chunked (it fails permanently), it keeps the old
geometry and the cron keeps refusing. Fix or dead-letter it, then repeat
step 5.

## The stale sweep (L10)

**What it is.** `STALE_SWEEP_ENABLED=true` adds one step after a committed
run: the destination deletes this agency's documents that were synced
before the run started **and** are not in the committed manifest. It catches
content no deletion signal names any more — a delete a run made and then
failed to record, or content an earlier deployment left behind.

**Why the keep list.** The exporter never re-sends an unchanged document, so
a live document can carry a `synced_at` from weeks ago. Without the keep list
(every document the committed manifest lists) the sweep would delete it.

**When it runs, and what `stale_sweep=` in the run line means:**

| Value | Meaning | Action |
|---|---|---|
| `disabled` | `STALE_SWEEP_ENABLED` is off | none |
| `refused` | The run cannot vouch for the whole corpus: `--limit`, an abort, a document held back, or an empty manifest | none — the next clean full run sweeps |
| `not_supported` | The sink has no such sweep. Always so on the object-store sink, and on the llm-module sink until it agrees one (Open item 7) | none |
| `failed` | The sweep call failed. **The run's commit stands** | look at the warning line; if it says the far side deleted a kept document, the manifest now claims content the destination lacks — escalate to the llm-module owner |
| `done` | `stale_swept_count` documents were removed | none |

`unchanged` runs never sweep: they classified nothing.

## Index

- **Name**: `idx_source_file_agency_base_updated` on
  `data_collection.source_file (agency_base_id, base_id, updated_at DESC)`.
- **Changeset**: `DSL/Liquibase/changelog/20261020120000-add-source-file-export-index.{sql,xml}`.
  It adds no column.
- **Why `CONCURRENTLY`**: a plain `CREATE INDEX` blocks writes to
  `source_file`, and so the scraper and cleaning, for the whole build.
  `CONCURRENTLY` cannot run inside a transaction, which is why the changeSet
  sets `runInTransaction="false"`.
- **Rollback**: `20261020120000-rollback.sql` runs
  `DROP INDEX CONCURRENTLY IF EXISTS data_collection.idx_source_file_agency_base_updated`.

The document query now matches this index with `agency_base_id = ...`,
`base_id > ...`, `ORDER BY base_id, updated_at DESC` and `LIMIT`. At page
sizes 1,000 and 5,000 the first and middle pages use an ordered Index Scan on it
that stops after one page, with no Sort. At 20,000 the planner switched the
middle page to a Seq Scan and Sort. No index or migration was changed for
pagination.

**What the before-pagination measurement showed about the index:**

- **Watermark `MAX(updated_at)`**: always an Index Only Scan on this index
  (0.1 s at 50k), at every size measured.
- **Latest version per document, for a large agency** (20–55% of the table in
  these runs): the planner chose a Seq Scan and a Sort, not this index. That
  was the right call. Forcing the index made the queries 4–25× slower,
  because every version is fetched from the heap in index order
  (watermark / document query):
  - 10k: 3.7 s / 5.0 s
  - 50k: 67 s / 49 s
- **Small agency** (the 4-document seed agency in the 1.79M-row table): both
  queries took under 0.1 ms. The latest-version part used the existing
  `idx_source_file_agency_excluded_deleted`; the watermark's `MAX` used this
  index.
- **Historical conclusion**: this index served the watermark's `MAX`, but
  did not remove the Sort for an unbounded large-agency document query.
- **With pagination** (1,000 per page): each page reads about 20k buffers
  through this index, and a full 50k listing reads about 1M. That is why the
  total listing is slower than the single Seq Scan, while each call stays short.
