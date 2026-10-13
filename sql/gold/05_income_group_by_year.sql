-- Q4: How do the indicators compare across income groups, by year?
-- Aggregation method comes from silver_dim_indicator (config.yaml):
--   sum          -> SUM(value)                                  e.g. population, GDP
--   weighted_avg -> SUM(value * weight) / SUM(weight)          e.g. GDP per capita, life expectancy
--                   (weight = population, so per-capita values are not averaged naively)
-- Only countries with a value (and weight) are aggregated; coverage_status flags partial groups.
-- Income group = the country's CURRENT World Bank classification, applied to all years.
DROP VIEW IF EXISTS gold_income_group_by_year;
CREATE VIEW gold_income_group_by_year AS
WITH base AS (
    SELECT
        c.income_level,
        s.indicator_code,
        i.indicator_name,
        i.aggregation,
        s.year,
        s.country_iso3,
        f.value,
        w.value AS weight,
        CASE
            WHEN f.value IS NULL THEN 0
            WHEN i.aggregation = 'weighted_avg' AND w.value IS NULL THEN 0
            ELSE 1
        END AS is_usable
    FROM silver_expected_scope s
    JOIN silver_dim_country c ON c.country_iso3 = s.country_iso3
    JOIN silver_dim_indicator i ON i.indicator_code = s.indicator_code
    LEFT JOIN silver_fact_indicator_value f
           ON f.country_iso3 = s.country_iso3 AND f.indicator_code = s.indicator_code AND f.year = s.year
    LEFT JOIN silver_fact_indicator_value w
           ON w.country_iso3 = s.country_iso3 AND w.indicator_code = i.weight_indicator AND w.year = s.year
)
SELECT
    income_level,
    indicator_code,
    indicator_name,
    year,
    aggregation,
    COUNT(*) AS countries_in_group,
    SUM(is_usable) AS countries_reporting,
    CASE
        WHEN SUM(is_usable) = 0 THEN NULL
        WHEN aggregation = 'sum' THEN SUM(CASE WHEN is_usable = 1 THEN value END)
        WHEN aggregation = 'weighted_avg'
            THEN SUM(CASE WHEN is_usable = 1 THEN value * weight END)
               / NULLIF(SUM(CASE WHEN is_usable = 1 THEN weight END), 0)
    END AS group_value,
    CASE
        WHEN SUM(is_usable) = COUNT(*) THEN 'complete'
        WHEN SUM(is_usable) = 0 THEN 'no_data'
        ELSE 'partial'
    END AS coverage_status,
    string_agg(country_iso3, ',' ORDER BY country_iso3) AS countries
FROM base
GROUP BY income_level, indicator_code, indicator_name, year, aggregation
ORDER BY indicator_code, year, income_level;
