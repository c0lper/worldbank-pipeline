"""Minimal World Bank API v2 client: paging, retries and payload validation.

Response shape (JSON): [ {page, pages, per_page, total, lastupdated, ...}, [record, ...] ]
Errors can come back as HTTP 200 with a body like: [ {"message": [{"id": "120", ...}]} ]
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    pass


@dataclass
class RawPage:
    """One API response page, kept verbatim for traceability."""
    request_url: str
    page: int
    text: str          # exact response body as received
    meta: dict
    records: list


class WorldBankClient:
    def __init__(
        self,
        base_url: str = "https://api.worldbank.org/v2",
        per_page: int = 1000,
        timeout_seconds: float = 30,
        max_retries: int = 4,
        backoff_seconds: float = 2,
        session=None,
        sleep=time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.per_page = per_page
        self.timeout = timeout_seconds
        self.max_retries = max_retries
        self.backoff = backoff_seconds
        self.session = session or requests.Session()
        self.sleep = sleep

    @classmethod
    def from_config(cls, cfg) -> "WorldBankClient":
        return cls(cfg.base_url, cfg.per_page, cfg.timeout_seconds, cfg.max_retries, cfg.backoff_seconds)

    # --- public helpers -------------------------------------------------
    def countries(self, iso3_codes: list[str]) -> list[RawPage]:
        return self.fetch_all_pages(f"country/{';'.join(iso3_codes)}", {})

    def indicator(self, iso3_codes: list[str], indicator_code: str, year_start: int, year_end: int) -> list[RawPage]:
        path = f"country/{';'.join(iso3_codes)}/indicator/{indicator_code}"
        return self.fetch_all_pages(path, {"date": f"{year_start}:{year_end}"})

    # --- core -----------------------------------------------------------
    def build_url(self, path: str, params: dict) -> str:
        # Sorted params -> the same request always produces the same URL (used for lineage ids)
        return f"{self.base_url}/{path}?{urlencode(sorted(params.items()), safe=':')}"

    def fetch_all_pages(self, path: str, params: dict) -> list[RawPage]:
        pages: list[RawPage] = []
        page = 1
        while True:
            url = self.build_url(path, {**params, "format": "json", "per_page": self.per_page, "page": page})
            text = self._get_text(url)
            meta, records = self._parse(text, url)
            pages.append(RawPage(url, page, text, meta, records))
            total_pages = int(meta.get("pages") or 1)
            if page >= total_pages:
                break
            page += 1

        # Guard against silently truncated results
        expected = int(pages[-1].meta.get("total") or 0)
        received = sum(len(p.records) for p in pages)
        if received != expected:
            raise ApiError(f"{path}: received {received} records but API reported total={expected}")
        return pages

    def _get_text(self, url: str) -> str:
        attempts = self.max_retries + 1
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
            else:
                if resp.status_code == 200:
                    try:
                        json.loads(resp.text)
                        return resp.text
                    except ValueError:
                        # The API occasionally answers with an HTML error page under load
                        last_error = "response body is not valid JSON"
                elif resp.status_code in RETRYABLE_STATUS:
                    last_error = f"HTTP {resp.status_code}"
                else:
                    raise ApiError(f"HTTP {resp.status_code} for {url}: {resp.text[:200]}")

            if attempt < attempts:
                wait = self.backoff * (2 ** (attempt - 1))
                log.warning("Attempt %d/%d failed (%s), retrying in %.0fs: %s", attempt, attempts, last_error, wait, url)
                self.sleep(wait)
        raise ApiError(f"Giving up after {attempts} attempts ({last_error}): {url}")

    @staticmethod
    def _parse(text: str, url: str) -> tuple[dict, list]:
        payload = json.loads(text)
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise ApiError(f"Unexpected payload shape from {url}")
        meta = payload[0]
        if "message" in meta:
            raise ApiError(f"API error for {url}: {meta['message']}")
        records = payload[1] if len(payload) > 1 and payload[1] is not None else []
        return meta, records
