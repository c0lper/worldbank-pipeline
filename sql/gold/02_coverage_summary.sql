-- Q1 (summary): completeness per country and indicator over the configured year range.
DROP VIEW IF EXISTS gold_coverage_summary;
CREATE VIEW gold_coverage_summary AS
SELECT
    s.country_iso3,
    c.country_name,
    s.indicator_code,
    i.indicator_name,
    COUNT(*) AS years_expected,
    COUNT(f.value) AS years_available,
    ROUND(100.0 * COUNT(f.value) / COUNT(*), 1) AS completeness_pct,
    string_agg(CASE WHEN f.value IS NULL THEN CAST(s.year AS VARCHAR) END, ',' ORDER BY s.year) AS missing_years,
    CASE
        WHEN COUNT(f.value) = COUNT(*) THEN 'complete'
        WHEN COUNT(f.value) = 0 THEN 'no_data'
        ELSE 'incomplete'
    END AS coverage_status
FROM silver_expected_scope s
JOIN silver_dim_indicator i ON i.indicator_code = s.indicator_code
LEFT JOIN silver_dim_country c ON c.country_iso3 = s.country_iso3
LEFT JOIN silver_fact_indicator_value f
       ON f.country_iso3 = s.country_iso3 AND f.indicator_code = s.indicator_code AND f.year = s.year
GROUP BY s.country_iso3, c.country_name, s.indicator_code, i.indicator_name
ORDER BY s.indicator_code, s.country_iso3;
