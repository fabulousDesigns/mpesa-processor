"""Integration tests run against a REAL MariaDB when TEST_DATABASE_URL points at one.
The fixture creates a throwaway database, loads stub core tables + every sql/*.sql
migration in order, and drops it afterwards. No MariaDB -> DB tests skip."""
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
SERVER_URL = os.getenv("TEST_DATABASE_URL",
                       "mysql+pymysql://root@localhost/?unix_socket=/run/mysqld/mysqld.sock&charset=utf8mb4")


def _run_sql_file(conn, path: Path):
    body = "\n".join(l for l in path.read_text().splitlines() if not l.strip().startswith("--"))
    for stmt in body.split(";"):
        if stmt.strip():
            conn.execute(text(stmt))


@pytest.fixture
def db_engine():
    try:
        server = create_engine(SERVER_URL)
        with server.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"MariaDB not reachable: {e}")
    name = f"stmt_it_{uuid.uuid4().hex[:8]}"
    with server.begin() as c:
        c.execute(text(f"CREATE DATABASE {name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    url = SERVER_URL.replace("/?", f"/{name}?")
    engine = create_engine(url, isolation_level="READ COMMITTED",
                           connect_args={"init_command": "SET time_zone = '+00:00'"})
    with engine.begin() as c:
        _run_sql_file(c, ROOT / "tests" / "sql" / "000_stub_core_tables.sql")
        for f in sorted((ROOT / "sql").glob("[0-9][0-9][0-9]_*.sql")):
            if not f.name.endswith(".down.sql"):
                _run_sql_file(c, f)
    yield engine
    engine.dispose()
    with server.begin() as c:
        c.execute(text(f"DROP DATABASE {name}"))
    server.dispose()