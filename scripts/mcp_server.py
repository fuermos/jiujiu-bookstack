#!/usr/bin/env python3
"""mcp_server.py - MCP stdio server，暴露 12 个工具给 AI Agent

用法:
    python mcp_server.py  # stdio 模式 (默认, 适合 Claude Desktop / Cursor)

配置 Claude Desktop:
    {
      "mcpServers": {
        "jiujiu-bookstack": {
          "command": "python",
          "args": ["/path/to/jiujiu-bookstack/scripts/mcp_server.py"]
        }
      }
    }
"""
import asyncio
import json
import logging
import re
import os
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


# ====== 配置 ======
PG_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', '15433')),
    'user': os.environ.get('DB_USER', 'admin'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'dbname': os.environ.get('DB_NAME', 'jiujiu_mind'),
}

EMBEDDING_URL = os.environ.get('EMBEDDING_URL', 'http://localhost:1234/v1/embeddings')
EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'text-embedding-bge-m3')

# 写操作黑名单
WRITE_KEYWORDS = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|VACUUM|REINDEX|CLUSTER|LOCK|NOTIFY|LISTEN|UNLISTEN|RESET)\b',
    re.IGNORECASE,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=__import__('sys').stderr)
log = logging.getLogger('jiujiu-bookstack-mcp')

app = Server('jiujiu-bookstack')


def get_conn():
    return psycopg2.connect(**PG_CONFIG)


def json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


# ====== MCP 工具列表 ======
TOOLS = [
    Tool(name='list_books', description='列出书库的书，可按分类过滤', inputSchema={
        'type': 'object',
        'properties': {
            'category': {'type': 'string', 'description': '分类名（如"文学"）'},
            'limit': {'type': 'integer', 'default': 20},
            'offset': {'type': 'integer', 'default': 0},
        },
    }),
    Tool(name='get_book', description='拿指定书的完整元数据', inputSchema={
        'type': 'object',
        'properties': {'book_id': {'type': 'integer', 'description': '书的 id'}},
        'required': ['book_id'],
    }),
    Tool(name='search_books', description='按书名/摘要关键词搜索', inputSchema={
        'type': 'object',
        'properties': {
            'query': {'type': 'string', 'description': '搜索词'},
            'limit': {'type': 'integer', 'default': 10},
        },
        'required': ['query'],
    }),
    Tool(name='semantic_search', description='语义搜索：把 query 嵌入向量，召回最相似的 chunks', inputSchema={
        'type': 'object',
        'properties': {
            'query': {'type': 'string'},
            'top_k': {'type': 'integer', 'default': 10},
            'book_id': {'type': 'integer', 'description': '限定只在某本书里搜'},
        },
        'required': ['query'],
    }),
    Tool(name='get_chunks', description='拿指定书的 chunks', inputSchema={
        'type': 'object',
        'properties': {
            'book_id': {'type': 'integer'},
            'chapter': {'type': 'integer', 'description': '可选，限定某 chapter'},
            'limit': {'type': 'integer', 'default': 5},
        },
        'required': ['book_id'],
    }),
    Tool(name='get_script', description='拿指定书的剧本', inputSchema={
        'type': 'object',
        'properties': {
            'book_id': {'type': 'integer'},
            'chapter': {'type': 'integer'},
            'game_type': {'type': 'string'},
        },
        'required': ['book_id'],
    }),
    Tool(name='list_categories', description='列出所有分类', inputSchema={'type': 'object', 'properties': {}}),
    Tool(name='get_random_chunk', description='随机拿一个 chunk', inputSchema={
        'type': 'object',
        'properties': {'book_id': {'type': 'integer'}},
    }),
    Tool(name='get_book_stats', description='全库统计', inputSchema={'type': 'object', 'properties': {}}),
    Tool(name='get_category_stats', description='分类统计', inputSchema={'type': 'object', 'properties': {}}),
    Tool(name='list_books_with_status', description='按处理状态过滤', inputSchema={
        'type': 'object',
        'properties': {
            'missing': {'type': 'string', 'enum': ['summary', 'category', 'script', 'embedding']},
            'limit': {'type': 'integer', 'default': 20},
        },
        'required': ['missing'],
    }),
    Tool(name='sql_query', description='只读 SQL 查询（拒写操作）', inputSchema={
        'type': 'object',
        'properties': {
            'query': {'type': 'string'},
            'params': {'type': 'array', 'items': {'type': 'string'}},
            'limit': {'type': 'integer', 'default': 50},
        },
        'required': ['query'],
    }),
]


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                handler = HANDLERS.get(name)
                if not handler:
                    return [TextContent(type='text', text=f'❌ 未知工具: {name}')]
                return await handler(cur, arguments)
    except Exception as e:
        log.exception(f'工具 {name} 调用失败')
        return [TextContent(type='text', text=f'❌ {type(e).__name__}: {e}')]


# ====== 工具实现 ======

async def list_books(cur, args):
    category = args.get('category')
    limit = args.get('limit', 20)
    offset = args.get('offset', 0)
    if category:
        cur.execute(
            'SELECT id, name, category, summary_generated_at FROM books WHERE category = %s ORDER BY id LIMIT %s OFFSET %s',
            (category, limit, offset),
        )
    else:
        cur.execute(
            'SELECT id, name, category, summary_generated_at FROM books ORDER BY id LIMIT %s OFFSET %s',
            (limit, offset),
        )
    rows = cur.fetchall()
    return [TextContent(type='text', text=json_dumps([dict(r) for r in rows]))]


async def get_book(cur, args):
    book_id = args['book_id']
    cur.execute('SELECT * FROM books WHERE id = %s', (book_id,))
    row = cur.fetchone()
    if not row:
        return [TextContent(type='text', text=f'❌ book_id={book_id} 不存在')]
    return [TextContent(type='text', text=json_dumps(dict(row)))]


async def search_books(cur, args):
    query = args['query']
    limit = args.get('limit', 10)
    cur.execute(
        "SELECT id, name, category, LEFT(summary, 100) AS summary_preview FROM books WHERE name ILIKE %s OR summary ILIKE %s ORDER BY id LIMIT %s",
        (f'%{query}%', f'%{query}%', limit),
    )
    rows = cur.fetchall()
    return [TextContent(type='text', text=json_dumps([dict(r) for r in rows]))]


async def semantic_search(cur, args):
    query = args['query']
    top_k = args.get('top_k', 10)
    book_id = args.get('book_id')

    # 1. embed query
    resp = requests.post(EMBEDDING_URL, json={'model': EMBEDDING_MODEL, 'input': query}, timeout=10)
    resp.raise_for_status()
    query_vec = resp.json()['data'][0]['embedding']

    # 2. 召回
    book_filter = 'AND c.book_id = %s' if book_id else ''
    sql = f'''
        SELECT c.id AS chunk_id, c.book_id, b.name, c.chapter_index,
               LEFT(c.chunk_text, 300) AS preview,
               1 - (v.embedding <=> %s::vector) AS similarity
        FROM chunks c
        JOIN chunk_vectors v ON v.chunk_id = c.id
        JOIN books b ON b.id = c.book_id
        WHERE TRUE {book_filter}
        ORDER BY v.embedding <=> %s::vector
        LIMIT %s
    '''
    params = [query_vec] + ([book_id] if book_id else []) + [query_vec, top_k]
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [TextContent(type='text', text=json_dumps([dict(r) for r in rows]))]


async def get_chunks(cur, args):
    book_id = args['book_id']
    chapter = args.get('chapter')
    limit = args.get('limit', 5)
    if chapter is not None:
        cur.execute(
            'SELECT id, chapter_index, LEFT(chunk_text, 500) AS preview FROM chunks WHERE book_id = %s AND chapter_index = %s ORDER BY id LIMIT %s',
            (book_id, chapter, limit),
        )
    else:
        cur.execute(
            'SELECT id, chapter_index, LEFT(chunk_text, 500) AS preview FROM chunks WHERE book_id = %s ORDER BY id LIMIT %s',
            (book_id, limit),
        )
    rows = cur.fetchall()
    return [TextContent(type='text', text=json_dumps([dict(r) for r in rows]))]


async def get_script(cur, args):
    book_id = args['book_id']
    chapter = args.get('chapter')
    game_type = args.get('game_type')
    sql = 'SELECT id, game_type, total_scenes, status, script_json FROM game_scripts WHERE book_id = %s'
    params = [book_id]
    if chapter is not None:
        sql += ' AND chapter_index = %s'
        params.append(chapter)
    if game_type:
        sql += ' AND game_type = %s'
        params.append(game_type)
    sql += ' ORDER BY id'
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [TextContent(type='text', text=json_dumps([dict(r) for r in rows]))]


async def list_categories(cur, args):
    cur.execute('SELECT category, COUNT(*) AS count FROM books WHERE category IS NOT NULL GROUP BY category ORDER BY count DESC')
    rows = cur.fetchall()
    return [TextContent(type='text', text=json_dumps([dict(r) for r in rows]))]


async def get_random_chunk(cur, args):
    book_id = args.get('book_id')
    if book_id:
        cur.execute('SELECT id, chunk_text FROM chunks WHERE book_id = %s ORDER BY RANDOM() LIMIT 1', (book_id,))
    else:
        cur.execute('SELECT id, book_id, chunk_text FROM chunks ORDER BY RANDOM() LIMIT 1')
    row = cur.fetchone()
    if not row:
        return [TextContent(type='text', text='❌ 无 chunks')]
    return [TextContent(type='text', text=json_dumps(dict(row)))]


async def get_book_stats(cur, args):
    cur.execute('''
        SELECT
            (SELECT COUNT(*) FROM books) AS total_books,
            (SELECT COUNT(*) FROM chunks) AS total_chunks,
            (SELECT COUNT(*) FROM chunk_vectors) AS embedded_chunks,
            (SELECT COUNT(*) FROM game_scripts) AS total_scripts,
            (SELECT COUNT(DISTINCT book_id) FROM game_scripts) AS books_with_scripts
    ''')
    row = cur.fetchone()
    return [TextContent(type='text', text=json_dumps(dict(row)))]


async def get_category_stats(cur, args):
    cur.execute('''
        SELECT b.category,
               COUNT(DISTINCT b.id) AS book_count,
               COUNT(c.id) AS chunk_count
        FROM books b
        LEFT JOIN chunks c ON c.book_id = b.id
        WHERE b.category IS NOT NULL
        GROUP BY b.category ORDER BY book_count DESC
    ''')
    rows = cur.fetchall()
    return [TextContent(type='text', text=json_dumps([dict(r) for r in rows]))]


async def list_books_with_status(cur, args):
    missing = args['missing']
    limit = args.get('limit', 20)

    if missing == 'summary':
        cur.execute("SELECT id, name FROM books WHERE summary IS NULL OR summary = '' ORDER BY id LIMIT %s", (limit,))
    elif missing == 'category':
        cur.execute("SELECT id, name FROM books WHERE category IS NULL OR category = '' ORDER BY id LIMIT %s", (limit,))
    elif missing == 'script':
        cur.execute('''SELECT b.id, b.name FROM books b
                       WHERE NOT EXISTS (SELECT 1 FROM game_scripts g WHERE g.book_id = b.id)
                       ORDER BY b.id LIMIT %s''', (limit,))
    elif missing == 'embedding':
        cur.execute('''SELECT b.id, b.name FROM books b
                       WHERE EXISTS (SELECT 1 FROM chunks c WHERE c.book_id = b.id)
                         AND EXISTS (
                           SELECT 1 FROM chunks c
                           WHERE c.book_id = b.id
                             AND NOT EXISTS (SELECT 1 FROM chunk_vectors v WHERE v.chunk_id = c.id)
                         )
                       ORDER BY b.id LIMIT %s''', (limit,))
    rows = cur.fetchall()
    return [TextContent(type='text', text=json_dumps([dict(r) for r in rows]))]


async def sql_query(cur, args):
    query = args['query'].strip()
    params = args.get('params')
    limit = args.get('limit', 50)

    if WRITE_KEYWORDS.search(query):
        return [TextContent(type='text', text='❌ 拒绝: 只允许 SELECT, 检测到写操作关键字')]
    if not query.upper().lstrip().startswith('SELECT') and not query.upper().lstrip().startswith('WITH'):
        return [TextContent(type='text', text='❌ 拒绝: 必须以 SELECT/WITH 开头')]

    if 'LIMIT' not in query.upper():
        query += f'\nLIMIT {limit}'

    if params:
        cur.execute(query, params)
    else:
        cur.execute(query)

    if cur.description is None:
        return [TextContent(type='text', text='✅ 执行成功 (无返回)')]

    cols = [d.name for d in cur.description]
    rows = cur.fetchall()

    if rows and isinstance(rows[0], dict):
        data = [{k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in r.items()} for r in rows]
    else:
        data = [dict(zip(cols, r)) for r in rows]
    return [TextContent(type='text', text=json_dumps(data))]


HANDLERS = {
    'list_books': list_books,
    'get_book': get_book,
    'search_books': search_books,
    'semantic_search': semantic_search,
    'get_chunks': get_chunks,
    'get_script': get_script,
    'list_categories': list_categories,
    'get_random_chunk': get_random_chunk,
    'get_book_stats': get_book_stats,
    'get_category_stats': get_category_stats,
    'list_books_with_status': list_books_with_status,
    'sql_query': sql_query,
}


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == '__main__':
    asyncio.run(main())
