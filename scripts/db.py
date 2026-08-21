#!/usr/bin/env python3
"""db.py - 数据库连接池（pgvector）"""
import psycopg2
import psycopg2.extras
from psycopg2 import pool
from contextlib import contextmanager

_pool: Optional[pool.SimpleConnectionPool] = None


def init_pool(config: dict, min_conn: int = 1, max_conn: int = 5):
    """初始化连接池"""
    global _pool
    _pool = pool.SimpleConnectionPool(
        min_conn, max_conn,
        host=config['host'],
        port=config['port'],
        user=config['user'],
        password=config['password'],
        dbname=config['dbname'],
    )


def close_pool():
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None


@contextmanager
def get_conn():
    """从池里取一个连接（自动归还）"""
    if _pool is None:
        raise RuntimeError('连接池未初始化，先调用 init_pool()')
    conn = _pool.getconn()
    try:
        yield conn
    finally:
        _pool.putconn(conn)


@contextmanager
def get_cursor(dict_cursor: bool = True):
    """快捷方式：取连接 + cursor"""
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor if dict_cursor else None)
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
