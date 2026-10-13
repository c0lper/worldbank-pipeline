"""End-to-end runs through the CLI orchestration, with the fake API."""
import pytest

from pipeline import run as pipeline_run
from pipeline.db import connect


def counts(con):
    tables = ["bronze_raw_response", "silver_dim_country", "silver_fact_indicator_value"]
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def test_full_run_twice_is_idempotent(cfg, client, tmp_path):
    cfg.database = str(tmp_path / "wb.duckdb")
    cfg.reports_dir = str(tmp_path / "reports")

    first = pipeline_run.run(cfg, client=client)
    con = connect(cfg.database)
    after_first = counts(con)
    report_first = (tmp_path / "reports" / "yoy_change.csv").read_text(encoding="utf-8")
    con.close()

    second = pipeline_run.run(cfg, client=client)
    con = connect(cfg.database)
    after_second = counts(con)
    report_second = (tmp_path / "reports" / "yoy_change.csv").read_text(encoding="utf-8")

    assert first["facts_inserted"] == 36
    assert (second["raw_pages_new"], second["facts_inserted"], second["facts_updated"]) == (0, 0, 0)
    assert after_first == after_second
    assert report_first == report_second
    statuses = con.execute("SELECT status FROM meta_pipeline_run ORDER BY started_at_utc").fetchall()
    assert statuses == [("success",), ("success",)]
    con.close()


def test_rebuild_reprocesses_bronze_without_calling_the_api(cfg, client, con, tmp_path):
    cfg.reports_dir = str(tmp_path / "reports")
    pipeline_run.run(cfg, client=client, con=con)
    con.execute("DELETE FROM silver_fact_indicator_value")
    calls_before = len(client.session.calls)

    summary = pipeline_run.run(cfg, con=con, rebuild_only=True)

    assert len(client.session.calls) == calls_before
    assert summary["facts_inserted"] == 36


def test_dq_error_fails_the_run_and_keeps_previous_reports(cfg, client, con, fake_wb, tmp_path):
    cfg.reports_dir = str(tmp_path / "reports")
    pipeline_run.run(cfg, client=client, con=con)
    good_report = (tmp_path / "reports" / "latest_comparison.csv").read_text(encoding="utf-8")

    fake_wb.values[("MEX", "SP.DYN.LE00.IN", 2024)] = 300.0   # impossible life expectancy
    with pytest.raises(pipeline_run.PipelineError, match="value_within_valid_range"):
        pipeline_run.run(cfg, client=client, con=con)

    assert (tmp_path / "reports" / "latest_comparison.csv").read_text(encoding="utf-8") == good_report
    status = con.execute(
        "SELECT status FROM meta_pipeline_run ORDER BY started_at_utc DESC LIMIT 1").fetchone()[0]
    assert status == "failed"


def test_adding_a_country_in_config_extends_the_model(cfg, client, con, fake_wb, tmp_path):
    cfg.reports_dir = str(tmp_path / "reports")
    cfg.countries = ["MEX", "USA"]
    pipeline_run.run(cfg, client=client, con=con)
    assert con.execute("SELECT COUNT(*) FROM silver_expected_scope").fetchone()[0] == 24

    cfg.countries = ["MEX", "USA", "IND"]
    summary = pipeline_run.run(cfg, client=client, con=con)

    assert summary["facts_inserted"] == 12  # only India's rows are new
    assert con.execute("SELECT COUNT(*) FROM silver_expected_scope").fetchone()[0] == 36
