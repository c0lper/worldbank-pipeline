-- Q2: Year-over-year change for each indicator.
-- Compares each year with the immediately previous calendar year (no gap filling).
-- pct_change is NULL (never infinite, never 0) when it cannot be computed; yoy_status says why.
DROP VIEW IF EXISTS gold_yoy_change;
CREATE VIEW gold_yoy_change AS
SELECT
    s.country_iso3,
    c.country_name,
    s.indicator_code,
    i.indicator_name,
    s.year,
    cur.value AS value,
    prev.value AS prior_year_value,
    CASE WHEN cur.value IS NOT NULL AND prev.value IS NOT NULL
         THEN cur.value - prev.value END AS abs_change,
    CASE WHEN cur.value IS NOT NULL AND prev.value IS NOT NULL AND prev.value <> 0
         THEN ROUND(100.0 * (cur.value - prev.value) / ABS(prev.value), 4) END AS pct_change,
    CASE
        WHEN cur.value IS NULL THEN 'current_missing'
        WHEN prev.value IS NULL AND s.year = (SELECT MIN(year) FROM silver_expected_scope) THEN 'prior_year_not_in_range'
        WHEN prev.value IS NULL THEN 'prior_missing'
        WHEN prev.value = 0 THEN 'prior_zero'
        ELSE 'ok'
    END AS yoy_status
FROM silver_expected_scope s
JOIN silver_dim_indicator i ON i.indicator_code = s.indicator_code
LEFT JOIN silver_dim_country c ON c.country_iso3 = s.country_iso3
LEFT JOIN silver_fact_indicator_value cur
       ON cur.country_iso3 = s.country_iso3 AND cur.indicator_code = s.indicator_code AND cur.year = s.year
LEFT JOIN silver_fact_indicator_value prev
       ON prev.country_iso3 = s.country_iso3 AND prev.indicator_code = s.indicator_code AND prev.year = s.year - 1
ORDER BY s.indicator_code, s.country_iso3, s.year;
