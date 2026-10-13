-- Q1: Which country-indicator-year combinations are missing or incomplete?
-- One row per expected combination that has no usable value.
--   null_in_source : the API returned the observation, but with value = null
--   not_returned   : the API did not return the observation at all
DROP VIEW IF EXISTS gold_missing_values;
CREATE VIEW gold_missing_values AS
SELECT
    s.country_iso3,
    c.country_name,
    s.indicator_code,
    i.indicator_name,
    s.year,
    CASE WHEN f.country_iso3 IS NULL THEN 'not_returned' ELSE 'null_in_source' END AS missing_reason,
    f.source_last_updated,
    f.raw_response_id
FROM silver_expected_scope s
JOIN silver_dim_indicator i ON i.indicator_code = s.indicator_code
LEFT JOIN silver_dim_country c ON c.country_iso3 = s.country_iso3
LEFT JOIN silver_fact_indicator_value f
       ON f.country_iso3 = s.country_iso3 AND f.indicator_code = s.indicator_code AND f.year = s.year
WHERE f.value IS NULL
ORDER BY s.indicator_code, s.country_iso3, s.year;
