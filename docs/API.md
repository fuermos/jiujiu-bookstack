# MCP 12 工具 API 文档

启动 `python scripts/mcp_server.py` 后，对 AI Agent 暴露以下 12 个工具。

## 通用约定

- 所有工具返回 JSON 字符串
- 错误以 `❌` 开头
- `book_id` 是 `books` 表的主键
- `chapter_index` 是 0-based 章节索引
- `game_type` 形如 `v2_cyoa` / `v2_mixed` / `cyoa`

---

## 1. `list_books(category?, limit, offset)`

列出书库的书。

**参数**：
- `category`（可选）：按分类过滤，如 `"文学"` / `"科学"`
- `limit`（默认 20）：最多返回 N 本
- `offset`（默认 0）：跳过前 N 本

**返回**：
```json
[
  {"id": 384, "name": "敢于脆弱", "category": "成长心理", "summary_generated_at": "..."}
]
```

---

## 2. `get_book(book_id)`

拿指定书的完整元数据（含 summary）。

**返回**：
```json
{
  "id": 384,
  "name": "敢于脆弱_吉娜维芙·阿弗里伊尔",
  "category": "成长心理",
  "total_scenes": 17,
  "summary": "《敢于脆弱》是法国心理学家...",
  "summary_generated_at": "2026-08-21 13:25:26"
}
```

---

## 3. `search_books(query, limit)`

按书名/摘要关键词搜索（ILIKE 模糊匹配）。

---

## 4. `semantic_search(query, top_k, book_id?)`

语义搜索：把 query 嵌入向量，召回最相似的 chunks。

**参数**：
- `query`：自然语言查询，如 `"脆弱与力量的关系"`
- `top_k`（默认 10）：返回前 N 个
- `book_id`（可选）：限定只在某本书里搜

**返回**：
```json
[
  {"chunk_id": 332614, "book_id": 384, "name": "敢于脆弱...", "preview": "...", "similarity": 0.638}
]
```

---

## 5. `get_chunks(book_id, chapter?, limit)`

拿指定书的 chunks（按 chapter_index 排序）。

---

## 6. `get_script(book_id, chapter?, game_type?)`

拿指定书的剧本。

**参数**：
- `book_id`
- `chapter`（可选）：限定某章节
- `game_type`（可选）：如 `v2_cyoa` / `v2_mixed`

---

## 7. `list_categories()`

列出所有分类 + 每类有多少本书。

---

## 8. `get_random_chunk(book_id?)`

随机拿一个 chunk（适合给 agent 看个样本）。

---

## 9. `get_book_stats()`

全库统计。

**返回**：
```json
{
  "total_books": 532,
  "total_chunks": 670013,
  "embedded_chunks": 670013,
  "total_scripts": 6197,
  "books_with_scripts": 532,
  "books_with_chunks": 532
}
```

---

## 10. `get_category_stats()`

按分类统计。

---

## 11. `list_books_with_status(missing, limit)`

按处理状态过滤（缺 summary / 缺 category / 缺 script / 缺 embedding）。

**参数**：
- `missing`：必填，可选值 `"summary"` / `"category"` / `"script"` / `"embedding"`

---

## 12. `sql_query(query, params?, limit)`

通用 SQL 查询（**只允许 SELECT，自动拦截写操作**）。

**安全机制**：
- 强制 SELECT / WITH 开头
- 黑名单正则：INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/GRANT/REVOKE
- 5 秒连接超时
- 30 秒查询超时

**示例**：
```python
sql_query(query="SELECT b.id, b.name FROM books b WHERE b.category=%s", params=["文学"], limit=10)
```

---

## 🔌 集成示例

### Claude Desktop

编辑 `claude_desktop_config.json`：
```json
{
  "mcpServers": {
    "jiujiu-bookstack": {
      "command": "python",
      "args": ["/path/to/jiujiu-bookstack/scripts/mcp_server.py"]
    }
  }
}
```

### Cursor IDE

Settings → MCP → Add Server：
- Name: `jiujiu-bookstack`
- Command: `python /path/to/scripts/mcp_server.py`

### Claude Code (CLI)

```bash
claude mcp add jiujiu-bookstack -- python /path/to/scripts/mcp_server.py
```

---

## 🛡️ 安全提醒

`sql_query` 即使通过白名单 + 黑名单做了防护，**仍建议只对受信 Agent 暴露**。
生产环境可：
- 创建只读 PG 角色：`CREATE USER reader PASSWORD 'xxx'; GRANT SELECT ON ALL TABLES IN SCHEMA public TO reader;`
- 在 `config.yaml` 里用 reader 角色连接
