# Content External Service — runbook

Operator notes for running the exporter. Design and configuration are in
[README.md](README.md).

## Export query ceiling (#320)

Enumeration makes two Resql calls per run, both from
`exporter/services/document_source.py` with `RESQL_TIMEOUT_SECONDS = 30`:

- `GET /ckb/agency/get_agency_export_watermark` (one row)
- `GET /ckb/source_file/list_agency_export_documents` (one row per document)

Both take the latest row per `base_id`, so their cost grows with **every
version row** of the agency, not with its document count. `source_file` gets a
new row on every status change.

### Measured (2026-10-20, commit 1c483d3)

| Step | Documents | Version rows (agency) | Table rows (all agencies) | EXPLAIN, document query | EXPLAIN, watermark | Resql, document call | Response | `list_documents()` | Peak RSS |
|---|---|---|---|---|---|---|---|---|---|
| 10k | 10,000 | 204,758 | 1,001,977 | 1.25 s | 0.95 s | 1.50 s | 8.0 MB | 1.6–1.9 s | ~110 MB |
| 50k | 50,000 | 989,753 | 1,786,972 | 8.1 s (8.1–22.0 s over 4 warm runs) | 2.1 s (2.1–8.9 s) | 8.2–9.7 s | 40.2 MB | 8.5–9.9 s | ~310 MB |

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

### Ceiling and conclusion

- **Ceiling**: ≥ 50k documents (about 990k version rows); 200k not measured.
  The budget is 50% of `RESQL_TIMEOUT_SECONDS`, which is 15 s, for the slower
  of the Resql document call and `list_documents()`. At 50k that was 9.9 s at
  most for single calls. This is not an extrapolation beyond 50k.
- **The margin at 50k is thin.**
  - The same query took up to 22 s in the database when the VM's file cache
    was cold.
  - Back-to-back calls took 14.7–18.3 s through Resql.
  - 7 of 9 back-to-back `list_documents()` calls hit the 30 s timeout. A
    timed-out request leaves its query running in Postgres, and Resql
    does not cancel it, so the next call competes with it (see
    [Signs the ceiling is being hit](#signs-the-ceiling-is-being-hit)).
- **Expected production size per agency is unknown.** It has been asked from
  the business side. The keyset pagination decision waits for that answer.
- **Re-check threshold**: at 50k the slowest call was 9.9 s, 66% of the 15 s
  budget. So any agency above about 25,000 documents or about 500k version
  rows must be re-measured before go-live (step 5 below).
- **Total table size matters too.** For a large agency the planner reads the
  whole of `source_file`, so growth in other agencies also slows its export.

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
   `$1::UUID`:

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

   PREPARE docs(varchar) AS
   SELECT DISTINCT ON (base_id)
       base_id, source_base_id, url, page_title, subsector, status,
       cleaned_data_url, cleaned_metadata_url, edited_data_url, edited_metadata_url,
       file_size, is_excluded, is_deleted, updated_at
   FROM data_collection.source_file
   WHERE agency_base_id = $1::UUID
   ORDER BY base_id, updated_at DESC;
   EXPLAIN (ANALYZE, BUFFERS) EXECUTE docs('<agency>');  -- run 3 times
   ```

   Copied from the .sql files at 1c483d3; if they differ, the files win.

3. **Time both calls end to end through Resql**, one call at a time, three
   times each:

   ```bash
   for q in agency/get_agency_export_watermark source_file/list_agency_export_documents; do
     curl -s -o /dev/null --max-time 300 \
       -w "$q %{http_code} %{time_total}s %{size_download}B\n" \
       "http://<resql-ckb>:8090/ckb/$q?agency_base_id=<agency>"
   done
   ```

4. **Compare** against the table above.
   - Is the slowest warm document call (Resql or `list_documents()`) under
     15 s?
   - Does the Sort say `external merge`? That means it spills to disk and
     depends on `work_mem`.
   - How big is the response?

5. **Threshold**: if the slowest call is at or above 15 s, or within 2× of it
   for an agency you expect to grow, add keyset pagination before go-live.
   That is a separate change.

To reproduce the synthetic ladder on a **local, disposable** database only,
use `scripts/measure_export_queries.sql`. It refuses to run without
`-v measure_local_only=yes`, and its header lists the commands. Never run it
against a shared, staging or production database: it inserts millions of rows
into `data_collection.source_file`. Remove the data afterwards with
`-v action=cleanup`.

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
  after more than 30 s, usually waiting on `BufFileRead` (the Sort spilling to
  disk). Abandoned queries keep running, so each run adds load for the next
  one.

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
3. Re-measure as above. If the document call is over 15 s, keyset pagination
   is the fix. Raising `RESQL_TIMEOUT_SECONDS` only hides the problem until
   the next growth step.

## Do not retry immediately after a timeout

Resql does not cancel the query when the client disconnects. A retry
straight after a timeout therefore runs alongside the abandoned query, and
back-to-back calls stack: in the 50k measurement, 7 of 9 back-to-back
`list_documents()` calls hit the 30 s timeout, while single calls on an idle
database took 8.5–9.9 s. After a timeout, wait for the next run, or cancel
the running export queries first (step 2 above).

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

**What the measurement showed about the index:**

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
- **Conclusion**: this index serves the watermark's `MAX`. It does not remove
  the Sort for a large agency.
