"""Data-quality checks on the silver model.

Each check is a SQL query that returns the failing rows. Results are stored in meta_dq_result.
Severity 'error' stops the run before gold reports are refreshed; 'warn' is recorded and logged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .bronze import utc_now
from .config import Config

log = logging.getLogger(__name__)

FACT = "silver_fact_indicator_value"


@dataclass
class CheckResult:
    name: str
    severity: str
    failing_rows: int
    sample: list

    @property
    def passed(self) -> bool:
        return self.failing_rows == 0


def build_checks(cfg: Config) -> list[tuple[str, str, str, list]]:
    """(name, severity, sql returning failing rows, params)"""
    checks = [
        (
            "fact_business_key_is_unique", "error",
            f"""SELECT country_iso3, indicator_code, year, COUNT(*) AS n FROM {FACT}
                GROUP BY country_iso3, indicator_code, year HAVING COUNT(*) > 1""",
            [],
        ),
        (
            "dim_country_key_is_unique", "error",
            "SELECT country_iso3, COUNT(*) FROM silver_dim_country GROUP BY country_iso3 HAVING COUNT(*) > 1",
            [],
        ),
        (
            "fact_value_status_matches_value", "error",
            f"""SELECT country_iso3, indicator_code, year, value, value_status FROM {FACT}
                WHERE (value IS NULL AND value_status <> 'null_in_source')
                   OR (value IS NOT NULL AND value_status <> 'available')""",
            [],
        ),
        (
            "fact_has_matching_dimensions", "error",
            f"""SELECT f.country_iso3, f.indicator_code, f.year FROM {FACT} f
                LEFT JOIN silver_dim_country c ON c.country_iso3 = f.country_iso3
                LEFT JOIN silver_dim_indicator i ON i.indicator_code = f.indicator_code
                WHERE c.country_iso3 IS NULL OR i.indicator_code IS NULL""",
            [],
        ),
        (
            "fact_is_traceable_to_raw_response", "error",
            f"""SELECT f.country_iso3, f.indicator_code, f.year FROM {FACT} f
                LEFT JOIN bronze_raw_response b ON b.raw_response_id = f.raw_response_id
                WHERE b.raw_response_id IS NULL""",
            [],
        ),
        (
            "value_within_valid_range", "error",
            f"""SELECT f.country_iso3, f.indicator_code, f.year, f.value FROM {FACT} f
                JOIN silver_dim_indicator i ON i.indicator_code = f.indicator_code
                WHERE f.value IS NOT NULL AND (f.value < i.valid_min OR f.value > i.valid_max)""",
            [],
        ),
        (
            "every_expected_combination_was_returned", "warn",
            f"""SELECT s.country_iso3, s.indicator_code, s.year FROM silver_expected_scope s
                LEFT JOIN {FACT} f ON f.country_iso3 = s.country_iso3
                 AND f.indicator_code = s.indicator_code AND f.year = s.year
                WHERE f.country_iso3 IS NULL""",
            [],
        ),
        (
            "missing_share_below_threshold", "warn",
            f"""SELECT missing_pct FROM (
                    SELECT 100.0 * SUM(CASE WHEN f.value IS NULL THEN 1 ELSE 0 END) / COUNT(*) AS missing_pct
                    FROM silver_expected_scope s
                    LEFT JOIN {FACT} f ON f.country_iso3 = s.country_iso3
                     AND f.indicator_code = s.indicator_code AND f.year = s.year
                ) t WHERE missing_pct > ?""",
            [cfg.max_missing_pct],
        ),
    ]

    # Cross-indicator consistency, e.g. GDP per capita ~= GDP / population
    for rule in cfg.consistency_rules:
        checks.append(
            (
                f"consistency_{rule.name}", "warn",
                f"""SELECT e.country_iso3, e.year, e.value AS expected_value,
                           n.value / d.value AS derived_value,
                           ROUND(100.0 * ABS(e.value - n.value / d.value) / ABS(e.value), 3) AS diff_pct
                    FROM {FACT} e
                    JOIN {FACT} n ON n.country_iso3 = e.country_iso3 AND n.year = e.year AND n.indicator_code = ?
                    JOIN {FACT} d ON d.country_iso3 = e.country_iso3 AND d.year = e.year AND d.indicator_code = ?
                    WHERE e.indicator_code = ?
                      AND e.value IS NOT NULL AND n.value IS NOT NULL AND d.value IS NOT NULL
                      AND e.value <> 0 AND d.value <> 0
                      AND 100.0 * ABS(e.value - n.value / d.value) / ABS(e.value) > ?""",
                [rule.numerator, rule.denominator, rule.expected, rule.tolerance_pct],
            )
        )
    return checks


def run_checks(con, cfg: Config, run_id: str) -> list[CheckResult]:
    results = []
    now = utc_now()
    for name, severity, sql, params in build_checks(cfg):
        rows = con.execute(sql, params).fetchall()
        result = CheckResult(name, severity, len(rows), rows[:5])
        results.append(result)
        details = "" if result.passed else f"sample: {result.sample}"
        con.execute(
            "INSERT INTO meta_dq_result VALUES (?, ?, ?, ?, ?, ?, ?)",
            [run_id, name, severity, "pass" if result.passed else "fail", len(rows), details[:2000], now],
        )
        if result.passed:
            log.info("DQ pass  %-45s", name)
        else:
            log.warning("DQ FAIL  %-45s [%s] %d row(s) %s", name, severity, len(rows), details[:300])
    return results


def errors(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not r.passed and r.severity == "error"]
