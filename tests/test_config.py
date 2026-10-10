import copy
from pathlib import Path

import pytest

from pipeline.config import ConfigError, load_config, parse_config
from tests.conftest import TEST_CONFIG


def test_repo_config_is_valid():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    assert len(cfg.countries) == 9
    assert cfg.years == list(range(2018, 2025))
    assert set(cfg.indicator_codes) == {"SP.POP.TOTL", "NY.GDP.MKTP.CD", "NY.GDP.PCAP.CD", "SP.DYN.LE00.IN"}


def test_adding_a_country_is_config_only():
    raw = copy.deepcopy(TEST_CONFIG)
    raw["countries"].append("fra")
    cfg = parse_config(raw)
    assert cfg.countries[-1] == "FRA"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda r: r.update(countries=["MEXICO"]), "ISO alpha-3"),
        (lambda r: r.update(countries=["MEX", "MEX"]), "Duplicate countries"),
        (lambda r: r["years"].update(start=2025), "years.start"),
        (lambda r: r["indicators"][0].update(aggregation="median"), "aggregation"),
        (lambda r: r["indicators"][2].update(weight_indicator="NOPE"), "weight_indicator"),
        (lambda r: r["quality"]["consistency_rules"][0].update(expected="NOPE"), "not a configured indicator"),
        (lambda r: r.pop("countries"), "missing or malformed"),
    ],
)
def test_invalid_config_is_rejected(mutate, message):
    raw = copy.deepcopy(TEST_CONFIG)
    mutate(raw)
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)
