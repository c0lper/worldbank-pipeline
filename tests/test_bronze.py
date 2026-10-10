import pytest

from pipeline import bronze
from pipeline.api import ApiError
from tests.conftest import FakeResponse


def count(con, table):
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_ingest_stores_every_page_verbatim(con, cfg, client):
    ids, new = bronze.ingest(con, client, cfg, "run1")

    # 1 country metadata request + 4 indicators, one page each
    assert len(ids) == 5 and new == 5
    row = con.execute(
        "SELECT payload_json FROM bronze_raw_response WHERE request_key = 'indicator:SP.POP.TOTL'"
    ).fetchone()
    assert '"countryiso3code": "MEX"' in row[0]


def test_reingesting_unchanged_data_adds_no_bronze_rows(con, cfg, client):
    first_ids, _ = bronze.ingest(con, client, cfg, "run1")
    second_ids, new = bronze.ingest(con, client, cfg, "run2")

    assert new == 0
    assert sorted(first_ids) == sorted(second_ids)
    assert count(con, "bronze_raw_response") == 5


def test_changed_source_data_is_kept_as_a_new_raw_version(con, cfg, client, fake_wb):
    bronze.ingest(con, client, cfg, "run1")

    fake_wb.values[("MEX", "SP.POP.TOTL", 2024)] = 999.0
    _, new = bronze.ingest(con, client, cfg, "run2")

    assert new == 1  # only the population page changed
    assert count(con, "bronze_raw_response") == 6
    assert len(bronze.latest_snapshot_ids(con)) == 5


def test_failed_fetch_writes_nothing(con, cfg, client, fake_wb):
    def broken(url):
        if "SP.DYN.LE00.IN" in url:
            return FakeResponse(500, "error")
        return fake_wb(url)

    client.session.handler = broken

    with pytest.raises(ApiError, match="Giving up"):
        bronze.ingest(con, client, cfg, "run1")
    assert count(con, "bronze_raw_response") == 0
