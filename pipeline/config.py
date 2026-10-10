"""Load and validate config.yaml into simple dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ALLOWED_AGGREGATIONS = {"sum", "weighted_avg"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Indicator:
    code: str
    name: str
    unit: str
    aggregation: str
    valid_min: float
    valid_max: float
    weight_indicator: str | None = None


@dataclass(frozen=True)
class ConsistencyRule:
    name: str
    numerator: str
    denominator: str
    expected: str
    tolerance_pct: float


@dataclass
class Config:
    base_url: str
    per_page: int
    timeout_seconds: float
    max_retries: int
    backoff_seconds: float
    database: str
    reports_dir: str
    year_start: int
    year_end: int
    countries: list[str]
    indicators: list[Indicator]
    consistency_rules: list[ConsistencyRule] = field(default_factory=list)
    max_missing_pct: float = 25.0

    @property
    def years(self) -> list[int]:
        return list(range(self.year_start, self.year_end + 1))

    @property
    def indicator_codes(self) -> list[str]:
        return [i.code for i in self.indicators]


def load_config(path: str | Path = "config.yaml") -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return parse_config(raw)


def parse_config(raw: dict) -> Config:
    try:
        api = raw["api"]
        storage = raw["storage"]
        years = raw["years"]
        countries = [str(c).strip().upper() for c in raw["countries"]]
        indicators = [
            Indicator(
                code=str(i["code"]).strip(),
                name=str(i["name"]),
                unit=str(i.get("unit", "")),
                aggregation=str(i.get("aggregation", "sum")),
                valid_min=float(i["valid_range"][0]),
                valid_max=float(i["valid_range"][1]),
                weight_indicator=i.get("weight_indicator"),
            )
            for i in raw["indicators"]
        ]
        quality = raw.get("quality", {}) or {}
        rules = [
            ConsistencyRule(
                name=r["name"],
                numerator=r["numerator"],
                denominator=r["denominator"],
                expected=r["expected"],
                tolerance_pct=float(r["tolerance_pct"]),
            )
            for r in quality.get("consistency_rules", []) or []
        ]
        cfg = Config(
            base_url=str(api["base_url"]).rstrip("/"),
            per_page=int(api.get("per_page", 1000)),
            timeout_seconds=float(api.get("timeout_seconds", 30)),
            max_retries=int(api.get("max_retries", 4)),
            backoff_seconds=float(api.get("backoff_seconds", 2)),
            database=str(storage["database"]),
            reports_dir=str(storage.get("reports_dir", "reports")),
            year_start=int(years["start"]),
            year_end=int(years["end"]),
            countries=countries,
            indicators=indicators,
            consistency_rules=rules,
            max_missing_pct=float(quality.get("max_missing_pct", 25)),
        )
    except (KeyError, TypeError, IndexError) as e:
        raise ConfigError(f"Invalid config: missing or malformed field {e}") from e

    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if not cfg.countries:
        raise ConfigError("At least one country is required")
    bad = [c for c in cfg.countries if len(c) != 3 or not c.isalpha()]
    if bad:
        raise ConfigError(f"Countries must be ISO alpha-3 codes: {bad}")
    if len(set(cfg.countries)) != len(cfg.countries):
        raise ConfigError("Duplicate countries in config")
    if cfg.year_start > cfg.year_end:
        raise ConfigError("years.start must be <= years.end")
    codes = cfg.indicator_codes
    if not codes:
        raise ConfigError("At least one indicator is required")
    if len(set(codes)) != len(codes):
        raise ConfigError("Duplicate indicators in config")
    for ind in cfg.indicators:
        if ind.aggregation not in ALLOWED_AGGREGATIONS:
            raise ConfigError(f"{ind.code}: aggregation must be one of {sorted(ALLOWED_AGGREGATIONS)}")
        if ind.aggregation == "weighted_avg" and ind.weight_indicator not in codes:
            raise ConfigError(f"{ind.code}: weight_indicator must be one of the configured indicators")
        if ind.valid_min > ind.valid_max:
            raise ConfigError(f"{ind.code}: valid_range min > max")
    for rule in cfg.consistency_rules:
        for code in (rule.numerator, rule.denominator, rule.expected):
            if code not in codes:
                raise ConfigError(f"Consistency rule {rule.name}: {code} is not a configured indicator")
