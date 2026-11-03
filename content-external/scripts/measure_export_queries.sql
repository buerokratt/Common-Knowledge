-- Synthetic source_file and source history for measuring the two Stage E export
-- queries (#320). LOCAL DATABASES ONLY: it inserts millions of rows straight
-- into data_collection.source_file and data_collection.source. Never run it
-- against a shared, staging or production database. See
-- content-external/RUNBOOK.md.
--
-- Load (idempotent per step; documents 1..n_docs that already exist are kept):
--   docker exec -i <db-container> psql -U <db-user> -d <db-name> \
--     -v measure_local_only=yes -v measure_agency_id=<agency-uuid> \
--     -v bucket=<bucket> -v n_docs=10000 [-v others_n_docs=10000] \
--     -f - < content-external/scripts/measure_export_queries.sql
-- Remove every generated row and reclaim the space (same measure_agency_id):
--   ... -v measure_local_only=yes -v measure_agency_id=<agency-uuid> \
--     -v action=cleanup -f - < ...
-- The local compose values for <db-container>, <db-user> and <db-name> are in
-- docker-compose.yml.
--
-- Five agencies: measure_agency_id is the measured one, and four others derived
-- from it make the index discriminate by agency. Every value is a hash of the
-- agency and document, so a re-run with the same variables produces the same
-- data, and cleanup removes exactly what load created.

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

\if :{?measure_agency_id}
\else
DO $$ BEGIN RAISE EXCEPTION
  'refusing to run: pass -v measure_agency_id=<uuid> for the measured agency, the same for load and cleanup';
END $$;
\endif

-- Generator shape. These defaults produced the RUNBOOK measurements; override
-- any of them with -v name=value.
-- Version depth: shallow_share of documents have shallow_min..shallow_max
-- versions, the rest are a tail of frequently refreshed pages with
-- deep_min..deep_max (mean about 20).
\if :{?shallow_share} \else \set shallow_share 0.90 \endif
\if :{?shallow_min_versions} \else \set shallow_min_versions 5 \endif
\if :{?shallow_max_versions} \else \set shallow_max_versions 15 \endif
\if :{?deep_min_versions} \else \set deep_min_versions 16 \endif
\if :{?deep_max_versions} \else \set deep_max_versions 200 \endif
-- Outcome of each document's latest version; the remainder is finished.
\if :{?deleted_share} \else \set deleted_share 0.05 \endif
\if :{?excluded_share} \else \set excluded_share 0.02 \endif
\if :{?not_found_share} \else \set not_found_share 0.02 \endif
\if :{?scraping_share} \else \set scraping_share 0.03 \endif
\if :{?cleaning_share} \else \set cleaning_share 0.02 \endif
-- Share of documents with a manual edit, from their fourth version on.
\if :{?edited_share} \else \set edited_share 0.10 \endif
-- Each history starts within start_spread of history_start and lasts history_span.
\if :{?history_start} \else \set history_start '2025-01-01 00:00:00+00' \endif
\if :{?start_spread} \else \set start_spread '30 days' \endif
\if :{?history_span} \else \set history_span '330 days' \endif
-- Row content. page_url_format takes the agency's ordinal, the document number
-- and two hashes; URL columns come out at about 120 characters.
\if :{?page_url_format} \else \set page_url_format 'https://www.asutus%s.ee/et/teenused-ja-toetused/%s/%s-%s' \endif
\if :{?page_title_format} \else \set page_title_format 'Teenuse kirjeldus %s – %s' \endif
\if :{?subsectors} \else \set subsectors 'tax,health,education,transport,social' \endif
\if :{?sources_per_agency} \else \set sources_per_agency 25 \endif
-- Source history. Every scrape writes the source row about five times
-- (is_running, running, finished, next run, zip-dirty finished), so one scrape
-- a day over history_span gives the default. Raise it for sources scraped more
-- often. The first round(sources_per_agency * source_deleted_share) sources of
-- each agency end deleted, and their files stay live rows, as
-- source/delete_source.sql leaves them.
\if :{?source_versions} \else \set source_versions 1650 \endif
\if :{?source_deleted_share} \else \set source_deleted_share 0.04 \endif
\if :{?min_file_size} \else \set min_file_size 1000 \endif
\if :{?file_size_spread} \else \set file_size_spread 200000 \endif

\if :{?action}
\else
\set action load
\endif
SELECT :'action' = 'cleanup' AS is_cleanup \gset

CREATE TEMP TABLE measure_agencies AS
SELECT CASE WHEN ord = 1 THEN :'measure_agency_id'::uuid
            ELSE md5(:'measure_agency_id'::uuid::text || ':other:' || ord)::uuid
       END AS agency_base_id,
       ord
FROM generate_series(1, 5) AS ord;

-- Refuse an agency this script did not make, for load and cleanup alike:
-- cleanup deletes every source_file and source row of these five agencies.
-- Generated rows are recognisable because their base_ids are hashes of the
-- agency and a contiguous counter, so a real agency id fails here: it has an
-- agency row, or rows whose base_id no counter produces.
WITH counts AS (
    SELECT m.agency_base_id,
           (SELECT count(DISTINCT f.base_id) FROM data_collection.source_file AS f
            WHERE f.agency_base_id = m.agency_base_id) AS n_files,
           (SELECT count(DISTINCT s.base_id) FROM data_collection.source AS s
            WHERE s.agency_base_id = m.agency_base_id) AS n_sources
    FROM measure_agencies AS m
),
expected_files AS (
    SELECT c.agency_base_id, md5(c.agency_base_id::text || ':' || d)::uuid AS base_id
    FROM counts AS c
    CROSS JOIN LATERAL generate_series(1, c.n_files) AS d
),
expected_sources AS (
    SELECT c.agency_base_id,
           md5(c.agency_base_id::text || ':source:' || k)::uuid AS base_id
    FROM counts AS c
    CROSS JOIN LATERAL generate_series(0, c.n_sources - 1) AS k
)
SELECT
    EXISTS (
        SELECT 1 FROM agency_management.agency AS a
        JOIN measure_agencies AS m ON a.base_id = m.agency_base_id
    )
    OR EXISTS (
        SELECT 1 FROM data_collection.source_file AS f
        JOIN measure_agencies AS m ON f.agency_base_id = m.agency_base_id
        WHERE NOT EXISTS (
            SELECT 1 FROM expected_files AS e
            WHERE e.agency_base_id = f.agency_base_id AND e.base_id = f.base_id
        )
    )
    OR EXISTS (
        SELECT 1 FROM data_collection.source AS s
        JOIN measure_agencies AS m ON s.agency_base_id = m.agency_base_id
        WHERE NOT EXISTS (
            SELECT 1 FROM expected_sources AS e
            WHERE e.agency_base_id = s.agency_base_id AND e.base_id = s.base_id
        )
    ) AS foreign_rows \gset
\if :foreign_rows
DO $$ BEGIN RAISE EXCEPTION
  'refusing to run: measure_agency_id, or an agency derived from it, has an agency row or rows this script did not generate. Pass a fresh UUID that no real agency uses.';
END $$;
\endif

\if :is_cleanup
DELETE FROM data_collection.source_file
WHERE agency_base_id IN (SELECT agency_base_id FROM measure_agencies);
DELETE FROM data_collection.source
WHERE agency_base_id IN (SELECT agency_base_id FROM measure_agencies);
VACUUM (FULL, ANALYZE) data_collection.source_file;
VACUUM (FULL, ANALYZE) data_collection.source;
\else

\if :{?bucket}
\else
DO $$ BEGIN RAISE EXCEPTION 'pass -v bucket=<name of the bucket in the generated s3:// URLs>'; END $$;
\endif
\if :{?n_docs}
\else
DO $$ BEGIN RAISE EXCEPTION 'pass -v n_docs=<documents for the measured agency>'; END $$;
\endif
\if :{?others_n_docs}
\else
\set others_n_docs :n_docs
\endif

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
    CASE WHEN a.ord = 1 THEN :n_docs ELSE :others_n_docs END
) AS d;

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
    md5(d.agency_base_id::text || ':source:' || d.doc_no % :sources_per_agency)::uuid,
    d.agency_base_id,
    format(:'page_url_format',
           d.ord, lpad(d.doc_no::text, 7, '0'),
           md5(d.base_id::text), md5(d.base_id::text || 's')),
    format(:'page_title_format', d.doc_no, left(md5(d.base_id::text || 't'), 30)),
    p.prefix || '/original.html',
    CASE WHEN v.v >= 3 THEN p.prefix || '/cleaned.txt' END,
    CASE WHEN r.u4 < :edited_share AND v.v >= 4 THEN p.prefix || '/edited.txt' END,
    p.prefix || '/original.meta.json',
    CASE WHEN v.v >= 3 THEN p.prefix || '/cleaned.meta.json' END,
    CASE WHEN r.u4 < :edited_share AND v.v >= 4 THEN p.prefix || '/edited.meta.json' END,
    md5(d.base_id::text || v.v) || md5(v.v || d.base_id::text),
    t.at, t.at, t.at, t0.at,
    s.status::source_file_status_type,
    'scraped_file',
    c.subsectors[1 + d.doc_no % cardinality(c.subsectors)],
    s.is_excluded,
    s.is_deleted,
    :min_file_size + floor(r.u6 * :file_size_spread)::bigint
FROM measure_docs AS d
CROSS JOIN (SELECT string_to_array(:'subsectors', ',') AS subsectors) AS c
CROSS JOIN LATERAL (
    SELECT (hashtext(d.base_id::text || ':1') & 2147483647) / 2147483647.0 AS u1,
           (hashtext(d.base_id::text || ':2') & 2147483647) / 2147483647.0 AS u2,
           (hashtext(d.base_id::text || ':3') & 2147483647) / 2147483647.0 AS u3,
           (hashtext(d.base_id::text || ':4') & 2147483647) / 2147483647.0 AS u4,
           (hashtext(d.base_id::text || ':5') & 2147483647) / 2147483647.0 AS u5,
           (hashtext(d.base_id::text || ':6') & 2147483647) / 2147483647.0 AS u6
) AS r
CROSS JOIN LATERAL (
    SELECT CASE WHEN r.u1 < :shallow_share
                THEN :shallow_min_versions
                     + floor(r.u2 * (:shallow_max_versions - :shallow_min_versions + 1))
                ELSE :deep_min_versions
                     + floor(r.u2 * (:deep_max_versions - :deep_min_versions + 1))
           END::int AS versions
) AS n
CROSS JOIN LATERAL generate_series(1, n.versions) AS v(v)
CROSS JOIN LATERAL (
    SELECT :'history_start'::timestamptz + r.u5 * :'start_spread'::interval AS at
) AS t0
CROSS JOIN LATERAL (
    SELECT t0.at + (v.v - 1) * (:'history_span'::interval / n.versions) AS at
) AS t
CROSS JOIN LATERAL (
    SELECT format('s3://%s/uploads/scrapped-data/%s/%s/v%s',
                  :'bucket', d.agency_base_id, d.base_id, v.v) AS prefix
) AS p
CROSS JOIN LATERAL (
    -- Upper bounds of each outcome's slice of [0, 1), in the order of the shares.
    SELECT :deleted_share AS deleted,
           :deleted_share + :excluded_share AS excluded,
           :deleted_share + :excluded_share + :not_found_share AS not_found,
           :deleted_share + :excluded_share + :not_found_share + :scraping_share AS scraping,
           :deleted_share + :excluded_share + :not_found_share + :scraping_share
               + :cleaning_share AS cleaning
) AS b
CROSS JOIN LATERAL (
    -- Older versions cycle through the pipeline; the latest carries the outcome.
    SELECT CASE
               WHEN v.v < n.versions THEN (ARRAY['finished', 'scraping', 'cleaning'])[1 + v.v % 3]
               WHEN r.u3 < b.not_found AND r.u3 >= b.excluded THEN 'not_found'
               WHEN r.u3 < b.scraping AND r.u3 >= b.not_found THEN 'scraping'
               WHEN r.u3 < b.cleaning AND r.u3 >= b.scraping THEN 'cleaning'
               ELSE 'finished'
           END AS status,
           v.v = n.versions AND r.u3 < b.deleted AS is_deleted,
           v.v = n.versions AND r.u3 >= b.deleted AND r.u3 < b.excluded AS is_excluded
) AS s
ORDER BY t.at;

-- Sources, once per agency: they do not depend on n_docs, so a later step keeps
-- them. base_id matches the source_base_id the files above were given.
INSERT INTO data_collection.source (
    base_id, agency_base_id, url, subsector, created_at, updated_at,
    last_scraped_at, next_scrapping_at, is_deleted, type, status,
    update_automatically, cron_schedule
)
SELECT
    src.base_id,
    src.agency_base_id,
    format('https://www.asutus%s.ee/et/allikas-%s', src.ord, src.k),
    c.subsectors[1 + src.k % cardinality(c.subsectors)],
    t0.at, t.at, t.at, t.at + interval '1 day',
    v.v = :source_versions AND src.deleted,
    'url_to_scrape',
    (ARRAY['running', 'finished'])[1 + v.v % 2]::source_status_type,
    TRUE,
    '0 0 3 * * ?'
FROM (
    SELECT a.agency_base_id,
           a.ord,
           k,
           md5(a.agency_base_id::text || ':source:' || k)::uuid AS base_id,
           k < round(:sources_per_agency * :source_deleted_share) AS deleted
    FROM measure_agencies AS a
    CROSS JOIN generate_series(0, :sources_per_agency - 1) AS k
) AS src
CROSS JOIN (SELECT string_to_array(:'subsectors', ',') AS subsectors) AS c
CROSS JOIN LATERAL (
    SELECT :'history_start'::timestamptz
           + ((hashtext(src.base_id::text) & 2147483647) / 2147483647.0)
             * :'start_spread'::interval AS at
) AS t0
CROSS JOIN LATERAL generate_series(1, :source_versions) AS v(v)
CROSS JOIN LATERAL (
    SELECT t0.at + (v.v - 1) * (:'history_span'::interval / :source_versions) AS at
) AS t
WHERE NOT EXISTS (
    SELECT 1 FROM data_collection.source AS s WHERE s.base_id = src.base_id
)
ORDER BY t.at;

ANALYZE data_collection.source_file;
ANALYZE data_collection.source;

SELECT a.ord,
       a.agency_base_id,
       (SELECT count(DISTINCT f.base_id) FROM data_collection.source_file AS f
        WHERE f.agency_base_id = a.agency_base_id) AS documents,
       (SELECT count(*) FROM data_collection.source_file AS f
        WHERE f.agency_base_id = a.agency_base_id) AS rows,
       (SELECT count(DISTINCT s.base_id) FROM data_collection.source AS s
        WHERE s.agency_base_id = a.agency_base_id) AS sources,
       (SELECT count(*) FROM data_collection.source AS s
        WHERE s.agency_base_id = a.agency_base_id) AS source_rows
FROM measure_agencies AS a
ORDER BY a.ord;

\endif

SELECT count(*) AS total_rows,
       pg_size_pretty(pg_relation_size('data_collection.source_file')) AS heap,
       pg_size_pretty(pg_indexes_size('data_collection.source_file')) AS indexes,
       pg_size_pretty(pg_total_relation_size('data_collection.source_file')) AS total,
       pg_size_pretty(pg_relation_size('data_collection.idx_source_file_agency_base_updated'))
           AS export_index,
       (SELECT count(*) FROM data_collection.source) AS total_source_rows,
       pg_size_pretty(pg_total_relation_size('data_collection.source')) AS source_total
FROM data_collection.source_file;
