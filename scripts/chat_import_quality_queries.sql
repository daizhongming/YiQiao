\set ON_ERROR_STOP on
\pset pager off

\if :{?historical_batch}
\else
\set historical_batch 'chat-import-20260713T032735Z'
\endif
\if :{?historical_xmin}
\else
\set historical_xmin 13434
\endif
\if :{?pro_project}
\else
\set pro_project 'benchmark-optimized-pro-20260714-full-r02'
\endif
\if :{?tiered_project}
\else
\set tiered_project 'benchmark-tiered-20260714-full-r01'
\endif
\if :{?pro_job_id}
\else
\set pro_job_id '8790c03f-44e6-4786-8dec-4e07e80683c2'
\endif
\if :{?tiered_job_id}
\else
\set tiered_job_id '40aac686-b03a-46ef-a874-512be1e5007c'
\endif
\if :{?historical_table}
\else
\set historical_table 'memories'
\endif
\if :{?candidate_table}
\else
\set candidate_table 'memories_bench_20260714'
\endif
\if :{?expected_historical_id_set_md5}
\else
\echo 'Refusing semantic comparison: pass expected_historical_id_set_md5 as an external psql variable.'
\quit 3
\endif

-- Historical freeze gate. The externally verified reference has 949 rows,
-- SHA-256 56a8040873dff6bde62f6f24bf60de3db74da941c4bd0c1a134e97948bd138d5,
-- and PostgreSQL built-in MD5 a87d5093318dcda117e4eb4fa4d63c41.
WITH historical AS (
    SELECT id
    FROM :"historical_table"
    WHERE payload->>'import_batch' = :'historical_batch'
      AND xmin::text::bigint <= :historical_xmin
)
SELECT
    count(*) = 949 AS row_count_ok,
    md5(coalesce(string_agg(lower(id::text) || E'\n', '' ORDER BY lower(id::text)), ''))
        = :'expected_historical_id_set_md5' AS id_set_fingerprint_ok
FROM historical
\gset historical_gate_
\if :historical_gate_row_count_ok
\else
\echo 'Refusing semantic comparison: historical row count does not equal 949.'
\quit 3
\endif
\if :historical_gate_id_set_fingerprint_ok
\else
\echo 'Refusing semantic comparison: historical ID-set fingerprint does not match.'
\quit 3
\endif

-- Tiered routing, fallback, audit, memory yield, and latency distribution.
SELECT
    chunk.status,
    count(*) AS persisted_rows,
    sum(json_array_length(coalesce(chunk.memory_ids, '[]'::json))) AS memories
FROM memory_import_chunks AS chunk
WHERE chunk.job_id = :'tiered_job_id'::uuid
GROUP BY chunk.status
ORDER BY chunk.status;

WITH successful AS (
    SELECT chunk.*
    FROM memory_import_chunks AS chunk
    WHERE chunk.job_id = :'tiered_job_id'::uuid
      AND chunk.status = 'succeeded'
)
SELECT
    coalesce(model_used, '[missing]') AS model_used,
    coalesce(fallback_reason, 'none') AS fallback_reason,
    coalesce(audit_result, 'not_audited') AS audit_result,
    count(*) AS chunks,
    sum(json_array_length(coalesce(memory_ids, '[]'::json))) AS memories,
    round(avg(duration_seconds)::numeric, 3) AS mean_seconds,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_seconds)::numeric, 3) AS p50_seconds,
    round(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_seconds)::numeric, 3) AS p95_seconds
FROM successful
GROUP BY model_used, fallback_reason, audit_result
ORDER BY chunks DESC, model_used, fallback_reason, audit_result;

-- Memory citations must be valid, belong to the persisted chunk source list,
-- and cite at least one core (non-overlap-only) message.
WITH jobs(variant, project_id, job_id) AS (
    VALUES
        ('full_pro', :'pro_project', :'pro_job_id'::uuid),
        ('tiered', :'tiered_project', :'tiered_job_id'::uuid)
), joined AS (
    SELECT
        jobs.variant,
        rows.id,
        CASE
            WHEN jsonb_typeof(rows.payload->'source_message_indices') = 'array'
            THEN rows.payload->'source_message_indices'
            ELSE '[]'::jsonb
        END AS citations,
        CASE
            WHEN jsonb_typeof(chunk.source_message_indices::jsonb) = 'array'
            THEN chunk.source_message_indices::jsonb
            ELSE '[]'::jsonb
        END AS chunk_sources,
        CASE
            WHEN jsonb_typeof(chunk.core_source_message_indices::jsonb) = 'array'
            THEN chunk.core_source_message_indices::jsonb
            ELSE '[]'::jsonb
        END AS chunk_core
    FROM jobs
    JOIN :"candidate_table" AS rows ON rows.payload->>'project_id' = jobs.project_id
    JOIN memory_import_chunks AS chunk
      ON chunk.job_id = jobs.job_id
     AND chunk.import_key = rows.payload->>'import_key'
     AND chunk.status = 'succeeded'
), checks AS (
    SELECT
        variant,
        id,
        jsonb_array_length(citations) > 0
            AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(citations) AS elements(item)
                WHERE jsonb_typeof(item) <> 'number' OR item #>> '{}' !~ '^[0-9]+$'
            )
            AND jsonb_array_length(citations) = (
                SELECT count(DISTINCT item) FROM jsonb_array_elements(citations) AS elements(item)
            ) AS citation_structure_valid,
        NOT EXISTS (
            SELECT item FROM jsonb_array_elements(citations) AS citation(item)
            EXCEPT
            SELECT item FROM jsonb_array_elements(chunk_sources) AS source(item)
        ) AS citation_in_chunk_sources,
        jsonb_array_length(chunk_core) > 0
            AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(chunk_core) AS elements(item)
                WHERE jsonb_typeof(item) <> 'number' OR item #>> '{}' !~ '^[0-9]+$'
            )
            AND jsonb_array_length(chunk_core) = (
                SELECT count(DISTINCT item) FROM jsonb_array_elements(chunk_core) AS elements(item)
            ) AS core_structure_valid,
        EXISTS (
            SELECT 1
            FROM jsonb_array_elements(citations) AS citation(item)
            JOIN jsonb_array_elements(chunk_core) AS core(item) ON core.item = citation.item
        ) AS cites_core_evidence
    FROM joined
)
SELECT
    variant,
    count(*) AS memories,
    count(*) FILTER (WHERE citation_structure_valid) AS citation_structure_valid,
    count(*) FILTER (WHERE citation_in_chunk_sources) AS citation_in_chunk_sources,
    count(*) FILTER (WHERE core_structure_valid) AS core_structure_valid,
    count(*) FILTER (WHERE cites_core_evidence) AS cites_core_evidence,
    count(*) FILTER (
        WHERE citation_structure_valid
          AND citation_in_chunk_sources
          AND core_structure_valid
          AND cites_core_evidence
    ) AS fully_valid
FROM checks
GROUP BY variant
ORDER BY variant;

-- Basename matching is permitted only after every individual scope proves that
-- one basename identifies at most one raw source path.
WITH variants(variant, project_id) AS (
    VALUES ('full_pro', :'pro_project'), ('tiered', :'tiered_project')
), scoped AS (
    SELECT
        variants.variant AS scope,
        rows.payload->>'source_path' AS source_path,
        regexp_replace(replace(coalesce(rows.payload->>'source_path', ''), E'\\', '/'), '^.*/', '') AS source_key
    FROM variants
    JOIN :"candidate_table" AS rows ON rows.payload->>'project_id' = variants.project_id
    UNION ALL
    SELECT
        'historical' AS scope,
        rows.payload->>'source_path' AS source_path,
        regexp_replace(replace(coalesce(rows.payload->>'source_path', ''), E'\\', '/'), '^.*/', '') AS source_key
    FROM :"historical_table" AS rows
    WHERE rows.payload->>'import_batch' = :'historical_batch'
      AND rows.xmin::text::bigint <= :historical_xmin
), collisions AS (
    SELECT scope, source_key
    FROM scoped
    GROUP BY scope, source_key
    HAVING count(DISTINCT source_path) > 1
)
SELECT count(*) = 0 AS clear
FROM collisions
\gset basename_collision_gate_
\if :basename_collision_gate_clear
\else
\echo 'Refusing semantic comparison: at least one basename collision was found.'
\quit 3
\endif

-- Basename collision and historical-path coverage gates. No raw path is returned.
WITH variants(variant, project_id) AS (
    VALUES ('full_pro', :'pro_project'), ('tiered', :'tiered_project')
), scoped AS (
    SELECT
        variants.variant,
        rows.payload->>'source_path' AS source_path,
        regexp_replace(replace(coalesce(rows.payload->>'source_path', ''), E'\\', '/'), '^.*/', '') AS source_key
    FROM variants
    JOIN :"candidate_table" AS rows ON rows.payload->>'project_id' = variants.project_id
), historical AS (
    SELECT
        rows.payload->>'source_path' AS source_path,
        regexp_replace(replace(coalesce(rows.payload->>'source_path', ''), E'\\', '/'), '^.*/', '') AS source_key
    FROM :"historical_table" AS rows
    WHERE rows.payload->>'import_batch' = :'historical_batch'
      AND rows.xmin::text::bigint <= :historical_xmin
), collisions AS (
    SELECT scope, count(*) AS collision_basenames
    FROM (
        SELECT variant AS scope, source_key
        FROM scoped
        GROUP BY variant, source_key
        HAVING count(DISTINCT source_path) > 1
        UNION ALL
        SELECT 'historical' AS scope, source_key
        FROM historical
        GROUP BY source_key
        HAVING count(DISTINCT source_path) > 1
    ) AS collision_keys
    GROUP BY scope
), historical_paths AS (
    SELECT DISTINCT source_key FROM historical
), coverage AS (
    SELECT
        variants.variant,
        count(DISTINCT historical_paths.source_key) AS historical_paths,
        count(DISTINCT scoped.source_key) FILTER (WHERE scoped.source_key IS NOT NULL) AS candidate_paths,
        count(DISTINCT historical_paths.source_key)
            - count(DISTINCT scoped.source_key) FILTER (WHERE scoped.source_key IS NOT NULL) AS missing_paths
    FROM variants
    CROSS JOIN historical_paths
    LEFT JOIN scoped
      ON scoped.variant = variants.variant
     AND scoped.source_key = historical_paths.source_key
    GROUP BY variants.variant
)
SELECT
    coverage.variant,
    coverage.historical_paths,
    coverage.candidate_paths,
    coverage.missing_paths,
    coalesce(collisions.collision_basenames, 0) AS candidate_collision_basenames,
    coalesce((SELECT collision_basenames FROM collisions WHERE scope = 'historical'), 0)
        AS historical_collision_basenames
FROM coverage
LEFT JOIN collisions ON collisions.scope = coverage.variant
ORDER BY coverage.variant;

-- Directional historical semantic proximity for both completed candidates.
-- Missing candidate paths remain in historical_to_candidate with similarity zero.
WITH variants(variant, project_id) AS (
    VALUES ('full_pro', :'pro_project'), ('tiered', :'tiered_project')
), historical AS (
    SELECT
        rows.id,
        rows.vector,
        regexp_replace(replace(coalesce(rows.payload->>'source_path', ''), E'\\', '/'), '^.*/', '') AS source_key
    FROM :"historical_table" AS rows
    WHERE rows.payload->>'import_batch' = :'historical_batch'
      AND rows.xmin::text::bigint <= :historical_xmin
), candidate AS (
    SELECT
        variants.variant,
        rows.id,
        rows.vector,
        regexp_replace(replace(coalesce(rows.payload->>'source_path', ''), E'\\', '/'), '^.*/', '') AS source_key
    FROM variants
    JOIN :"candidate_table" AS rows ON rows.payload->>'project_id' = variants.project_id
), historical_to_candidate AS (
    SELECT
        variants.variant,
        historical.id,
        coalesce(max(1 - (historical.vector <=> candidate.vector)), 0)::double precision AS similarity
    FROM variants
    CROSS JOIN historical
    LEFT JOIN candidate
      ON candidate.variant = variants.variant
     AND candidate.source_key = historical.source_key
    GROUP BY variants.variant, historical.id
), candidate_to_historical AS (
    SELECT
        candidate.variant,
        candidate.id,
        coalesce(max(1 - (candidate.vector <=> historical.vector)), 0)::double precision AS similarity
    FROM candidate
    LEFT JOIN historical ON historical.source_key = candidate.source_key
    GROUP BY candidate.variant, candidate.id
), scores AS (
    SELECT 'historical_to_candidate' AS direction, variant, similarity FROM historical_to_candidate
    UNION ALL
    SELECT 'candidate_to_historical' AS direction, variant, similarity FROM candidate_to_historical
)
SELECT
    direction,
    variant,
    count(*) AS n,
    count(*) FILTER (WHERE similarity = 0) AS zero_similarity,
    round(avg(similarity)::numeric, 6) AS mean,
    round(percentile_cont(0.1) WITHIN GROUP (ORDER BY similarity)::numeric, 6) AS p10,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY similarity)::numeric, 6) AS median,
    round(percentile_cont(0.9) WITHIN GROUP (ORDER BY similarity)::numeric, 6) AS p90,
    count(*) FILTER (WHERE similarity >= 0.70) AS at_or_above_070,
    count(*) FILTER (WHERE similarity >= 0.80) AS at_or_above_080,
    count(*) FILTER (WHERE similarity >= 0.85) AS at_or_above_085,
    count(*) FILTER (WHERE similarity >= 0.90) AS at_or_above_090
FROM scores
GROUP BY direction, variant
ORDER BY direction, variant;

-- Per-historical-memory tiered delta against the full-Pro anchor.
WITH variants(variant, project_id) AS (
    VALUES ('full_pro', :'pro_project'), ('tiered', :'tiered_project')
), historical AS (
    SELECT
        rows.id,
        rows.vector,
        regexp_replace(replace(coalesce(rows.payload->>'source_path', ''), E'\\', '/'), '^.*/', '') AS source_key
    FROM :"historical_table" AS rows
    WHERE rows.payload->>'import_batch' = :'historical_batch'
      AND rows.xmin::text::bigint <= :historical_xmin
), candidate AS (
    SELECT
        variants.variant,
        rows.vector,
        regexp_replace(replace(coalesce(rows.payload->>'source_path', ''), E'\\', '/'), '^.*/', '') AS source_key
    FROM variants
    JOIN :"candidate_table" AS rows ON rows.payload->>'project_id' = variants.project_id
), scores AS (
    SELECT
        variants.variant,
        historical.id,
        coalesce(max(1 - (historical.vector <=> candidate.vector)), 0)::double precision AS similarity
    FROM variants
    CROSS JOIN historical
    LEFT JOIN candidate
      ON candidate.variant = variants.variant
     AND candidate.source_key = historical.source_key
    GROUP BY variants.variant, historical.id
), paired AS (
    SELECT
        id,
        max(similarity) FILTER (WHERE variant = 'full_pro') AS full_pro_similarity,
        max(similarity) FILTER (WHERE variant = 'tiered') AS tiered_similarity
    FROM scores
    GROUP BY id
), deltas AS (
    SELECT tiered_similarity - full_pro_similarity AS delta FROM paired
)
SELECT
    count(*) AS n,
    round(avg(delta)::numeric, 6) AS mean_delta,
    round(percentile_cont(0.1) WITHIN GROUP (ORDER BY delta)::numeric, 6) AS p10_delta,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY delta)::numeric, 6) AS median_delta,
    round(percentile_cont(0.9) WITHIN GROUP (ORDER BY delta)::numeric, 6) AS p90_delta,
    count(*) FILTER (WHERE delta > 0) AS tiered_higher,
    count(*) FILTER (WHERE delta = 0) AS tied,
    count(*) FILTER (WHERE delta < 0) AS tiered_lower
FROM deltas;

-- Direct full-Pro/tiered proximity on exact source paths (no legacy basename fallback).
WITH full_pro AS (
    SELECT id, vector, payload->>'source_path' AS source_path
    FROM :"candidate_table"
    WHERE payload->>'project_id' = :'pro_project'
), tiered AS (
    SELECT id, vector, payload->>'source_path' AS source_path
    FROM :"candidate_table"
    WHERE payload->>'project_id' = :'tiered_project'
), scores AS (
    SELECT
        'full_pro_to_tiered' AS direction,
        full_pro.id,
        coalesce(max(1 - (full_pro.vector <=> tiered.vector)), 0)::double precision AS similarity
    FROM full_pro
    LEFT JOIN tiered ON tiered.source_path = full_pro.source_path
    GROUP BY full_pro.id
    UNION ALL
    SELECT
        'tiered_to_full_pro' AS direction,
        tiered.id,
        coalesce(max(1 - (tiered.vector <=> full_pro.vector)), 0)::double precision AS similarity
    FROM tiered
    LEFT JOIN full_pro ON full_pro.source_path = tiered.source_path
    GROUP BY tiered.id
)
SELECT
    direction,
    count(*) AS n,
    count(*) FILTER (WHERE similarity = 0) AS zero_similarity,
    round(avg(similarity)::numeric, 6) AS mean,
    round(percentile_cont(0.1) WITHIN GROUP (ORDER BY similarity)::numeric, 6) AS p10,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY similarity)::numeric, 6) AS median,
    round(percentile_cont(0.9) WITHIN GROUP (ORDER BY similarity)::numeric, 6) AS p90,
    count(*) FILTER (WHERE similarity >= 0.70) AS at_or_above_070,
    count(*) FILTER (WHERE similarity >= 0.80) AS at_or_above_080,
    count(*) FILTER (WHERE similarity >= 0.85) AS at_or_above_085,
    count(*) FILTER (WHERE similarity >= 0.90) AS at_or_above_090
FROM scores
GROUP BY direction
ORDER BY direction;
