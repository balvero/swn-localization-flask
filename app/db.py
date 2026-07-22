"""Shared Postgres connection pool.

Mirrors netlify/functions/_db.js's getPool()/getProdPool() split, but a
Flask process is long-lived (unlike a serverless function invocation), so
this uses a real connection pool instead of a lazily-created singleton
Pool object — same idea, different lifecycle.

DATABASE_URL is the "real" database this server talks to for normal
requests. DATABASE_URL_PROD + USE_PROD_DB are kept only for parity with
the Import tab's local-dev-writes-straight-to-production behavior; unlike
the Netlify version, there's no LOCAL_DEV gate here, since this project
doesn't have a separate "local dev vs. deployed" runtime distinction yet —
whatever DATABASE_URL points at IS the database this process uses.
"""

import os
from contextlib import contextmanager
from psycopg2.pool import ThreadedConnectionPool

_pool = None
_prod_pool = None


def _make_pool(url):
    if not url:
        raise RuntimeError("DATABASE_URL is not set — see .env.example")
    # Local Docker Postgres doesn't speak SSL; Supabase requires it. A
    # connection string with "sslmode=require" (or similar) already carries
    # this, so no separate ssl flag is needed the way the JS pg client needed
    # one — psycopg2 reads sslmode straight from the URL/DSN.
    return ThreadedConnectionPool(minconn=1, maxconn=10, dsn=url)


def using_prod_db():
    return os.environ.get("USE_PROD_DB") == "true"


def _get_pool():
    global _pool
    if using_prod_db():
        return _get_prod_pool()
    if _pool is None:
        _pool = _make_pool(os.environ.get("DATABASE_URL"))
    return _pool


def _get_prod_pool():
    global _prod_pool
    if _prod_pool is None:
        _prod_pool = _make_pool(os.environ.get("DATABASE_URL_PROD"))
    return _prod_pool


@contextmanager
def get_cursor(prod=False, commit=True):
    """Checks out a connection, yields a cursor, returns the connection to
    the pool afterward. commit=True autocommits after the block (matching
    the JS side's pool.query() behavior, which isn't wrapped in an explicit
    transaction); pass commit=False and call conn.commit()/rollback()
    yourself for multi-statement transactions (see import_translations_csv.py
    and import_page.py, which mirror the JS transaction blocks)."""
    pool = _get_prod_pool() if prod else _get_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        yield conn, cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
