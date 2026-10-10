"""DuckDB connection and schema helpers."""
from __future__ import annotations

from pathlib import Path

import duckdb

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def connect(database: str = ":memory:"):
    if database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(database)


def run_sql_file(con, path: Path) -> None:
    """Execute a .sql file statement by statement (statements separated by ';')."""
    text = Path(path).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.strip().startswith("--")]
    for statement in "\n".join(lines).split(";"):
        if statement.strip():
            con.execute(statement)


def create_schema(con) -> None:
    run_sql_file(con, SQL_DIR / "schema.sql")


def query_dicts(con, sql: str, params=None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
