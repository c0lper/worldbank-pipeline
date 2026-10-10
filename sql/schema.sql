-- Physical data model. Layers are separated by table prefix:
--   bronze_*  raw API responses, append-only, exactly as received
--   silver_*  cleaned, typed, standardized model (dims + fact)
--   gold_*    analytics views (see sql/gold/)
--   meta_*    run log and data-quality results
-- Uniqueness of business keys is enforced by data-quality checks (see pipeline/quality.py).

-- One row per distinct API response page. raw_response_id = hash(request_url + body),
-- so re-fetching identical data does not create a new row.
CREATE TABLE IF NOT EXISTS bronze_raw_response (
    raw_response_id   VARCHAR NOT NULL,
    run_id            VARCHAR NOT NULL,
    request_key       VARCHAR NOT NULL,   -- 'country_metadata' or 'indicator:<code>'
    request_url       VARCHAR NOT NULL,
    page              INTEGER NOT NULL,
    fetched_at_utc    TIMESTAMP NOT NULL,
    payload_sha256    VARCHAR NOT NULL,
    payload_json      VARCHAR NOT NULL
);

-- Grain: one row per country. Business key: country_iso3.
CREATE TABLE IF NOT EXISTS silver_dim_country (
    country_iso3      VARCHAR NOT NULL,
    country_iso2      VARCHAR,
    country_name      VARCHAR NOT NULL,
    region            VARCHAR,
    income_level_id   VARCHAR,
    income_level      VARCHAR,
    raw_response_id   VARCHAR NOT NULL,
    loaded_at_utc     TIMESTAMP NOT NULL
);

-- Grain: one row per indicator. Business key: indicator_code. Comes from config.yaml.
CREATE TABLE IF NOT EXISTS silver_dim_indicator (
    indicator_code    VARCHAR NOT NULL,
    indicator_name    VARCHAR NOT NULL,
    unit              VARCHAR,
    aggregation       VARCHAR NOT NULL,
    weight_indicator  VARCHAR,
    valid_min         DOUBLE,
    valid_max         DOUBLE
);

-- Expected grain requested by the config: country x indicator x year.
-- Used to tell "missing" apart from "never requested".
CREATE TABLE IF NOT EXISTS silver_expected_scope (
    country_iso3      VARCHAR NOT NULL,
    indicator_code    VARCHAR NOT NULL,
    year              INTEGER NOT NULL
);

-- Grain: one row per country x indicator x year returned by the API.
-- Business key: (country_iso3, indicator_code, year).
-- value is NULL when the API returned the observation without a value (never 0).
CREATE TABLE IF NOT EXISTS silver_fact_indicator_value (
    country_iso3         VARCHAR NOT NULL,
    indicator_code       VARCHAR NOT NULL,
    year                 INTEGER NOT NULL,
    value                DOUBLE,
    value_status         VARCHAR NOT NULL,  -- 'available' | 'null_in_source'
    obs_status           VARCHAR,
    source_last_updated  VARCHAR,           -- 'lastupdated' from the API page metadata
    raw_response_id      VARCHAR NOT NULL,  -- lineage to bronze_raw_response
    first_loaded_at_utc  TIMESTAMP NOT NULL,
    updated_at_utc       TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_pipeline_run (
    run_id            VARCHAR NOT NULL,
    command           VARCHAR NOT NULL,
    started_at_utc    TIMESTAMP NOT NULL,
    finished_at_utc   TIMESTAMP,
    status            VARCHAR NOT NULL,   -- running | success | failed
    raw_pages_new     INTEGER,
    facts_inserted    INTEGER,
    facts_updated     INTEGER,
    facts_unchanged   INTEGER,
    message           VARCHAR
);

CREATE TABLE IF NOT EXISTS meta_dq_result (
    run_id            VARCHAR NOT NULL,
    check_name        VARCHAR NOT NULL,
    severity          VARCHAR NOT NULL,   -- error | warn
    status            VARCHAR NOT NULL,   -- pass | fail
    failing_rows      INTEGER NOT NULL,
    details           VARCHAR,
    checked_at_utc    TIMESTAMP NOT NULL
);
