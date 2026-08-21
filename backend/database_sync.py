# database_sync.py
import psycopg2
from psycopg2 import pool, extras
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
import logging
from config import settings

logger = logging.getLogger(__name__)

_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None


def get_db_pool() -> psycopg2.pool.SimpleConnectionPool:
    """Get or create the sync connection pool"""
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=settings.DATABASE_URL
        )
        logger.info("Sync database pool created")
    return _pool


@contextmanager
def get_db_connection():
    """Get a database connection from the pool"""
    pool = get_db_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def execute_query(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """Execute a query and return results as list of dicts"""
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchall()


def execute_one(query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    """Execute a query and return one result"""
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchone()


def execute_update(query: str, params: tuple = ()) -> int:
    """Execute an update/insert and return row count"""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.rowcount