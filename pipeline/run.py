"""Orchestration: bronze -> silver -> data quality -> gold, with a run log."""
from __future__ import annotations

import logging
import uuid

from . import bronze, gold, quality, silver
from .api import WorldBankClient
from .bronze import utc_now
from .config import Config
from .db import connect, create_schema

log = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    pass


def run(cfg: Config, client: WorldBankClient | None = None, con=None, rebuild_only: bool = False) -> dict:
    """Full run (fetch from the API) or, with rebuild_only=True, reprocess what is already in bronze."""
    owns_connection = con is None
    con = con or connect(cfg.database)
    create_schema(con)
    run_id = utc_now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    command = "rebuild" if rebuild_only else "run"
    con.execute(
        "INSERT INTO meta_pipeline_run (run_id, command, started_at_utc, status) VALUES (?, ?, ?, 'running')",
        [run_id, command, utc_now()],
    )
    log.info("Run %s started (%s)", run_id, command)

    summary = {"run_id": run_id, "raw_pages_new": 0}
    try:
        if rebuild_only:
            raw_ids = bronze.latest_snapshot_ids(con)
        else:
            raw_ids, summary["raw_pages_new"] = bronze.ingest(
                con, client or WorldBankClient.from_config(cfg), cfg, run_id
            )

        stats = silver.load(con, cfg, raw_ids)
        summary.update(
            facts_inserted=stats.facts_inserted,
            facts_updated=stats.facts_updated,
            facts_unchanged=stats.facts_unchanged,
        )

        results = quality.run_checks(con, cfg, run_id)
        failed = quality.errors(results)
        summary["dq_warnings"] = sum(1 for r in results if not r.passed and r.severity == "warn")
        if failed:
            raise PipelineError(f"Data-quality errors, gold not refreshed: {[r.name for r in failed]}")

        gold.build_views(con)
        gold.export_reports(con, cfg.reports_dir, run_id)
    except Exception as e:
        _finish(con, run_id, "failed", summary, str(e))
        log.error("Run %s failed: %s", run_id, e)
        raise
    else:
        _finish(con, run_id, "success", summary, "")
        log.info("Run %s succeeded: %s", run_id, summary)
        return summary
    finally:
        if owns_connection:
            con.close()


def _finish(con, run_id: str, status: str, s: dict, message: str) -> None:
    con.execute(
        """UPDATE meta_pipeline_run
           SET finished_at_utc = ?, status = ?, raw_pages_new = ?, facts_inserted = ?,
               facts_updated = ?, facts_unchanged = ?, message = ?
           WHERE run_id = ?""",
        [utc_now(), status, s.get("raw_pages_new"), s.get("facts_inserted"), s.get("facts_updated"),
         s.get("facts_unchanged"), message[:2000], run_id],
    )
