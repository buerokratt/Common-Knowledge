# Content External Service — runbook

Operator notes for running the exporter. Design and configuration are in
[README.md](README.md).

## Export query ceiling (#320)

Enumeration makes one watermark call and one document call per page, from
`exporter/services/document_source.py` with `RESQL_TIMEOUT_SECONDS = 30`:

- `GET /ckb/agency/get_agency_export_watermark` (one row)
- `GET /ckb/source_file/list_agency_export_documents` (up to `EXPORT_PAGE_SIZE` documents)

Both take the latest row per `base_id`, so their cost grows with **every
version row** of the agency, not with its document count. `source_file` gets a
new row on every status change. Document pages use an exclusive `base_id` cursor,
starting at `NIL_UUID` from `document_source.py`; a short page ends the listing.
An exact multiple of the page size requires a final empty page. All pages must
succeed before any documents are returned. Each call keeps the 30 s timeout
and the current single-attempt behaviour. Successful enumeration logs the
agency, page count (including an empty terminal page), and total row count.

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

- **Ceiling**: ≥ 50k documents (about 990k version rows) at the default page
  size; 200k not measured. The budget is 15 s per HTTP call, half of
  `RESQL_TIMEOUT_SECONDS`. At 50k the slowest call was the watermark at 4.5 s
  (30%), and the slowest document page took 1.53 s. This is not an
  extrapolation beyond 50k.
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
  listing time both scale with the agency's version rows. Page time does not.
- **Expected production size per agency is unknown.** It has been asked from
  the business side.
- **Re-check threshold**: re-measure before go-live (step 5 below) for any
  agency above 50,000 documents or about 1M version rows, the measured range.
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
   WITH latest_files AS (
       SELECT DISTINCT ON (base_id) base_id, is_deleted
       FROM data_collection.source_file
       WHERE agency_base_id = $1::UUID
       ORDER BY base_id, updated_at DESC
   )
   SELECT MAX(updated_at) AS max_updated_at,
          (SELECT COUNT(*) FROM latest_files WHERE is_deleted = false) AS live_count
   FROM data_collection.source_file
   WHERE agency_base_id = $1::UUID;
   EXPLAIN (ANALYZE, BUFFERS) EXECUTE wm('<agency>');  -- run 3 times

   PREPARE docs(varchar, varchar, varchar) AS
   SELECT DISTINCT ON (base_id)
       base_id, source_base_id, url, page_title, subsector, status,
       cleaned_data_url, cleaned_metadata_url, edited_data_url, edited_metadata_url,
       file_size, is_excluded, is_deleted, updated_at
   FROM data_collection.source_file
   WHERE agency_base_id = $1::UUID AND base_id > $2::UUID
   ORDER BY base_id, updated_at DESC
   LIMIT $3::INTEGER;
   EXPLAIN (ANALYZE, BUFFERS) EXECUTE docs('<agency>', '<cursor>', '<page-size>');
   -- Run 3 times for each cursor / page size, retaining the third plan.
   ```

   If these examples differ from the .sql files, the files win.

3. **Time both calls end to end through Resql**, one call at a time, three
   times each:

   ```bash
   curl -s -o /dev/null --max-time 300 \
     -w 'watermark %{http_code} %{time_total}s %{size_download}B\n' \
     'http://<resql-ckb>:8090/ckb/agency/get_agency_export_watermark?agency_base_id=<agency>'
   curl -s --get --max-time 300 \
     --data-urlencode 'agency_base_id=<agency>' \
     --data-urlencode 'after_base_id=<cursor>' \
     --data-urlencode 'page_size=<page-size>' \
     -o /tmp/export-page.json \
     -w 'documents %{http_code} %{time_total}s %{size_download}B\n' \
     'http://<resql-ckb>:8090/ckb/source_file/list_agency_export_documents'
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
use `scripts/measure_export_queries.sql`. It refuses to run without
`-v measure_local_only=yes`, and its header lists the commands. Never run it
against a shared, staging or production database: it inserts millions of rows
into `data_collection.source_file`. Remove the data afterwards with
`-v action=cleanup`. Reproduce the 50k step with `-v n_docs=50000` and
`-v others_n_docs=10000` after loading the 10k step, always with
`-v measure_local_only=yes`. After cleanup, verify the seed watermark has
`maxUpdatedAt` at 12:00 and `liveCount` 3, and that following the paged endpoint
to a short page yields the seed's four rows.

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
2. Cancel export queries that have run longer than `RESQL_TIMEOUT_SECONDS`
   (30 s). Their exporter request has already timed out. Resql keeps its
   database connection open, so a client that has gone is not visible in
   `pg_stat_activity`; the age of the query is the signal:

   ```sql
   SELECT pid, now() - query_start AS age, pg_cancel_backend(pid)
   FROM pg_stat_activity
   WHERE state = 'active'
     AND (query LIKE '%retain removal and hold signals%'
          OR query LIKE '%advance the export watermark%')
     AND now() - query_start > interval '30 seconds'
     AND pid <> pg_backend_pid();
   ```

   Resql sends each query with its header comment, and the two patterns are
   the `description` lines of `list_agency_export_documents.sql` and
   `get_agency_export_watermark.sql`. No other file in `DSL/Resql` contains
   them. The file names themselves do not appear in the query text. If a
   description changes, update the pattern.
3. Re-measure as above. If a document page is over 15 s, reduce the page
   size and inspect its query plan. Raising `RESQL_TIMEOUT_SECONDS` only hides the problem until
   the next growth step.

## Do not retry immediately after a timeout

Resql does not cancel the query when the client disconnects. A retry
straight after a timeout therefore runs alongside the abandoned query, and
back-to-back calls stack. Before pagination, 7 of 9 back-to-back 50k
`list_documents()` calls hit the 30 s timeout, while single calls on an idle
database took 8.5–9.9 s. At the default page size none of 9 did, but at
20,000 one did, and its abandoned page slowed the next listing. After a
timeout, wait for the next run, or cancel the running export queries first
(step 2 above).

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
