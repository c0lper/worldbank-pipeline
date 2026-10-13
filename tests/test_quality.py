from pipeline import bronze, quality, silver


def load(con, cfg, client):
    ids, _ = bronze.ingest(con, client, cfg, "run")
    silver.load(con, cfg, ids)


def by_name(results):
    return {r.name: r for r in results}


def test_clean_data_passes_all_checks(con, cfg, client):
    load(con, cfg, client)

    results = quality.run_checks(con, cfg, "run")

    assert all(r.passed for r in results), [r for r in results if not r.passed]
    stored = con.execute("SELECT COUNT(*) FROM meta_dq_result WHERE run_id = 'run'").fetchone()[0]
    assert stored == len(results)


def test_inconsistent_gdp_per_capita_is_flagged(con, cfg, client, fake_wb):
    fake_wb.values[("MEX", "NY.GDP.PCAP.CD", 2023)] *= 1.10  # 10% off vs GDP / population
    load(con, cfg, client)

    result = by_name(quality.run_checks(con, cfg, "run"))["consistency_gdp_per_capita_vs_gdp_over_population"]

    assert result.failing_rows == 1
    assert result.sample[0][0:2] == ("MEX", 2023)


def test_small_rounding_differences_are_tolerated(con, cfg, client, fake_wb):
    fake_wb.values[("MEX", "NY.GDP.PCAP.CD", 2023)] *= 1.005  # 0.5% < 2% tolerance
    load(con, cfg, client)

    result = by_name(quality.run_checks(con, cfg, "run"))["consistency_gdp_per_capita_vs_gdp_over_population"]

    assert result.passed


def test_out_of_range_value_is_an_error(con, cfg, client, fake_wb):
    fake_wb.values[("IND", "SP.DYN.LE00.IN", 2024)] = 150.0
    load(con, cfg, client)

    results = quality.run_checks(con, cfg, "run")

    assert [r.name for r in quality.errors(results)] == ["value_within_valid_range"]


def test_missing_and_null_values_are_warnings_not_errors(con, cfg, client, fake_wb):
    fake_wb.values[("USA", "SP.DYN.LE00.IN", 2024)] = None       # returned with null value
    del fake_wb.values[("IND", "NY.GDP.MKTP.CD", 2024)]          # not returned at all
    load(con, cfg, client)

    results = by_name(quality.run_checks(con, cfg, "run"))

    assert results["every_expected_combination_was_returned"].failing_rows == 1
    assert results["every_expected_combination_was_returned"].severity == "warn"
    assert quality.errors(list(results.values())) == []


def test_duplicate_business_key_is_detected(con, cfg, client):
    load(con, cfg, client)
    con.execute(
        "INSERT INTO silver_fact_indicator_value SELECT * FROM silver_fact_indicator_value "
        "WHERE country_iso3 = 'MEX' AND indicator_code = 'SP.POP.TOTL' AND year = 2024"
    )

    results = by_name(quality.run_checks(con, cfg, "run"))

    assert not results["fact_business_key_is_unique"].passed
