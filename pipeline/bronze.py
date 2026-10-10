"""Bronze layer: fetch from the API and store every response page verbatim."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from .api import RawPage, WorldBankClient
from .config import Config

log = logging.getLogger(__name__)

COUNTRY_METADATA_KEY = "country_metadata"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def raw_response_id(request_url: str, text: str) -> str:
    return hashlib.sha256(f"{request_url}\n{text}".encode("utf-8")).hexdigest()[:24]


def save_page(con, run_id: str, request_key: str, page: RawPage) -> tuple[str, bool]:
    """Insert a page unless the exact same response is already stored. Returns (id, is_new)."""
    rid = raw_response_id(page.request_url, page.text)
    if con.execute("SELECT 1 FROM bronze_raw_response WHERE raw_response_id = ?", [rid]).fetchone():
        return rid, False
    con.execute(
        "INSERT INTO bronze_raw_response VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            rid, run_id, request_key, page.request_url, page.page, utc_now(),
            hashlib.sha256(page.text.encode("utf-8")).hexdigest(), page.text,
        ],
    )
    return rid, True


def ingest(con, client: WorldBankClient, cfg: Config, run_id: str) -> tuple[list[str], int]:
    """Fetch country metadata and every configured indicator.

    Returns the raw_response_ids that make up this run's snapshot and how many pages were new.
    All API calls happen before anything is written, so a failed run leaves bronze untouched.
    """
    requests_to_make = [(COUNTRY_METADATA_KEY, lambda: client.countries(cfg.countries))]
    for ind in cfg.indicators:
        requests_to_make.append(
            (f"indicator:{ind.code}",
             lambda code=ind.code: client.indicator(cfg.countries, code, cfg.year_start, cfg.year_end))
        )

    fetched: list[tuple[str, RawPage]] = []
    for key, fetch in requests_to_make:
        pages = fetch()
        log.info("Fetched %s: %d page(s), %d record(s)", key, len(pages), sum(len(p.records) for p in pages))
        fetched.extend((key, p) for p in pages)

    ids, new_count = [], 0
    for key, page in fetched:
        rid, is_new = save_page(con, run_id, key, page)
        ids.append(rid)
        new_count += int(is_new)
    log.info("Bronze: %d page(s) in snapshot, %d new", len(ids), new_count)
    return ids, new_count


def latest_snapshot_ids(con) -> list[str]:
    """Most recent stored response for every request URL/page (used to rebuild without the API)."""
    rows = con.execute(
        """
        SELECT raw_response_id FROM (
            SELECT raw_response_id,
                   ROW_NUMBER() OVER (PARTITION BY request_url, page ORDER BY fetched_at_utc DESC) AS rn
            FROM bronze_raw_response
        ) t WHERE rn = 1
        """
    ).fetchall()
    return [r[0] for r in rows]
