import os
import logging
import time
from contextlib import contextmanager
from psycopg2.pool import ThreadedConnectionPool
import psycopg2

logger = logging.getLogger(__name__)

_pool = None

def _get_db_params() -> dict:
    return {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": os.environ.get("DB_PORT", "5432"),
        "dbname": os.environ.get("DB_NAME", "omnidb"),
        "user": os.environ.get("DB_USER", "omni_admin"),
        "password": os.environ.get("DB_PASSWORD", ""),
    }

def get_pool(minconn: int = 2, maxconn: int = 10) -> ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        params = _get_db_params()
        backoff = 2
        for attempt in range(10):
            try:
                _pool = ThreadedConnectionPool(minconn, maxconn, **params)
                logger.info(f"DB pool criado ({minconn}-{maxconn} conns) -> {params['host']}:{params['port']}/{params['dbname']}")
                return _pool
            except psycopg2.OperationalError as e:
                logger.error(f"DB pool falhou (tentativa {attempt+1}): {e}. Retry em {backoff}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
        raise RuntimeError("Falha ao criar pool de conexões após 10 tentativas")
    return _pool

@contextmanager
def get_conn():
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)

def close_pool():
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        logger.info("DB pool fechado.")
        _pool = None
