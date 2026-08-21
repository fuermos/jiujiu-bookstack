#!/usr/bin/env python3
"""mcp_server.py - MCP stdio 服务器（mcp 2.0 兼容版）

12 个工具：list_books / get_book / search_books / semantic_search / get_chunks /
get_script / list_categories / get_random_chunk / get_book_stats / get_category_stats /
list_books_with_status / sql_query
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
import yaml
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server

# mcp_types 是 mcp 2.0 的低层类型库, 在 mcp 1.x 中不存在
# 我们这里懒导入: 只在需要时（运行 MCP stdio server）才需要它
try:
    from mcp_types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        PaginatedRequestParams,
        TextContent,
        Tool as MCPTool,
    )
    MCP_V2 = True
except ImportError:
    MCP_V2 = False
    # 1.x fallback: 直接定义 (mcp 1.x 中 tool 是 mcp.types.Tool)
    from mcp.types import TextContent
    # 1.x 不支持 add_request_handler 接口, 本文件假设运行在 mcp 2.0+
    raise ImportError("mcp_server.py 要求 mcp >= 2.0 (请 pip install 'mcp>=2.0' 或重新构建 Docker 镜像)")

# 加载项目根目录的 .env
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger("jiujiu-mind-mcp")


def _load_config() -> dict:
    """加载配置：优先用 config_loader（支持 env 覆盖）"""
    try:
        from config_loader import load_config
        return load_config(str(PROJECT_ROOT / "config" / "config.yaml"))
    except Exception:
        pass
    # fallback: 自己解析
    cfg_path = PROJECT_ROOT / "config" / "config.yaml"
    if not cfg_path.exists():
        cfg_path = PROJECT_ROOT / "config" / "config.example.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    db = cfg.get("database", {})
    db["host"] = os.environ.get("DB_HOST", db.get("host", "localhost"))
    db["port"] = int(os.environ.get("DB_PORT", db.get("port", 15433)))
    db["user"] = os.environ.get("DB_USER", db.get("user", "admin"))
    db["password"] = os.environ.get("DB_PASSWORD", db.get("password", ""))
    db["dbname"] = os.environ.get("DB_NAME", db.get("dbname", "jiujiu_mind"))
    cfg["database"] = db
    # Embedding 也支持 env 覆盖
    emb = cfg.get("embedding", {})
    if "EMBEDDING_BASE_URL" in os.environ:
        emb["base_url"] = os.environ["EMBEDDING_BASE_URL"]
    if "EMBEDDING_MODEL" in os.environ:
        emb["model"] = os.environ["EMBEDDING_MODEL"]
    if "EMBEDDING_API_KEY" in os.environ:
        emb["api_key"] = os.environ["EMBEDDING_API_KEY"]
    cfg["embedding"] = emb
    return cfg


CONFIG = _load_config()
EMBEDDING_CFG = CONFIG.get("embedding", {})


def get_conn():
    return psycopg2.connect(
        host=CONFIG["database"]["host"],
        port=CONFIG["database"]["port"],
        user=CONFIG["database"]["user"],
        password=CONFIG["database"]["password"],
        dbname=CONFIG["database"]["dbname"],
    )


def embed_text(text: str) -> list[float]:
    base = EMBEDDING_CFG.get('base_url', 'http://localhost:1234/v1').rstrip('/')
    endpoint = '/embeddings' if base.endswith('/v1') else '/v1/embeddings'
    resp = requests.post(
        f"{base}{endpoint}",
        headers={"Content-Type": "application/json"},
        json={"model": EMBEDDING_CFG.get("model", "text-embedding-bge-m3"), "input": text},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    if data and isinstance(data[0], dict):
        return data[0].get("embedding", [])
    if data and isinstance(data[0], list):
        return data[0]
    return []


# ---------- 12 个工具实现 ----------

def tool_list_books(args, cur):
    category = args.get("category")
    limit = min(int(args.get("limit", 20)), 100)
    offset = int(args.get("offset", 0))
    if category:
        cur.execute("SELECT id, name, category FROM books WHERE category=%s ORDER BY id LIMIT %s OFFSET %s",
                    (category, limit, offset))
    else:
        cur.execute("SELECT id, name, category FROM books ORDER BY id LIMIT %s OFFSET %s", (limit, offset))
    return json.dumps([dict(r) for r in cur.fetchall()], ensure_ascii=False, indent=2)


def tool_get_book(args, cur):
    book_id = int(args["book_id"])
    cur.execute("SELECT * FROM books WHERE id=%s", (book_id,))
    row = cur.fetchone()
    return json.dumps(dict(row) if row else {"error": f"book_id={book_id} 不存在"},
                     ensure_ascii=False, indent=2, default=str)


def tool_search_books(args, cur):
    q = f"%{args['query']}%"
    limit = min(int(args.get("limit", 10)), 50)
    cur.execute("""SELECT id, name, category FROM books
                   WHERE name ILIKE %s OR summary ILIKE %s
                   ORDER BY id LIMIT %s""", (q, q, limit))
    return json.dumps([dict(r) for r in cur.fetchall()], ensure_ascii=False, indent=2)


def tool_semantic_search(args, cur):
    query = args["query"]
    top_k = min(int(args.get("top_k", 10)), 50)
    book_id = args.get("book_id")
    vec = embed_text(query)
    vec_str = "[" + ",".join(map(str, vec)) + "]"
    where = "WHERE c.book_id = %s" if book_id else ""
    params = (vec_str, vec_str, vec_str, top_k) if not book_id else (vec_str, int(book_id), vec_str, top_k)
    sql = f"""SELECT c.book_id, c.chapter_index, c.chunk_text,
                     1 - (cv.embedding <=> %s::vector) AS similarity
              FROM chunk_vectors cv JOIN chunks c ON c.id = cv.chunk_id
              {where}
              ORDER BY cv.embedding <=> %s::vector LIMIT %s"""
    cur.execute(sql, params)
    result = []
    for r in cur.fetchall():
        item = dict(r)
        if item.get("chunk_text"):
            item["chunk_text"] = item["chunk_text"][:200] + "..."
        result.append(item)
    return json.dumps(result, ensure_ascii=False, indent=2)


def tool_get_chunks(args, cur):
    book_id = int(args["book_id"])
    chapter = args.get("chapter")
    limit = min(int(args.get("limit", 5)), 100)
    if chapter is not None:
        cur.execute("SELECT id, chapter_index, chunk_text FROM chunks WHERE book_id=%s AND chapter_index=%s LIMIT %s",
                    (book_id, int(chapter), limit))
    else:
        cur.execute("SELECT id, chapter_index, chunk_text FROM chunks WHERE book_id=%s LIMIT %s", (book_id, limit))
    return json.dumps([dict(r) for r in cur.fetchall()], ensure_ascii=False, indent=2, default=str)


def tool_get_script(args, cur):
    book_id = int(args["book_id"])
    chapter = args.get("chapter")
    game_type = args.get("game_type", "v2_cyoa")
    if chapter is not None:
        cur.execute("SELECT chapter_index, game_type, script_json FROM game_scripts WHERE book_id=%s AND chapter_index=%s AND game_type=%s",
                    (book_id, int(chapter), game_type))
    else:
        cur.execute("SELECT chapter_index, game_type, script_json FROM game_scripts WHERE book_id=%s AND game_type=%s ORDER BY chapter_index",
                    (book_id, game_type))
    return json.dumps([dict(r) for r in cur.fetchall()], ensure_ascii=False, indent=2, default=str)


def tool_list_categories(args, cur):
    cur.execute("SELECT category, COUNT(*) AS n FROM books WHERE category IS NOT NULL GROUP BY category ORDER BY n DESC")
    return json.dumps([dict(r) for r in cur.fetchall()], ensure_ascii=False, indent=2)


def tool_get_random_chunk(args, cur):
    book_id = args.get("book_id")
    if book_id:
        cur.execute("SELECT book_id, chapter_index, chunk_text FROM chunks WHERE book_id=%s ORDER BY RANDOM() LIMIT 1",
                    (int(book_id),))
    else:
        cur.execute("SELECT book_id, chapter_index, chunk_text FROM chunks ORDER BY RANDOM() LIMIT 1")
    row = cur.fetchone()
    return json.dumps(dict(row) if row else {}, ensure_ascii=False, indent=2, default=str)


def tool_get_book_stats(args, cur):
    cur.execute("""SELECT
        (SELECT COUNT(*) FROM books) AS books,
        (SELECT COUNT(*) FROM chunks) AS chunks,
        (SELECT COUNT(*) FROM chunk_vectors) AS embeddings,
        (SELECT COUNT(*) FROM game_scripts) AS scripts,
        (SELECT COUNT(*) FROM book_mindmaps) AS mindmaps""")
    return json.dumps(dict(cur.fetchone()), ensure_ascii=False, indent=2)


def tool_get_category_stats(args, cur):
    cur.execute("""SELECT b.category, COUNT(*) AS books, COALESCE(SUM(c.cnt),0) AS chunks
                   FROM books b
                   LEFT JOIN (SELECT book_id, COUNT(*) AS cnt FROM chunks GROUP BY book_id) c ON c.book_id=b.id
                   WHERE b.category IS NOT NULL
                   GROUP BY b.category ORDER BY books DESC""")
    return json.dumps([dict(r) for r in cur.fetchall()], ensure_ascii=False, indent=2)


def tool_list_books_with_status(args, cur):
    missing = args["missing"]
    limit = min(int(args.get("limit", 20)), 100)
    where_map = {
        "summary": "summary IS NULL OR summary = ''",
        "category": "category IS NULL",
        "script": "id NOT IN (SELECT DISTINCT book_id FROM game_scripts)",
        "embedding": "id IN (SELECT book_id FROM chunks GROUP BY book_id HAVING COUNT(*) > COUNT(*) FILTER (WHERE id IN (SELECT chunk_id FROM chunk_vectors)))",
    }
    where = where_map.get(missing)
    if not where:
        return json.dumps({"error": f"unknown missing={missing}"})
    cur.execute(f"SELECT id, name, category FROM books WHERE {where} ORDER BY id LIMIT %s", (limit,))
    return json.dumps([dict(r) for r in cur.fetchall()], ensure_ascii=False, indent=2)


def tool_sql_query(args, cur):
    query = args["query"].strip()
    q_lower = query.lower().lstrip()
    if not (q_lower.startswith("select") or q_lower.startswith("with")):
        return json.dumps({"error": "只允许 SELECT/WITH 查询"})
    forbidden = ["insert", "update", "delete", "drop", "alter", "create", "truncate", "grant", "revoke"]
    for kw in forbidden:
        if f" {kw} " in f" {q_lower} ":
            return json.dumps({"error": f"禁止 {kw.upper()} 操作"})
    cur.execute(query, args.get("params"))
    cols = [d.name for d in cur.description] if cur.description else []
    rows = cur.fetchmany(int(args.get("limit", 50)))
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append({c: r.get(c) for c in cols})
        else:
            out.append(dict(zip(cols, r)))
    return json.dumps(out, ensure_ascii=False, indent=2, default=str)


HANDLERS = {
    "list_books": tool_list_books, "get_book": tool_get_book,
    "search_books": tool_search_books, "semantic_search": tool_semantic_search,
    "get_chunks": tool_get_chunks, "get_script": tool_get_script,
    "list_categories": tool_list_categories, "get_random_chunk": tool_get_random_chunk,
    "get_book_stats": tool_get_book_stats, "get_category_stats": tool_get_category_stats,
    "list_books_with_status": tool_list_books_with_status, "sql_query": tool_sql_query,
}


def _make_tool(name, description, properties, required=None):
    return MCPTool(
        name=name,
        description=description,
        inputSchema={"type": "object", "properties": properties, "required": required or []},
    )


TOOLS = [
    _make_tool("list_books", "列出书库（可按分类过滤）",
               {"category": {"type": "string"}, "limit": {"type": "integer"}, "offset": {"type": "integer"}}),
    _make_tool("get_book", "拿单本书的完整元数据（含 summary）",
               {"book_id": {"type": "integer", "description": "书的 ID"}}, required=["book_id"]),
    _make_tool("search_books", "按书名/摘要关键词搜索",
               {"query": {"type": "string"}, "limit": {"type": "integer"}}, required=["query"]),
    _make_tool("semantic_search", "语义搜索：把 query 嵌入向量，召回最相关的 chunks",
               {"query": {"type": "string"}, "top_k": {"type": "integer"}, "book_id": {"type": "integer"}},
               required=["query"]),
    _make_tool("get_chunks", "拿指定书的 chunks",
               {"book_id": {"type": "integer"}, "chapter": {"type": "integer"}, "limit": {"type": "integer"}},
               required=["book_id"]),
    _make_tool("get_script", "拿指定书的剧本",
               {"book_id": {"type": "integer"}, "chapter": {"type": "integer"},
                "game_type": {"type": "string"}}, required=["book_id"]),
    _make_tool("list_categories", "列出所有分类 + 每类书数", {}),
    _make_tool("get_random_chunk", "随机拿一个 chunk",
               {"book_id": {"type": "integer", "description": "可选，限定某本书"}}),
    _make_tool("get_book_stats", "全库统计（书数/chunks/embeddings/scripts）", {}),
    _make_tool("get_category_stats", "分类统计（每类书数+chunks数）", {}),
    _make_tool("list_books_with_status", "按处理状态过滤（缺summary/category/script/embedding）",
               {"missing": {"type": "string", "enum": ["summary", "category", "script", "embedding"]},
                "limit": {"type": "integer"}}, required=["missing"]),
    _make_tool("sql_query", "通用 SQL 查询（仅 SELECT/WITH，拒写）",
               {"query": {"type": "string"}, "params": {"type": "array"},
                "limit": {"type": "integer"}}, required=["query"]),
]


# ---------- MCP 2.0 Server 主循环 ----------

app = Server("jiujiu-mind-mcp")


async def _handle_list_tools(*args) -> ListToolsResult:
    """MCP 2.0 SDK 调用时传 (ctx, params)，但模块级函数会被当作 method 调用
    传 (self, ctx, params) 或 (ctx, params) 或仅 (params)。
    只取最后一个参数是 params。"""
    params = args[-1] if args else None
    return ListToolsResult(tools=TOOLS)


async def _handle_call_tool(*args) -> CallToolResult:
    """同上：兼容多种调用方式"""
    params = args[-1] if args else None
    if params is None:
        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": "no params"}, ensure_ascii=False))], isError=True)
    name = params.name
    arguments = params.arguments or {}
    handler = HANDLERS.get(name)
    if not handler:
        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False))], isError=True)
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            result_text = handler(arguments, cur)
        conn.commit()
        return CallToolResult(content=[TextContent(type="text", text=result_text)])
    except Exception as e:
        conn.rollback()
        log.exception(f"工具 {name} 失败")
        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))], isError=True)
    finally:
        conn.close()


app.add_request_handler("tools/list", PaginatedRequestParams, _handle_list_tools)
app.add_request_handler("tools/call", CallToolRequestParams, _handle_call_tool)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())