import asyncpg
from typing import Optional
from contextlib import asynccontextmanager
from config import settings

_db_pool: Optional[asyncpg.pool.Pool] = None


async def get_db_pool() -> asyncpg.pool.Pool:
    """Get or create database connection pool"""
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(
            settings.DATABASE_URL,
            min_size=1,
            max_size=10,
            command_timeout=60
        )
        print("Database connection pool created")
    return _db_pool


@asynccontextmanager
async def get_db_connection():
    """Get a database connection from the pool"""
    pool = await get_db_pool()
    async with pool.acquire() as connection:
        yield connection


async def close_db_pool():
    """Close database connection pool"""
    global _db_pool
    if _db_pool:
        await _db_pool.close()
        _db_pool = None
        print("Database connections closed")