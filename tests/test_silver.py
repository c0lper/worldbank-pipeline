import pytest

from pipeline import bronze, silver


def ingest_and_load(con, cfg, client, run_id="run"):
    ids, _ = bronze.ingest(con, client, cfg, run_id)
    return silver.load(con, cfg, ids)


def fact_count(con):
    return con.execute("SELECT COUNT(*) FROM silver_fact_indicator_value").fetchone()[0]


def test_loads_standardized_model(con, cfg, client):
    stats = ingest_and_load(con, cfg, client)

    assert stats.facts_inserted == 3 * 4 * 3  # countries x indicators x years
    assert fact_count(con) == 36
    row = con.execute(
        "SELECT country_name, income_level FROM silver_dim_country WHERE country_iso3 = 'IND'"
    ).fetchone()
    assert row == ("India", "Lower middle income")
    assert con.execute("SELECT COUNT(*) FROM silver_expected_scope").fetchone()[0] == 36


def test_rerun_with_unchanged_source_is_idempotent(con, cfg, client):
    ingest_and_load(con, cfg, client, "run1")
    before = con.execute("SELECT * FROM silver_fact_indicator_value ORDER BY 1, 2, 3").fetchall()

    stats = ingest_and_load(con, cfg, client, "run2")

    after = con.execute("SELECT * FROM silver_fact_indicator_value ORDER BY 1, 2, 3").fetchall()
    assert (stats.facts_inserted, stats.facts_updated, stats.facts_unchanged) == (0, 0, 36)
    assert before == after


def test_corrected_source_value_updates_in_place(con, cfg, client, fake_wb):
    ingest_and_load(con, cfg, client, "run1")

    fake_wb.values[("MEX", "SP.POP.TOTL", 2024)] = 123.0
    stats = ingest_and_load(con, cfg, client, "run2")

    assert (stats.facts_inserted, stats.facts_updated) == (0, 1)
    assert fact_count(con) == 36
    value, rid = con.execute(
        "SELECT value, raw_response_id FROM silver_fact_indicator_value "
        "WHERE country_iso3 = 'MEX' AND indicator_code = 'SP.POP.TOTL' AND year = 2024"
    ).fetchone()
    assert value == 123.0
    # lineage points at the new raw response that contains the corrected value
    payload = con.execute(
        "SELECT payload_json FROM bronze_raw_response WHERE raw_response_id = ?", [rid]
    ).fetchone()[0]
    assert '"value": 123.0' in payload


def test_null_values_stay_null_not_zero(con, cfg, client, fake_wb):
    fake_wb.values[("USA", "SP.DYN.LE00.IN", 2024)] = None

    ingest_and_load(con, cfg, client)

    value, status = con.execute(
        "SELECT value, value_status FROM silver_fact_indicator_value "
        "WHERE country_iso3 = 'USA' AND indicator_code = 'SP.DYN.LE00.IN' AND year = 2024"
    ).fetchone()
    assert value is None and status == "null_in_source"


def test_every_fact_is_traceable_to_a_raw_response(con, cfg, client):
    ingest_and_load(con, cfg, client)

    orphans = con.execute(
        "SELECT COUNT(*) FROM silver_fact_indicator_value f "
        "LEFT JOIN bronze_raw_response b ON b.raw_response_id = f.raw_response_id "
        "WHERE b.raw_response_id IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_rebuild_from_bronze_without_api(con, cfg, client):
    ingest_and_load(con, cfg, client)
    con.execute("DELETE FROM silver_fact_indicator_value")

    stats = silver.load(con, cfg, bronze.latest_snapshot_ids(con))

    assert stats.facts_inserted == 36


def test_missing_country_metadata_fails(con, cfg, client, fake_wb):
    fake_wb.countries = ["MEX", "USA"]
    with pytest.raises(ValueError, match="IND"):
        ingest_and_load(con, cfg, client)
