"""Silver layer: parse raw responses from bronze into a typed, standardized model.

- silver_dim_country / silver_dim_indicator / silver_expected_scope are refreshed from source + config.
- silver_fact_indicator_value is upserted on its business key (country_iso3, indicator_code, year):
  new keys are inserted, keys whose value changed are replaced, unchanged keys are left alone.
  Re-running with the same source data therefore changes nothing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .bronze import COUNTRY_METADATA_KEY, utc_now
from .config import Config

log = logging.getLogger(__name__)


@dataclass
class SilverStats:
    countries: int = 0
    facts_inserted: int = 0
    facts_updated: int = 0
    facts_unchanged: int = 0
    records_out_of_scope: int = 0


def load(con, cfg: Config, raw_response_ids: list[str]) -> SilverStats:
    countries, facts, stats = parse_raw(con, cfg, raw_response_ids)

    missing = sorted(set(cfg.countries) - {c["country_iso3"] for c in countries})
    if missing:
        raise ValueError(f"Country metadata not returned by the API for: {missing}")

    now = utc_now()
    con.execute("BEGIN TRANSACTION")
    try:
        _refresh_dimensions(con, cfg, countries, now)
        _upsert_facts(con, facts, now, stats)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    stats.countries = len(countries)
    log.info(
        "Silver: %d inserted, %d updated, %d unchanged, %d out-of-scope record(s) skipped",
        stats.facts_inserted, stats.facts_updated, stats.facts_unchanged, stats.records_out_of_scope,
    )
    return stats


def parse_raw(con, cfg: Config, raw_response_ids: list[str]):
    """Read the given bronze pages and turn them into standardized rows (oldest first, so newest wins)."""
    if not raw_response_ids:
        raise ValueError("No raw responses to process")
    placeholders = ",".join("?" for _ in raw_response_ids)
    rows = con.execute(
        f"""
        SELECT raw_response_id, request_key, payload_json
        FROM bronze_raw_response
        WHERE raw_response_id IN ({placeholders})
        ORDER BY fetched_at_utc, request_url, page
        """,
        list(raw_response_ids),
    ).fetchall()

    countries: dict[str, dict] = {}
    facts: dict[tuple, dict] = {}
    stats = SilverStats()
    wanted_countries, wanted_codes = set(cfg.countries), set(cfg.indicator_codes)

    for rid, request_key, payload_json in rows:
        payload = json.loads(payload_json)
        meta, records = payload[0], (payload[1] or [])
        if request_key == COUNTRY_METADATA_KEY:
            for rec in records:
                c = parse_country(rec, rid)
                if c["country_iso3"] in wanted_countries:
                    countries[c["country_iso3"]] = c
            continue
        for rec in records:
            f = parse_observation(rec, meta, rid)
            in_scope = (
                f["country_iso3"] in wanted_countries
                and f["indicator_code"] in wanted_codes
                and cfg.year_start <= f["year"] <= cfg.year_end
            )
            if not in_scope:
                stats.records_out_of_scope += 1
                continue
            facts[(f["country_iso3"], f["indicator_code"], f["year"])] = f
    return list(countries.values()), facts, stats


def parse_country(rec: dict, rid: str) -> dict:
    income = rec.get("incomeLevel") or {}
    region = rec.get("region") or {}
    return {
        "country_iso3": rec["id"].strip().upper(),
        "country_iso2": rec.get("iso2Code"),
        "country_name": rec["name"].strip(),
        "region": (region.get("value") or "").strip() or None,
        "income_level_id": income.get("id"),
        "income_level": (income.get("value") or "").strip() or None,
        "raw_response_id": rid,
    }


def parse_observation(rec: dict, meta: dict, rid: str) -> dict:
    value = rec.get("value")
    value = float(value) if value is not None else None
    return {
        "country_iso3": (rec.get("countryiso3code") or "").strip().upper(),
        "indicator_code": rec["indicator"]["id"],
        "year": int(rec["date"]),
        "value": value,
        "value_status": "available" if value is not None else "null_in_source",
        "obs_status": rec.get("obs_status") or None,
        "source_last_updated": meta.get("lastupdated"),
        "raw_response_id": rid,
    }


def _refresh_dimensions(con, cfg: Config, countries: list[dict], now) -> None:
    con.execute("DELETE FROM silver_dim_country")
    con.executemany(
        "INSERT INTO silver_dim_country VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [[c["country_iso3"], c["country_iso2"], c["country_name"], c["region"],
          c["income_level_id"], c["income_level"], c["raw_response_id"], now] for c in countries],
    )

    con.execute("DELETE FROM silver_dim_indicator")
    con.executemany(
        "INSERT INTO silver_dim_indicator VALUES (?, ?, ?, ?, ?, ?, ?)",
        [[i.code, i.name, i.unit, i.aggregation, i.weight_indicator, i.valid_min, i.valid_max]
         for i in cfg.indicators],
    )

    con.execute("DELETE FROM silver_expected_scope")
    con.executemany(
        "INSERT INTO silver_expected_scope VALUES (?, ?, ?)",
        [[c, code, y] for c in cfg.countries for code in cfg.indicator_codes for y in cfg.years],
    )


def _upsert_facts(con, facts: dict[tuple, dict], now, stats: SilverStats) -> None:
    existing = {
        (r[0], r[1], r[2]): (r[3], r[4], r[5])
        for r in con.execute(
            "SELECT country_iso3, indicator_code, year, value, value_status, first_loaded_at_utc "
            "FROM silver_fact_indicator_value"
        ).fetchall()
    }

    to_write = []
    for key, f in facts.items():
        if key not in existing:
            stats.facts_inserted += 1
            first_loaded = now
        else:
            old_value, old_status, first_loaded = existing[key]
            if old_value == f["value"] and old_status == f["value_status"]:
                stats.facts_unchanged += 1
                continue
            stats.facts_updated += 1
            log.info("Value changed for %s: %s -> %s", key, old_value, f["value"])
        to_write.append((key, f, first_loaded))

    if not to_write:
        return
    con.executemany(
        "DELETE FROM silver_fact_indicator_value WHERE country_iso3 = ? AND indicator_code = ? AND year = ?",
        [list(key) for key, _, _ in to_write],
    )
    con.executemany(
        "INSERT INTO silver_fact_indicator_value VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [[f["country_iso3"], f["indicator_code"], f["year"], f["value"], f["value_status"],
          f["obs_status"], f["source_last_updated"], f["raw_response_id"], first_loaded, now]
         for _, f, first_loaded in to_write],
    )
