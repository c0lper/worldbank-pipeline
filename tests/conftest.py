"""Shared test helpers. Nothing here calls the real API."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from pipeline.api import WorldBankClient
from pipeline.config import parse_config
from pipeline.db import connect, create_schema

COUNTRY_INFO = {
    "MEX": ("MX", "Mexico", "Latin America & Caribbean ", "UMC", "Upper middle income"),
    "USA": ("US", "United States", "North America", "HIC", "High income"),
    "IND": ("IN", "India", "South Asia", "LMC", "Lower middle income"),
}


class FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class FakeSession:
    """Replays queued responses per URL (or a handler function) and records every call."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[str] = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        result = self.handler(url)
        if isinstance(result, Exception):
            raise result
        return result


def page_payload(records, page=1, pages=1, per_page=1000, total=None, lastupdated="2026-07-01"):
    meta = {
        "page": page, "pages": pages, "per_page": per_page,
        "total": len(records) if total is None else total,
        "sourceid": "2", "lastupdated": lastupdated,
    }
    return [meta, records]


def obs(iso3, code, year, value):
    """One observation shaped like the World Bank API returns it."""
    iso2, name = COUNTRY_INFO[iso3][0], COUNTRY_INFO[iso3][1]
    return {
        "indicator": {"id": code, "value": code},
        "country": {"id": iso2, "value": name},
        "countryiso3code": iso3,
        "date": str(year),
        "value": value,
        "unit": "",
        "obs_status": "",
        "decimal": 0,
    }


def country_record(iso3):
    iso2, name, region, inc_id, inc = COUNTRY_INFO[iso3]
    return {
        "id": iso3, "iso2Code": iso2, "name": name,
        "region": {"id": "X", "iso2code": "X", "value": region},
        "incomeLevel": {"id": inc_id, "iso2code": "X", "value": inc},
        "lendingType": {"id": "X", "iso2code": "X", "value": "X"},
        "capitalCity": "", "longitude": "", "latitude": "",
    }


def default_values():
    """(iso3, indicator, year) -> value for the test config. GDP per capita = GDP / population."""
    pop = {"MEX": 100.0, "USA": 300.0, "IND": 1000.0}
    gdp = {"MEX": 1000.0, "USA": 9000.0, "IND": 5000.0}
    life = {"MEX": 75.0, "USA": 78.0, "IND": 70.0}
    values = {}
    for iso3 in pop:
        for year in (2022, 2023, 2024):
            growth = 1 + 0.01 * (year - 2022)
            p, g = pop[iso3] * growth, gdp[iso3] * growth * growth
            values[(iso3, "SP.POP.TOTL", year)] = p
            values[(iso3, "NY.GDP.MKTP.CD", year)] = g
            values[(iso3, "NY.GDP.PCAP.CD", year)] = g / p
            values[(iso3, "SP.DYN.LE00.IN", year)] = life[iso3] + (year - 2022) * 0.1
    return values


class FakeWorldBank:
    """Serves API-shaped payloads from an in-memory dict of values; values can be edited between runs.

    A key mapped to None is returned as an observation with "value": null.
    A key absent from the dict is not returned at all.
    """

    def __init__(self, values, countries=("MEX", "USA", "IND")):
        self.values = values
        self.countries = list(countries)
        self.lastupdated = "2026-07-01"

    def __call__(self, url):
        parsed = urlsplit(url)  # urlsplit keeps ";" inside the path
        parts = parsed.path.split("/")
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        requested = parts[parts.index("country") + 1].split(";")
        if "indicator" not in parts:
            records = [country_record(c) for c in requested if c in self.countries]
        else:
            code = parts[parts.index("indicator") + 1]
            start, end = (int(y) for y in q["date"].split(":"))
            records = [
                obs(iso3, ind, year, val)
                for (iso3, ind, year), val in sorted(self.values.items(), key=lambda kv: (kv[0][0], -kv[0][2]))
                if ind == code and iso3 in requested and start <= year <= end
            ]
        return FakeResponse(200, json.dumps(page_payload(records, lastupdated=self.lastupdated)))


TEST_CONFIG = {
    "api": {"base_url": "https://api.worldbank.org/v2", "per_page": 1000, "max_retries": 2, "backoff_seconds": 0},
    "storage": {"database": ":memory:", "reports_dir": "reports"},
    "years": {"start": 2022, "end": 2024},
    "countries": ["MEX", "USA", "IND"],
    "indicators": [
        {"code": "SP.POP.TOTL", "name": "Population", "unit": "people", "aggregation": "sum",
         "valid_range": [1, 1e10]},
        {"code": "NY.GDP.MKTP.CD", "name": "GDP", "unit": "US$", "aggregation": "sum",
         "valid_range": [1, 1e15]},
        {"code": "NY.GDP.PCAP.CD", "name": "GDP per capita", "unit": "US$", "aggregation": "weighted_avg",
         "weight_indicator": "SP.POP.TOTL", "valid_range": [1, 500000]},
        {"code": "SP.DYN.LE00.IN", "name": "Life expectancy", "unit": "years", "aggregation": "weighted_avg",
         "weight_indicator": "SP.POP.TOTL", "valid_range": [20, 100]},
    ],
    "quality": {
        "consistency_rules": [
            {"name": "gdp_per_capita_vs_gdp_over_population", "numerator": "NY.GDP.MKTP.CD",
             "denominator": "SP.POP.TOTL", "expected": "NY.GDP.PCAP.CD", "tolerance_pct": 2}
        ],
        "max_missing_pct": 50,
    },
}


@pytest.fixture
def cfg():
    return parse_config(json.loads(json.dumps(TEST_CONFIG)))


@pytest.fixture
def fake_wb():
    return FakeWorldBank(default_values())


@pytest.fixture
def con():
    """Fresh in-memory DuckDB with the schema created."""
    c = connect()
    create_schema(c)
    return c


@pytest.fixture
def client(fake_wb):
    return WorldBankClient(session=FakeSession(fake_wb), max_retries=2, backoff_seconds=0, sleep=lambda s: None)
