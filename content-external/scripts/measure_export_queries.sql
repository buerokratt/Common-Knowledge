-- Synthetic source_file history for measuring the two Stage E export queries
-- (#320). LOCAL DATABASES ONLY: it inserts millions of rows straight into
-- data_collection.source_file. Never run it against a shared, staging or
-- production database. See content-external/RUNBOOK.md.
--
-- Load (idempotent per step; documents 1..n_docs that already exist are kept):
--   docker exec -i database psql -U byk -d ckb_db \
--     -v measure_local_only=yes -v n_docs=10000 [-v others_n_docs=10000] \
--     -f - < content-external/scripts/measure_export_queries.sql
-- Remove every generated row and reclaim the space:
--   ... -v measure_local_only=yes -v action=cleanup -f - < ...
--
-- Five agencies: cafe0000-0000-4000-8000-000000000001 is the measured one,
-- ...0002 to ...0005 make the index discriminate by agency. Every value is a
-- hash of the document, so a re-run produces the same data.

\set ON_ERROR_STOP on

\if :{?measure_local_only}
SELECT :'measure_local_only' = 'yes' AS measure_ok \gset
\else
\set measure_ok false
\endif
\if :measure_ok
\else
DO $$ BEGIN RAISE EXCEPTION
  'refusing to run: pass -v measure_local_only=yes, and only against a local database';
END $$;
\endif

\if :{?action}
\else
\set action load
\endif
SELECT :'action' = 'cleanup' AS is_cleanup \gset

\if :is_cleanup
DELETE FROM data_collection.source_file
WHERE agency_base_id IN (
    'cafe0000-0000-4000-8000-000000000001', 'cafe0000-0000-4000-8000-000000000002',
    'cafe0000-0000-4000-8000-000000000003', 'cafe0000-0000-4000-8000-000000000004',
    'cafe0000-0000-4000-8000-000000000005'
);
VACUUM (FULL, ANALYZE) data_collection.source_file;
\else

\if :{?n_docs}
\else
DO $$ BEGIN RAISE EXCEPTION 'pass -v n_docs=<documents for the measured agency>'; END $$;
\endif
\if :{?others_n_docs}
\else
\set others_n_docs :n_docs
\endif

CREATE TEMP TABLE measure_agencies AS
SELECT ('cafe0000-0000-4000-8000-00000000000' || ord)::uuid AS agency_base_id,
       ord,
       CASE WHEN ord = 1 THEN :n_docs ELSE :others_n_docs END AS n_docs
FROM generate_series(1, 5) AS ord;

CREATE TEMP TABLE measure_docs AS
SELECT a.agency_base_id,
       a.ord,
       d AS doc_no,
       md5(a.agency_base_id::text || ':' || d)::uuid AS base_id
FROM measure_agencies AS a
CROSS JOIN LATERAL generate_series(
    (SELECT count(DISTINCT s.base_id) + 1
     FROM data_collection.source_file AS s
     WHERE s.agency_base_id = a.agency_base_id),
    a.n_docs
) AS d;

-- Version depth: 90% of documents have 5-15 versions, a 10% tail of frequently
-- refreshed pages has 16-200 (mean about 20). Each history spans about a year.
-- Rows are inserted in updated_at order, so versions of different documents and
-- agencies interleave on the heap as they would after a year of scraping.
INSERT INTO data_collection.source_file (
    base_id, source_base_id, agency_base_id, url, page_title,
    original_data_url, cleaned_data_url, edited_data_url,
    original_metadata_url, cleaned_metadata_url, edited_metadata_url,
    original_data_hash, created_at, updated_at, last_scraped_at,
    originally_scraped, status, type, subsector, is_excluded, is_deleted,
    file_size
)
SELECT
    d.base_id,
    md5(d.agency_base_id::text || ':source:' || d.doc_no % 25)::uuid,
    d.agency_base_id,
    format('https://www.asutus%s.ee/et/teenused-ja-toetused/%s/%s-%s',
           d.ord, lpad(d.doc_no::text, 7, '0'),
           md5(d.base_id::text), md5(d.base_id::text || 's')),
    format('Teenuse kirjeldus %s – %s', d.doc_no, left(md5(d.base_id::text || 't'), 30)),
    p.prefix || '/original.html',
    CASE WHEN v.v >= 3 THEN p.prefix || '/cleaned.txt' END,
    CASE WHEN r.u4 < 0.10 AND v.v >= 4 THEN p.prefix || '/edited.txt' END,
    p.prefix || '/original.meta.json',
    CASE WHEN v.v >= 3 THEN p.prefix || '/cleaned.meta.json' END,
    CASE WHEN r.u4 < 0.10 AND v.v >= 4 THEN p.prefix || '/edited.meta.json' END,
    md5(d.base_id::text || v.v) || md5(v.v || d.base_id::text),
    t.at, t.at, t.at, t0.at,
    s.status::source_file_status_type,
    'scraped_file',
    (ARRAY['tax', 'health', 'education', 'transport', 'social'])[1 + d.doc_no % 5],
    s.is_excluded,
    s.is_deleted,
    1000 + floor(r.u6 * 200000)::bigint
FROM measure_docs AS d
CROSS JOIN LATERAL (
    SELECT (hashtext(d.base_id::text || ':1') & 2147483647) / 2147483647.0 AS u1,
           (hashtext(d.base_id::text || ':2') & 2147483647) / 2147483647.0 AS u2,
           (hashtext(d.base_id::text || ':3') & 2147483647) / 2147483647.0 AS u3,
           (hashtext(d.base_id::text || ':4') & 2147483647) / 2147483647.0 AS u4,
           (hashtext(d.base_id::text || ':5') & 2147483647) / 2147483647.0 AS u5,
           (hashtext(d.base_id::text || ':6') & 2147483647) / 2147483647.0 AS u6
) AS r
CROSS JOIN LATERAL (
    SELECT CASE WHEN r.u1 < 0.90 THEN 5 + floor(r.u2 * 11)
                ELSE 16 + floor(r.u2 * 185) END::int AS versions
) AS n
CROSS JOIN LATERAL generate_series(1, n.versions) AS v(v)
CROSS JOIN LATERAL (
    SELECT timestamptz '2025-01-01 00:00:00+00' + r.u5 * interval '30 days' AS at
) AS t0
CROSS JOIN LATERAL (
    SELECT t0.at + (v.v - 1) * (interval '330 days' / n.versions) AS at
) AS t
CROSS JOIN LATERAL (
    SELECT format('s3://ckb/uploads/scrapped-data/%s/%s/v%s',
                  d.agency_base_id, d.base_id, v.v) AS prefix
) AS p
CROSS JOIN LATERAL (
    -- Older versions cycle through the pipeline; the latest carries the outcome.
    SELECT CASE
               WHEN v.v < n.versions THEN (ARRAY['finished', 'scraping', 'cleaning'])[1 + v.v % 3]
               WHEN r.u3 < 0.09 AND r.u3 >= 0.07 THEN 'not_found'
               WHEN r.u3 < 0.12 AND r.u3 >= 0.09 THEN 'scraping'
               WHEN r.u3 < 0.14 AND r.u3 >= 0.12 THEN 'cleaning'
               ELSE 'finished'
           END AS status,
           v.v = n.versions AND r.u3 < 0.05 AS is_deleted,
           v.v = n.versions AND r.u3 >= 0.05 AND r.u3 < 0.07 AS is_excluded
) AS s
ORDER BY t.at;

ANALYZE data_collection.source_file;

SELECT a.ord,
       a.agency_base_id,
       count(DISTINCT f.base_id) AS documents,
       count(f.*) AS rows
FROM measure_agencies AS a
LEFT JOIN data_collection.source_file AS f ON f.agency_base_id = a.agency_base_id
GROUP BY a.ord, a.agency_base_id
ORDER BY a.ord;

\endif

SELECT count(*) AS total_rows,
       pg_size_pretty(pg_relation_size('data_collection.source_file')) AS heap,
       pg_size_pretty(pg_indexes_size('data_collection.source_file')) AS indexes,
       pg_size_pretty(pg_total_relation_size('data_collection.source_file')) AS total,
       pg_size_pretty(pg_relation_size('data_collection.idx_source_file_agency_base_updated'))
           AS export_index
FROM data_collection.source_file;
