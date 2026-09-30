from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    return create_engine(
        s.database_url,
        pool_size=s.db_pool_size,
        pool_pre_ping=True,      # MariaDB drops idle connections; don't hand out dead ones
        pool_recycle=1800,
        isolation_level="READ COMMITTED",
        # Every stmt_* timestamp is UTC. This makes NOW()/CURRENT_TIMESTAMP defaults UTC too,
        # whatever timezone the server itself runs in. Convert to EAT only for display.
        connect_args={"init_command": "SET time_zone = '+00:00'"},
    )