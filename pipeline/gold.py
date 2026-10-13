"""Gold layer: analytics views (sql/gold/*.sql) and CSV exports of them."""
from __future__ import annotations

import csv
import logging
from decimal import Decimal
from pathlib import Path

from .db import SQL_DIR, run_sql_file

log = logging.getLogger(__name__)

# view -> exported file name
REPORTS = {
    "gold_missing_values": "missing_values.csv",
    "gold_coverage_summary": "coverage_summary.csv",
    "gold_yoy_change": "yoy_change.csv",
    "gold_latest_comparison": "latest_comparison.csv",
    "gold_income_group_by_year": "income_group_by_year.csv",
}


def build_views(con) -> None:
    for path in sorted((SQL_DIR / "gold").glob("*.sql")):
        run_sql_file(con, path)
    log.info("Gold views refreshed")


def export_reports(con, reports_dir: str, run_id: str | None = None) -> list[Path]:
    out = Path(reports_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for view, filename in REPORTS.items():
        written.append(_write_csv(con, f"SELECT * FROM {view}", [], out / filename))
    if run_id:
        written.append(_write_csv(
            con,
            "SELECT check_name, severity, status, failing_rows, details FROM meta_dq_result "
            "WHERE run_id = ? ORDER BY severity, check_name",
            [run_id],
            out / "data_quality_results.csv",
        ))
    log.info("Wrote %d report(s) to %s", len(written), out)
    return written


def _write_csv(con, sql: str, params: list, path: Path) -> Path:
    cur = con.execute(sql, params)
    header = [d[0] for d in cur.description]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in cur.fetchall():
            writer.writerow([_fmt(v) for v in row])
    return path


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(round(v, 6))
    return v
