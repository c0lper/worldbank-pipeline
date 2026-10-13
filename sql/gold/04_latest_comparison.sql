-- Q3: How do the countries compare for the latest available year of each indicator?
-- Each country is shown at its own latest year with a value. years_behind_latest > 0 flags
-- countries whose latest value is older than the newest year available for that indicator,
-- so analysts can see when they are not comparing like with like.
DROP VIEW IF EXISTS gold_latest_comparison;
CREATE VIEW gold_latest_comparison AS
WITH available AS (
    SELECT f.country_iso3, f.indicator_code, f.year, f.value
    FROM silver_fact_indicator_value f
    JOIN silver_expected_scope s
      ON s.country_iso3 = f.country_iso3 AND s.indicator_code = f.indicator_code AND s.year = f.year
    WHERE f.value IS NOT NULL
),
country_latest AS (
    SELECT country_iso3, indicator_code, MAX(year) AS latest_year
    FROM available
    GROUP BY country_iso3, indicator_code
),
indicator_latest AS (
    SELECT indicator_code, MAX(year) AS indicator_latest_year
    FROM available
    GROUP BY indicator_code
)
SELECT
    cl.indicator_code,
    i.indicator_name,
    i.unit,
    cl.country_iso3,
    c.country_name,
    c.income_level,
    cl.latest_year,
    a.value,
    il.indicator_latest_year - cl.latest_year AS years_behind_latest,
    RANK() OVER (PARTITION BY cl.indicator_code ORDER BY a.value DESC) AS rank_highest_first
FROM country_latest cl
JOIN available a
  ON a.country_iso3 = cl.country_iso3 AND a.indicator_code = cl.indicator_code AND a.year = cl.latest_year
JOIN indicator_latest il ON il.indicator_code = cl.indicator_code
JOIN silver_dim_indicator i ON i.indicator_code = cl.indicator_code
LEFT JOIN silver_dim_country c ON c.country_iso3 = cl.country_iso3
ORDER BY cl.indicator_code, rank_highest_first;
