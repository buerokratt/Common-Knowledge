# Content External Service — runbook

Operator notes for running the exporter. Design and configuration are in
[README.md](README.md).

## Operating the export

For a manual export, use [Running an export](README.md#running-an-export).
The hourly export is this service's own timer, not a CronManager job
([Schedule](README.md#schedule)): it runs only while the chatbot's LLM module
toggle is OFF and nothing fires while it is ON. It reads the toggle itself
every `EXPORT_SCHEDULE_POLL_SECONDS` (default 300), so a switch takes effect
within that; nothing on the chatbot side tells it. Its state is `/health`'s
`export_schedule`. To run one export through the gate now, exactly as a tick
does, call CKB's flow; to make the timer re-read the toggle without waiting
for the next poll, call the sync flow, which starts or stops it:

```bash
curl -fsS '<ruuter-internal>/ckb/pipeline/content-export'
curl -fsS '<ruuter-internal>/ckb/pipeline/content-export-sync'
curl -fsS '<content-external>/health'   # export_schedule: state, last_tick_result, last_poll_result
```

Ruuter wraps each answer as `{"response": …}`. The drain and reconcile are
still CronManager jobs: `POST /execute/scheduler/<job>` runs one by hand. On
some CronManager versions a repeated `POST /execute` returns 500
(`ClassCastException`) although the job ran; check `/health` rather than
curl's exit code, and do not retry on the 500 alone. CronManager names the
job group after the job file, so `scheduler.yml` gives the group
`scheduler`. In HTTP commands here, `<cron-manager>`, `<ruuter-internal>`
and `<content-external>` stand for the configured base URLs.
Submission is not completion. Read the summary in `/health`'s `last_run`,
the `content-external run` log line, or the full CLI report from
`--output-json`; see [Observability](README.md#observability--how-to-tell-this-is-working).
A run refused before the export starts (a configuration, Vault or
store-build error) writes no `--output-json` report and exits `2` for a
`ConfigurationError`, `1` otherwise. Its type is in `/health`'s
`last_run.error_type` and the `content-external run` line; its cause is only
in the `content-external error` log line.

| Outcome | What to do |
|---|---|
| `success` | The run completed. Check skipped documents and per-document publish failures before treating every document as published. |
| `unchanged` | The committed manifest still matches; nothing needed publishing. Wait for the next tick. |
| `busy` | Another run holds the lock. This invocation did no export work; let the active run finish and watch `consecutive_busy`. |
| `failed` | Read `error_type` and the error log, then check credentials, connectivity and the destination before retrying. |

A call to CKB's `pipeline/content-export` (a tick, or by hand), or a drain or
reconcile job, can also answer `{"status":"skipped",…}` without reaching this
service. That is the
[LLM module toggle gate](README.md#the-chatbots-llm-module-toggle-gates-all-three-jobs),
and `/health` does not move. Act on the `reason`:

| `reason` | What to do |
|---|---|
| `llm_module_active` | Nothing. The chatbot uses the LLM module; flip the toggle OFF in Chatbot settings to resume exporting, which the next poll picks up. A tick or poll that meets this stops the export timer. |
| `chatbot_domain_not_configured` | content-external logs one WARNING, `no export will run: BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID is empty`. Set `BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID` in `constants.ini` to the chatbot widget domain UUID and restart ruuter-internal (Helm: redeploy with `--set ruuterInternal.chatbotWidgetDomainId=<uuid>`, which restarts it). The next poll re-reads it; call `pipeline/content-export-sync` to do so at once. |
| `chatbot_domain_unknown` | content-external logs one WARNING naming the value: `BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID=<value> is not an active chatbot widget domain`. The UUID is not an active row in the chatbot's `widget_domains`. Check it for typos and confirm the domain is active. |
| `toggle_unreadable` | `resql-users` failed or is down. Check its log and its connection to the chatbot's `byk` database. |

The CLI (`run_sync`, `run_ops`) ignores the gate. Check the toggle before
exporting by hand while it is ON.

The destination holding fewer documents than CKB's GUI is expected: only
`status = 'finished'` rows are published. Confirm the difference in the
report's skipped count and its reasons, rather than comparing totals alone.

This deployment is configured for `CONTENT_SINK=object_store`. Confirm the
running value using [Which sink is this deployment running?](README.md#which-sink-is-this-deployment-running).
On a non-listable sink such as `llm_module`, reconcile can see only what
read-back returns, so its best result is `clean_partial`, never `clean`; with
no read-back it reports `unverifiable`; see
[Sink parity](README.md#sink-parity--what-each-sink-can-and-cant-do). A failed run may mean the destination is down rather
than that CKB is wrong.

If `duration_seconds` routinely approaches the schedule interval, or
`consecutive_busy` keeps rising, **lengthen the interval** using the
[Schedule](README.md#schedule). Use the existing
[alert thresholds](README.md#the-alert-thresholds-to-agree-with-ops).

## First deployment (bootstrap)

Run the CLI commands in the exporter container with its configured
environment. Replace `<agency_base_id>` with the agency's base id. Flag
semantics and first-run safeguards are in
[Running an export](README.md#running-an-export).

1. Keep `BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID` empty in [constants.ini](../constants.ini)
   (Helm: leave `ruuterInternal.chatbotWidgetDomainId` unset). The gate then skips with `chatbot_domain_not_configured`, so the export
   timer settles `inactive` and nothing fires, whatever the chatbot's toggle
   says. `/health`'s `export_schedule.state` shows it.
2. Confirm the exporter container is up, the destination is accessible, and
   both sets of credentials work: CKB's `S3_*` / `AWS_*` read keys, and the
   destination's credentials — the `EXTERNAL_S3_*` keys for `s3`, or for
   `azure_blob` the Vault secret in
   [Azure Blob and Vault](#azure-blob-and-vault) plus a container that already
   exists. See
   [Environment variables](README.md#environment-variables) and
   [Reading CKB's bucket](README.md#reading-ckbs-bucket). Check the service:

   ```bash
   curl -fsS '<content-external>/health'
   ```

   A health response confirms the listener, not bucket access; verify the
   credentials separately before starting the sample.
3. Run a limited sample first:

   ```bash
   python -m exporter.run_sync --agency-id '<agency_base_id>' --limit 25 --output-json -
   ```

   Check the stored chunk shape, recompute a few chunk ids locally with
   `make_chunk_id(agency_id, document_id, ordinal)` from
   [ids.py](exporter/core/ids.py), and read one `metadata.json`.
4. Run the full export by hand and retain the JSON report:

   ```bash
   python -m exporter.run_sync --agency-id '<agency_base_id>' --output-json '<report-path>'
   ```

5. Read the report: new and skipped document counts, skip reasons, durations
   and failures. Resolve failures before arming the schedule (step 7).
6. Spot-check the destination against the committed manifest: sampled
   document ids, metadata and chunk counts should agree.
7. Set `BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID` in `constants.ini` to the chatbot widget
   domain whose **LLM module active** toggle governs this deployment, and
   restart ruuter-internal (Helm: redeploy with
   `--set ruuterInternal.chatbotWidgetDomainId=<uuid>`, which restarts it)
   ([toggle gate](README.md#the-chatbots-llm-module-toggle-gates-all-three-jobs)).
   Nothing is set on the chatbot side. Then call
   `curl -fsS '<ruuter-internal>/ckb/pipeline/content-export-sync'`, or wait
   for the next poll: with the toggle OFF the timer starts, exports at once
   and hourly after; with it ON it stays stopped. From then on the poll
   starts or stops it within `EXPORT_SCHEDULE_POLL_SECONDS` of a switch.
8. Set `DIFF_ON_MISSING_MANIFEST=fail` in the service's configuration; see
   [Environment variables](README.md#environment-variables).

A large skipped count on a fresh CKB is expected: most documents may still
be scraping or cleaning, or waiting in `in_review`. Later ticks pick them
up once they are finished.

## Azure Blob and Vault

Azure is selected per deployment: on Compose with
`CONTENT_EXTERNAL_STORE_BACKEND=azure_blob`, `AZURE_STORAGE_CONTAINER` and
`COMPOSE_PROFILES=azure-blob` in the root `.env`; on Helm with
`contentExternal.storeBackend=azure_blob`, `contentExternal.azure.container`
and `contentExternal.vaultAgent.enabled=true` (never put those variable names
in `env` or `extraEnv`; the chart refuses them). Step by step:
[docs/staging-deployment.md](docs/staging-deployment.md).

Every run then reads its credential from the Vault KV v2 secret at
`VAULT_SECRET_PATH` (`blob/connections/azure_blob/content`; on Helm,
`contentExternal.azure.vaultSecretPath`). `vault/init.sh` seeds it with
`REPLACE_ME` placeholders, and a run refuses them. Replace the secret with
`vault kv put`, not `kv patch` (a patch keeps the placeholder `account_key`
beside the new form, and the secret is refused for holding two), with
`account_url`
(`https://<account>.blob.core.windows.net`, no query string) plus exactly one
of:

- `account_key`
- `sas_token`
- `tenant_id`, `client_id` and `client_secret` (a service principal)

The secret is read on every run, so a rotated value is used by the next run
without a restart.

A failed run names the cause in `error_type` in `/health`'s `last_run` and in
the `content-external run` log line:

| `error_type` | Cause | What to do |
|---|---|---|
| `ConfigurationError` | The secret still holds a placeholder, `account_url` is missing or has a query string, it does not hold exactly one credential form, Vault answered 403/404, `AZURE_STORAGE_CONTAINER` or `VAULT_SECRET_PATH` is empty, or the agent token file is missing or empty. On Compose a 403 also follows an expired AppRole `secret_id` (24h TTL), and a missing token follows an unset `azure-blob` profile | Read the `content-external error` line for which one. Fix the secret, the setting or the `content-external-policy` grant. On Compose, start the `azure-blob` profile, or re-run `vault-init` (`docker compose up --force-recreate --no-deps vault-init`) to mint a new `secret_id`, at least daily. The next tick fails the same way until then |
| `StoreError`, retryable | Vault unreachable, timed out, 429 or 5xx | Nothing; the next tick retries |
| `StoreError`, permanent | Any other Vault 4xx, or a response that is not a KV v2 secret | Check `CKB_VAULT` (in `constants.ini`) and `VAULT_SECRET_PATH` (on Helm, `contentExternal.azure.vaultSecretPath`; the sidecar agent's own address is `contentExternal.vaultAgent.vaultAddr`, which should name the same Vault) |

The image runs as root, which is the only reason it can read the agent's
token file (owned by `vault:vault`, mode 0640). Adding a `USER` to the
Dockerfile needs the token file's mode or group set deliberately in the
agent's sink, or every Azure run fails reading the token.

## Export query ceiling (#320)

Enumeration makes one watermark call and one document call per page, from
`exporter/services/document_source.py`, through CKB's Ruuter-internal at
`CKB_RUUTER_INTERNAL` (from `constants.ini`). No service calls Resql
directly; each flow wraps one Resql query:

- `GET $CKB_RUUTER_INTERNAL/content-external/export-watermark?agencyBaseId=…`
  → `agency/get_agency_export_watermark` (one row)
- `GET $CKB_RUUTER_INTERNAL/content-external/export-documents?agencyBaseId=…&afterBaseId=…&pageSize=…`
  → `source_file/list_agency_export_documents` (up to `EXPORT_PAGE_SIZE` documents)

**Two timeouts, in order.** Each flow's Resql step has `timeout: 25000` (ms);
the exporter's own read timeout, `CKB_QUERY_TIMEOUT`, is 35 s, so Ruuter always
answers first. A slow query therefore surfaces as **HTTP 500 from Ruuter**,
not as an exporter `ReadTimeout`. Startup refuses `CKB_QUERY_TIMEOUT` below
30 s (`MIN_QUERY_TIMEOUT_SECONDS` in `exporter/services/ckb_ruuter.py`), and
`tests/test_content_external_flows.py` fails if the flows' `timeout:` and
`EXPORT_FLOW_TIMEOUT_SECONDS` there drift apart.

**Ruuter limits.** Ruuter buffers only 256 KB of a response by default; the
document flow sets `limit: 8192` (KB) because a 1,000-row page is about
0.81 MB. A page over the limit fails every run with HTTP 500
(`DataBufferLimitException` in ruuter-internal's log; verified locally
2026-11-28), so startup refuses `EXPORT_PAGE_SIZE` above 5,000
(`MAX_EXPORT_PAGE_SIZE`, ~4 MB, half the limit). To go higher, raise the
flow's `limit:`, `DOCUMENTS_FLOW_LIMIT_KB` and `MAX_EXPORT_PAGE_SIZE` together. CKB's `httpCodesAllowList` passes a Resql 400/500
through as HTTP 200, so each flow checks Resql's status itself and answers
502 instead; a missing param answers 400.

Both take the latest row per `base_id`, so their cost grows with **every
version row** of the agency, not with its document count. `source_file` gets a
new row on every status change. Document pages use an exclusive `base_id` cursor,
starting at `NIL_UUID` from `document_source.py`; a short page ends the listing.
An exact multiple of the page size requires a final empty page. All pages must
succeed before any documents are returned. Connection errors, 408/429 and
502/503/504 are retried under F8 (up to 4 attempts, full jitter, `Retry-After`
honoured), each logged as `CKB read <path>: attempt N of 4 failed`. **Neither a
read timeout nor a Ruuter 500 is retried** — a 500 is how a flow reports its
Resql step timing out — for the reason in
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

`EXPORT_PAGE_SIZE` defaults to **1,000**, from 1 to 5,000 (the ceiling is the
export-documents flow's response limit, above). It is the only
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
  measured. The budget is 15 s per HTTP call, well under the flows' 25 s
  Resql step timeout (the measurements below called Resql directly with the
  then 30 s exporter timeout; going through Ruuter-internal adds its JSON
  handling to each page). On the 2026-10-20/21 machine the slowest call at
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
           cleaned_data_url, cleaned_metadata_url,
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

3. **Time both calls end to end through Ruuter-internal**, the path the
   exporter uses, one call at a time, three times each (`CKB_RUUTER_INTERNAL`
   from `constants.ini`, e.g. `http://ruuter-internal:8089/ckb`, run from a
   container on `bykstack`):

   ```bash
   curl -s --get -o /dev/null --max-time 300 \
     --data-urlencode 'agencyBaseId=<agency>' \
     -w 'watermark %{http_code} %{time_total}s %{size_download}B\n' \
     "$CKB_RUUTER_INTERNAL/content-external/export-watermark"
   curl -s --get --max-time 300 \
     --data-urlencode 'agencyBaseId=<agency>' \
     --data-urlencode 'afterBaseId=<cursor>' \
     --data-urlencode 'pageSize=<page-size>' \
     -o <output-file> \
     -w 'documents %{http_code} %{time_total}s %{size_download}B\n' \
     "$CKB_RUUTER_INTERNAL/content-external/export-documents"
   ```

   A 500 here means Resql was unreachable, the step hit its 25 s timeout, or
   the page outgrew the flow's `limit:` — ruuter-internal's log tells them
   apart (`DataBufferLimitException` for the last). To separate Ruuter's cost
   from the query's, compare with the EXPLAIN ANALYZE timings from step 2.

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

## A run fails with HTTP 500 from a CKB read

The run line shows `error_type=AgencyCountUnavailableError` or
`DocumentSourceError`, and the error line reads, for example:

```
agency count: Ruuter-internal read failed (HTTP 500: Resql down or timed out,
page over the flow's limit, or content-external flows not deployed; see
ruuter-internal's log)
```

Ruuter answers every one of these with a bare 500, so the exporter cannot tell
them apart; ruuter-internal's log can. Find the request for the
`content-external/*` flow at that time:

| ruuter-internal's log shows | Cause | What to do |
|---|---|---|
| Nothing at all for the request — no `Request received for DSL: content-external/…` line (observed locally 2026-11-28: an undeployed flow answers 500 and logs nothing) | **The flows are not deployed.** In Kubernetes ruuter-internal clones its DSL from `buerokratt/Common-Knowledge`; an exporter image deployed before the flows are in what it clones fails every run | Merge `DSL/Ruuter.internal/ckb/GET/content-external/` into what ruuter-internal clones, then restart ruuter-internal (it reads DSL only at startup) |
| The Resql step failing with a connection error | **Resql was down or restarting** | Nothing, once Resql is back. This is **expected**: a Ruuter 500 is never retried (it may be a query still running), so a Resql restart fails that run, and the next tick recovers it |
| The Resql step failing with a timeout after 25 s | **A query is too slow** | See [Signs the ceiling is being hit](#signs-the-ceiling-is-being-hit) |
| `DataBufferLimitException` | **A page outgrew the flow's `limit:`** | Should not happen within `EXPORT_PAGE_SIZE`'s 5,000 ceiling; check for unusually long rows, and lower `EXPORT_PAGE_SIZE` |

That 500 is how a **locally built** Ruuter (Compose) answers a flow that does
not exist. The **published** image the chart runs
(`ghcr.io/buerokratt/ruuter:v2.2.1`) answers it 300 with an empty body instead
(seen in minikube, 2026-11-28); neither ever answers 404. The exporter fails
the run on the 300 at once, never retrying it, and says `HTTP 300: no such
flow: are the content-external flows deployed in ruuter-internal?`: the first
row's cause. The export timer's warning for `pipeline/content-export-status`
names the same 300 or 500 the same way.

## Signs the ceiling is being hit

**What the operator sees:**

- **Exporter**: every run fails during enumeration with
  `DocumentSourceError: CKB read /content-external/export-documents:
  Ruuter-internal read failed (HTTP 500: Resql down or timed out, …)` (or
  `…/export-watermark`). It fails the same way on every run, and is not
  retried within the run.
- **ruuter-internal log**: the flow's Resql step failing with a WebClient
  timeout after 25 s.
- **resql-ckb log**: `AsyncRequestNotUsableException … Broken pipe` for
  `/ckb/source_file/list_agency_export_documents`. Resql finished after Ruuter
  had already disconnected.
- **Postgres**: `pg_stat_activity` shows the export query still `active`
  after more than 25 s. The watermark waits on `BufFileRead` while its Sort
  spills to disk. A document page at the default size does not sort, so a
  slow page is reading the heap. Abandoned queries keep running,
  so each run adds load for the next one.

**What to do:**

1. Find the agency's version-row count (step 1 above) and compare it with the
   table.
2. Cancel export queries that have run longer than the flows' 25 s step
   timeout. Their request has already timed out in Ruuter. Resql keeps its
   database connection open, so a client that has gone is not visible in
   `pg_stat_activity`; the age of the query is the signal:

   ```sql
   SELECT pid, now() - query_start AS age, pg_cancel_backend(pid)
   FROM pg_stat_activity
   WHERE state = 'active'
     AND (query LIKE '%retain removal and hold signals%'
          OR query LIKE '%advance the export watermark%')
     AND now() - query_start > interval '25 seconds'
     AND pid <> pg_backend_pid();
   ```

   Resql sends each query with its header comment, and the two patterns are
   the `description` lines of `list_agency_export_documents.sql` and
   `get_agency_export_watermark.sql`. No other file in `DSL/Resql` contains
   them. The file names themselves do not appear in the query text. If a
   description changes, update the pattern.
3. Re-measure as above. If a document page is over 15 s, reduce the page
   size and inspect its query plan. Raising the flows' `timeout:` (and
   `CKB_QUERY_TIMEOUT` above it) only hides the problem until the next growth
   step.

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

Neither Ruuter nor Resql cancels the query when the client disconnects, so a
Ruuter 500 (its step timed out) leaves the query running just as an exporter
timeout would. A retry
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
3. Deploy the configuration change, with `LLM_MODULE_CHUNK_PROFILE_CONFIRMED`
   set to the new `CHUNK_PROFILE` (without it, startup refuses). Expect
   `rechunk=refused` on the next tick — that is the guard working, not an
   incident.
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
| `not_run` | It is on, but the run ended `unchanged`, `failed` or `busy`, so it never reached the sweep step, which only follows a commit | none, unless `failed` repeats |
| `refused` | The run cannot vouch for the whole corpus: `--limit`, an abort, a document held back, or an empty manifest | none — the next clean full run sweeps |
| `not_supported` | The sink has no such sweep. Always so on the object-store sink, whose sweep is J5's tail rule. The llm-module sink has one under the agreed contract (§13 q12), so this appears there only if that agreement is withdrawn | none |
| `failed` | The sweep call failed. **The run's commit stands** | look at the warning line; if it says the far side deleted a kept document, the manifest now claims content the destination lacks — escalate to the llm-module owner |
| `done` | `stale_swept_count` documents were removed | none |

`unchanged` runs never sweep: they classified nothing.

The last run's value is also on `GET /health`, as `last_run.stale_sweep` and
`last_run.stale_swept_count`, next to `last_run.rechunk` (L18:
`refused` there means every tick is refusing until the re-chunk is run by
hand, as described above). A `busy` tick or a failure before the run started
keeps a `refused` value. Only a run that completes clears it.

## Draining deferred deletes (J1, J2)

**What it is.** When the destination refuses a delete, the export journals it
under `$CONTENT_WORK_DIR/_deferred/<run_id>.jsonl` and commits anyway. The
`deletions_unresolved` count in the report says how many. The drain retries
those deletes.

**First, one full cycle dry.** `DRAIN_DELETES_ENABLED` is off by default, so
`POST /drain_deletions` only reports. By hand:

```bash
python -m exporter.run_ops drain --agency-id <agency> --dry-run
```

Read `candidate_ids` in the report against CKB: each one should be a
document that is really deleted, excluded or `not_found`. Then set
`DRAIN_DELETES_ENABLED=true`, or drain by hand without `--dry-run`.

`candidate_ids` is in the CLI's JSON report only. A dry drain from the cron
or `POST /drain_deletions` logs and records counts, never ids (F13), so
reading a cron dry cycle means either running the CLI dry run above or
reading the journals themselves under `$CONTENT_WORK_DIR/_deferred/`.

**The schedule.** `trigger_content_drain` ships disabled. Enabling it while
`DRAIN_DELETES_ENABLED` is off is how to run the dry cycle on the cron:
every tick reports and none deletes. Enable it with the one-line change in
[Schedule](README.md#schedule), or invoke it by hand:

```bash
curl -fsS -X POST '<cron-manager>/execute/scheduler/trigger_content_drain'
```

**Reading the report** (`content-external drain` line, or `last_drain` on
`/health`):

| Field | Meaning | Action |
|---|---|---|
| `outcome=nothing_pending` | Every journalled delete has landed | none |
| `outcome=drained`, `still_unresolved=0` | The retries landed | none |
| `still_unresolved` > 0 | The destination still refuses | the next drain retries; if it persists, check the destination's credentials and permissions |
| `skipped_republished` | Restored and re-published since the delete was journalled | none: deleting it would remove live content |
| `awaiting_export` | A journal from an export that died before its commit | none: the next export settles it |
| `tails_left_to_export` | A tail delete the destination refused | none: the next export retries it |
| `tails_left_to_reconcile` | An orphan-tail delete from reconcile's repair the destination refused | no export retries these: run `run_ops reconcile --repair`, which finds them in its listing again |
| `outcome=failed`, `NoManifestToDrainError` | A pending delete, and no committed manifest to re-check it against | the drain will not delete blind; investigate the manifest store. With nothing pending the drain never reads the manifest, so a deployment whose first export has not committed reports `nothing_pending` |
| `outcome=busy` | An export or another operation held the lock | none |

## Reconciling the destination (J4, J5, J9)

**What it is.** A read of what the destination holds, compared with the
committed manifest. `trigger_content_reconcile` runs it daily at 03:35
(J6) once enabled; it ships disabled, and the one-line change is in
[Schedule](README.md#schedule). Until then run it by hand, through the cron
job or the CLI:

```bash
curl -fsS -X POST '<cron-manager>/execute/scheduler/trigger_content_reconcile'
python -m exporter.run_ops reconcile --agency-id <agency>
```

**`scope` is a property of the sink, not a fault.**

- `full`: the object-store sink, which lists everything.
- `manifest_documents`: the llm-module sink with read-back. It sees only
  what the manifest lists, so no drift is `clean_partial`, never `clean`:
  anything else at the destination was not looked at. Alert on
  `outcome=clean` and this scope will never satisfy it; alert on
  `outcome=drift` if `clean_partial` is the best this sink can do.
- `none`: the outcome is `unverifiable`. Nothing was compared, so it is
  never reported as `clean`.

**Cost.** Full scope lists the agency's prefix and reads each document's
record; read-back scope makes one request per manifest document, in
sequence. Both grow with the corpus, and reconcile holds the run lock
throughout: one still running at 03:47 makes that drain report `busy`
(harmless; the next hour retries), and an export tick that fires while it
runs reports `busy` too. The export timer's minute is wherever the toggle was
last switched OFF or the service last started, so it can fall inside the
reconcile. Watch `duration_seconds` in `last_reconcile` and `consecutive_busy`
in `last_run`; if exports keep meeting it, move `trigger_content_reconcile`.

**Drift categories, and what to do:**

| Field | Meaning | Action |
|---|---|---|
| `missing_documents` | In the manifest, absent from the destination | the manifest believes it is published, so no export, not even `--force`, re-sends it until its row moves (Gate 2). Re-clean the document in CKB. If many are missing, re-bootstrap. Reconcile does not repair this category |
| `record_missing` | Chunks with no record (an F3 breach) | escalate: the publish order was violated |
| `chunk_gaps` | An ordinal below the manifest's count is absent | as `missing_documents` |
| `orphan_tail_documents` / `orphan_tail_chunks` | Chunks at or past the count | `--repair`, or `RECONCILE_REPAIR_ENABLED=true` |
| `unrecorded_documents` | At the destination, not in the manifest (`full` scope only) | usually a run that died before its commit; the next export settles it. If it persists, investigate before deleting anything by hand |

**Repair** removes orphaned tails only, at `full` scope, and only where the
document's record states its own chunk count. The tail starts at the larger
of that count and the manifest's. Each delete is journalled first, and the
journal is closed and mirrored, so the next export does not republish the
repaired documents. `outcome=repaired` means nothing else drifted. Run one
cycle report-only before setting `RECONCILE_REPAIR_ENABLED=true`.
`tails_unresolved` > 0 means the destination refused some deletes. No export
retries those (no manifest counted them); the next repair finds them in its
listing and tries again, and until then the drain reports them as
`tails_left_to_reconcile`.

## Purging an agency (J7, J8)

**A purge deletes everything the committed manifest records for the agency
at the destination.** It is never scheduled and has no HTTP route.

1. **Disable the export's schedule** (Stage G's CronManager entry). An export
   after a purge republishes every finished document CKB still holds.
2. Confirm nothing is running: `/health` shows the last run finished.
3. Purge, repeating the agency id:

   ```bash
   python -m exporter.run_ops purge --agency-id <agency> --confirm <agency>
   ```

4. Read the report:
   - `outcome=purged`: every requested delete landed and the destination
     was listed and found empty (`residual=verified_empty`). Done.
   - `outcome=purged_unverified`: every requested delete was acknowledged,
     but the sink cannot list (or the check failed), so `residual` is
     `unverifiable` and `requested` against `deleted` + `missing` is all
     there is. Confirm with the destination's owner.
   - `outcome=incomplete`: `unresolved_ids` stay journalled. Run
     `run_ops drain` until `still_unresolved=0`.

   The CLI exits `0` for `purged`, `purged_unverified` and
   `nothing_to_purge`, `1` for `incomplete`, `busy` and `failed`, and `2` for
   a usage or configuration error that stopped it before it started (a
   `--confirm` mismatch included), so a script never carries on past a purge
   that did not finish.
5. The manifest is kept, with no documents and no watermark. Re-enabling the
   export later republishes the agency in full.

## llm-module cutover (L19–L25)

The full plan, with commands and sign-off records, is
[docs/llm-module-cutover.md](docs/llm-module-cutover.md). What an operator
needs on the day:

**The order is fixed (L21).**

1. Bootstrap beside, into a new collection.
2. Incremental runs keep it current.
3. Retrieval comparison, recorded with a decision.
4. Swap the alias.
5. Settling period.
6. **Only then** the llm-module removes its chunker.
7. Drop the old collection, by its named owner.

Removing the chunker earlier deletes the rollback and the comparison
together.

**The exporter writes to the physical collection; the swap moves the
alias.** `LLM_MODULE_COLLECTION` is never the alias that retrieval reads
through. Put the alias in `LLM_MODULE_PROTECTED_COLLECTIONS` so startup
refuses it. Neither the swap nor the rollback needs any change to the
exporter. It keeps writing to the new collection throughout, and that is
harmless. `GET /health` shows which collection it is: `sink_id` is
`llm_module:<collection>`, the same value as `sink=` on every run and error
line.

**A `failed` run with `error_type=SinkMismatch` after changing
`LLM_MODULE_COLLECTION`** means the manifest was written for another
collection. That is the guard working, not an incident: the collection is
part of the manifest's sink_id, so the old manifest cannot claim documents
are already published in the new collection. If the change was deliberate
(a re-bootstrap), point `MANIFEST_STORE_PREFIX` at a fresh prefix and
bootstrap as in the plan's §2 and §3. **Keep the old manifest. Do not delete
it to make the error go away.** Before you re-point, check that the last run
against the old collection ended `success` or `unchanged`. The work
directory is shared, and the new collection's first commit would close any
deletion journal the old collection left open. If the change was not
deliberate, restore the previous value.

**The old collection is never removed by this service.** Neither
`run_ops purge` nor the stale sweep can see it, and startup refuses
`contextual_chunks_azure`, `contextual_chunks_aws` and anything in
`LLM_MODULE_PROTECTED_COLLECTIONS` as a target.

**The hourly ZIP job stays (L24).** The llm-module stops pulling
`zipped/{agency}.zip`, but the Global Classifier still consumes it. Do not
disable, re-schedule or remove `trigger_zipping`. Without it,
`agency.data_hash` freezes and the Classifier goes stale with no error.
`tests/test_classifier_safety.py` fails if the job is removed. Re-run
Verification 14–17 after the swap.

**Metadata merges are off until you switch them on (L25).** Leave
`LLM_MODULE_ENABLE_METADATA_MERGE` unset until the llm-module owner confirms
that merges are implemented all-or-nothing. Before you set it, check on
staging that a metadata-only edit, such as a page title, reaches the
collection. A far side that acknowledged a merge without applying it would
leave stale metadata that the exporter never retries. While the flag is off,
every `metadata_changed` document is re-pushed in full.

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
- **Check after the migration**: a failed `CONCURRENTLY` build leaves an
  INVALID index behind, and a rerun's `IF NOT EXISTS` skips it with only a
  notice, so Liquibase reports success either way. Confirm it is valid:

  ```sql
  SELECT indexrelid::regclass, indisvalid
  FROM pg_index
  WHERE indexrelid = 'data_collection.idx_source_file_agency_base_updated'::regclass;
  ```

  If `indisvalid` is `f`, drop it, clear its changelog row and rerun the
  migration:

  ```sql
  DROP INDEX CONCURRENTLY data_collection.idx_source_file_agency_base_updated;
  DELETE FROM databasechangelog
  WHERE id = '20261020120000' AND author = 'KoortHendrik';
  ```

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
