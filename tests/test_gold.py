import csv

import pytest

from pipeline import bronze, gold, silver
from pipeline import run as pipeline_run
from pipeline.db import query_dicts


def run_once(con, cfg, client, tmp_path):
    cfg.reports_dir = str(tmp_path / "reports")
    return pipeline_run.run(cfg, client=client, con=con)


def load_without_dq(con, cfg, client):
    """A 0 GDP fails the range check (as it should); build the views anyway for this test."""
    ids, _ = bronze.ingest(con, client, cfg, "run")
    silver.load(con, cfg, ids)
    gold.build_views(con)


def one(con, sql, params=None):
    rows = query_dicts(con, sql, params)
    assert len(rows) == 1, rows
    return rows[0]


def test_missing_values_distinguishes_null_from_not_returned(con, cfg, client, fake_wb, tmp_path):
    fake_wb.values[("USA", "SP.DYN.LE00.IN", 2024)] = None
    del fake_wb.values[("IND", "NY.GDP.MKTP.CD", 2023)]
    run_once(con, cfg, client, tmp_path)

    rows = query_dicts(con, "SELECT country_iso3, indicator_code, year, missing_reason FROM gold_missing_values")

    assert sorted((r["country_iso3"], r["year"], r["missing_reason"]) for r in rows) == [
        ("IND", 2023, "not_returned"),
        ("USA", 2024, "null_in_source"),
    ]
    summary = one(con, "SELECT * FROM gold_coverage_summary WHERE country_iso3 = 'IND' "
                       "AND indicator_code = 'NY.GDP.MKTP.CD'")
    assert summary["years_available"] == 2 and summary["missing_years"] == "2023"
    assert summary["coverage_status"] == "incomplete"


def test_yoy_change_handles_missing_and_zero_prior_values(con, cfg, client, fake_wb):
    fake_wb.values[("MEX", "SP.POP.TOTL", 2022)] = 100.0
    fake_wb.values[("MEX", "SP.POP.TOTL", 2023)] = 110.0
    fake_wb.values[("MEX", "SP.POP.TOTL", 2024)] = None
    fake_wb.values[("USA", "NY.GDP.MKTP.CD", 2023)] = 0.0   # unrealistic, but exercises the zero rule
    load_without_dq(con, cfg, client)

    rows = {(r["country_iso3"], r["indicator_code"], r["year"]): r
            for r in query_dicts(con, "SELECT * FROM gold_yoy_change")}

    assert rows[("MEX", "SP.POP.TOTL", 2022)]["yoy_status"] == "prior_year_not_in_range"
    assert rows[("MEX", "SP.POP.TOTL", 2023)]["pct_change"] == pytest.approx(10.0)
    assert rows[("MEX", "SP.POP.TOTL", 2023)]["abs_change"] == pytest.approx(10.0)
    assert rows[("MEX", "SP.POP.TOTL", 2024)]["yoy_status"] == "current_missing"
    assert rows[("MEX", "SP.POP.TOTL", 2024)]["pct_change"] is None
    usa_2024 = rows[("USA", "NY.GDP.MKTP.CD", 2024)]
    assert usa_2024["yoy_status"] == "prior_zero" and usa_2024["pct_change"] is None


def test_latest_comparison_uses_each_countrys_latest_year(con, cfg, client, fake_wb, tmp_path):
    fake_wb.values[("IND", "SP.DYN.LE00.IN", 2024)] = None
    run_once(con, cfg, client, tmp_path)

    rows = {r["country_iso3"]: r for r in query_dicts(
        con, "SELECT * FROM gold_latest_comparison WHERE indicator_code = 'SP.DYN.LE00.IN'")}

    assert rows["IND"]["latest_year"] == 2023 and rows["IND"]["years_behind_latest"] == 1
    assert rows["USA"]["latest_year"] == 2024 and rows["USA"]["rank_highest_first"] == 1


def test_income_group_uses_population_weighted_averages(con, cfg, client, fake_wb, tmp_path):
    # Put MEX and USA in the same group to test the weighting
    run_once(con, cfg, client, tmp_path)
    con.execute("UPDATE silver_dim_country SET income_level = 'High income' WHERE country_iso3 = 'MEX'")

    gdppc = one(con, "SELECT * FROM gold_income_group_by_year WHERE income_level = 'High income' "
                     "AND indicator_code = 'NY.GDP.PCAP.CD' AND year = 2022")
    pop = one(con, "SELECT * FROM gold_income_group_by_year WHERE income_level = 'High income' "
                   "AND indicator_code = 'SP.POP.TOTL' AND year = 2022")

    # weighted GDP per capita == total GDP / total population = (1000 + 9000) / (100 + 300)
    assert gdppc["group_value"] == pytest.approx(25.0)
    assert pop["group_value"] == pytest.approx(400.0)
    assert gdppc["countries_in_group"] == 2 and gdppc["coverage_status"] == "complete"


def test_income_group_flags_partial_coverage(con, cfg, client, fake_wb, tmp_path):
    fake_wb.values[("USA", "SP.DYN.LE00.IN", 2024)] = None
    run_once(con, cfg, client, tmp_path)
    con.execute("UPDATE silver_dim_country SET income_level = 'High income' WHERE country_iso3 = 'MEX'")

    row = one(con, "SELECT * FROM gold_income_group_by_year WHERE income_level = 'High income' "
                   "AND indicator_code = 'SP.DYN.LE00.IN' AND year = 2024")

    assert row["countries_reporting"] == 1 and row["coverage_status"] == "partial"
    assert row["group_value"] == pytest.approx(75.2)  # only MEX left


def test_reports_are_written(con, cfg, client, tmp_path):
    run_once(con, cfg, client, tmp_path)

    files = sorted(p.name for p in (tmp_path / "reports").iterdir())
    assert files == sorted([
        "missing_values.csv", "coverage_summary.csv", "yoy_change.csv",
        "latest_comparison.csv", "income_group_by_year.csv", "data_quality_results.csv",
    ])
    with open(tmp_path / "reports" / "yoy_change.csv", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 36
