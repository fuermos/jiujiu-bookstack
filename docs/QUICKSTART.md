# 5 分钟上手指南

## 准备清单

- [ ] Python 3.10+
- [ ] PostgreSQL 14+ (带 pgvector 扩展)
- [ ] 一个大模型 API Key（Anthropic / OpenAI / 国内大模型）
- [ ] 一个 embedding 服务（本地 LM Studio / OpenAI / 其他 OpenAI 兼容）

## 步骤 1: 安装

```bash
git clone https://github.com/<your-name>/jiujiu-bookstack.git
cd jiujiu-bookstack
pip install -r requirements.txt
```

## 步骤 2: 数据库初始化

### 方式 A: Docker

```yaml
# docker-compose.yml
version: '3'
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_PASSWORD: your-password
      POSTGRES_DB: jiujiu_mind
    ports:
      - "15433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

```bash
docker-compose up -d
```

### 方式 B: 现有 PG

```sql
CREATE DATABASE jiujiu_mind;
\c jiujiu_mind
CREATE EXTENSION vector;
```

## 步骤 3: 配置

```bash
cp config/config.example.yaml config/config.yaml
```

编辑 `config/config.yaml`：

```yaml
# ====== 大模型 ======
llm:
  primary:
    provider: anthropic
    api_key: sk-ant-xxxxx   # ← 填这里
    model: claude-sonnet-4-5
  fallback:
    provider: openai
    api_key: sk-xxxxx
    model: gpt-4o-mini

# ====== 向量服务 ======
embedding:
  base_url: http://localhost:1234/v1
  api_key: lm-studio
  model: text-embedding-bge-m3
  dimensions: 1024

# ====== 数据库 ======
database:
  host: localhost
  port: 15433
  user: admin
  password: your-password
  dbname: jiujiu_mind
```

## 步骤 4: 初始化表结构

```bash
python scripts/init_db.py
```

会自动创建以下表：
- `books`（书）
- `chunks`（文本片段）
- `chunk_vectors`（向量，pgvector）
- `game_scripts`（剧本）
- `book_mindmaps`（思维导图）
- `tts_audio`（TTS 缓存）

## 步骤 5: 跑流水线

### 单本书

```bash
# 把书放进 books/ 目录
cp ~/Downloads/我的书.epub books/

# 跑完整 8 步
python scripts/pipeline.py books/

# 或只补跑指定步骤
python scripts/pipeline.py books/ --steps summary
```

### 批量处理

```bash
# 处理整个 books/ 目录
python scripts/pipeline.py books/

# 限制并发（防 API 限流）
python scripts/pipeline.py books/ --concurrency 2
```

### 单本 --force 强制重生成

```bash
python scripts/pipeline.py --book-id 384 --force
```

## 步骤 6: 启动 MCP 服务

```bash
# 默认 stdio 模式（适合 Claude Desktop / Cursor）
python scripts/mcp_server.py

# SSE 模式（适合远程访问）
python scripts/mcp_server.py --transport sse --port 8765
```

## 步骤 7: 验证

启动 MCP 服务后，用 Claude Desktop 测试：

> "列出我的书库里所有纳兰相关的书"

应该返回 `search_books(query="纳兰")` 的结果。

---

## 🎯 常见问题

### Q: 必须用 PostgreSQL 吗？

A: 是的，本项目用 pgvector 做向量检索，PG 是最佳选择。MySQL / SQLite 暂不支持。

### Q: 必须用 Claude / GPT 吗？

A: 任何支持 OpenAI 协议或 Anthropic 协议的 LLM 都可以，包括：
- 通义千问 / 文心一言 / 智谱 GLM（OpenAI 兼容）
- Ollama / LM Studio（本地）
- 任何自部署模型

### Q: 数据量大了会怎样？

A: 532 本书 + 670K chunks 测试过，PG 单机足够。百万级建议上分布式 PG。

### Q: 怎么贡献代码？

A: 提交 PR 时请：
1. 保持三层数据流闭环设计
2. 在 `tests/` 加单元测试
3. 更新 `docs/` 对应文档
4. 跑 `pytest tests/` 全绿

---

## 下一步

- 阅读 [架构文档](ARCHITECTURE.md) 深入理解设计哲学
- 查看 [API 文档](API.md) 了解 MCP 12 工具
- 参考 `examples/` 目录的真实运行产物
