# jiujiu-bookstack 上手指南

> 一站式电子书知识库 → 全自动流水线（epub → 结构化知识 + SKILL + 可玩游戏化剧本 + 思维导图 PNG）
>
> **GitHub**: https://github.com/fuermos/jiujiu-bookstack
>
> **当前版本**: v0.4.0 (用户隔离 + 进度恢复 + 沉浸式剧本 + 真实封面)

---

## 方案选择

| 你的情况 | 推荐方案 |
|---------|---------|
| 想跑起来玩玩 / 快速体验 | **方案 A：Docker 一键启动** (5 分钟) |
| 想部署到自己的 NAS / 服务器 / 已有 PG | **方案 B：手动部署** (15 分钟) |
| 想贡献代码 / 本地开发 | **方案 C：源码开发模式** |

---

## 方案 A：Docker 一键启动 (推荐)

### 前置要求

- Docker 20+ 和 docker-compose v2
- 一个 LLM API Key（[MiniMax](https://api.minimaxi.com) / Anthropic / OpenAI 任一）
- 1GB 可用磁盘

### 步骤

```bash
# 1. 克隆
git clone https://github.com/fuermos/jiujiu-bookstack.git
cd jiujiu-bookstack

# 2. 填环境变量
cp .env.example .env
$EDITOR .env
# 改: MINIMAX_API_KEY=your_real_key_here

# 3. 一键启动 (PG + app + web)
docker-compose up -d

# 4. 验证
docker-compose ps          # 3 个容器都 healthy
curl http://localhost:8501 # 应该返回 200 (Streamlit)

# 5. 打开浏览器玩
open http://localhost:8501
```

### 跑流水线

```bash
# 拷书进 books/
cp my_book.epub books/

# 跑完整 8 步 (import → embed → classify → mindmap → skill → script+tts → summary)
docker-compose exec app python /app/scripts/pipeline.py /app/books/

# 单本强制重跑
docker-compose exec app python /app/scripts/pipeline.py /app/books/my_book.epub --force
```

### 命令行玩剧本杀

```bash
docker-compose exec app python /app/agent/deep_agent.py --book-id 1 --interactive
```

### 目录结构

```
jiujiu-bookstack/
├── docker-compose.yml       # 单文件整合 PG + app + web
├── .env                     # 你的真实 API Key
├── config/
│   ├── init_db.sql          # 9 张表的完整 schema (手动部署用)
│   ├── config-docker.yaml   # 容器专用配置
│   ├── config.example.yaml  # 手动部署配置模板
│   └── category_rules.yaml  # 自动分类规则
├── books/                   # 你的 epub 书库
├── mindmaps/                # 思维导图 PNG (自动生成)
├── tts/                     # TTS 音频缓存 (预生成)
├── data/covers/             # epub 封面图片 (自动提取)
├── scripts/                 # pipeline + MCP server
├── agent/deep_agent.py      # 剧本杀多 Agent 引擎
└── web/app.py               # Streamlit Web UI
```

---

## 方案 B：手动部署（用自己的 PG）

如果你已经有 PostgreSQL 14+ 跑着（带 pgvector 扩展），不想用容器化的 PG：

### 前置要求

- Python 3.10+
- PostgreSQL 14+ (带 pgvector)
- 一个 LLM API Key

### 步骤

```bash
# 1. 克隆
git clone https://github.com/fuermos/jiujiu-bookstack.git
cd jiujiu-bookstack

# 2. 安装依赖
pip install -r requirements.txt

# 3. 在你的 PG 上创建数据库 + 跑 schema
psql -U <你的用户> -d <你的数据库> -f config/init_db.sql
# 创建了 9 张表: books / chunks / chunk_vectors / book_mindmaps
#                 game_scripts / tts_audio
#                 users / script_play_records (v0.4 用户隔离)
#                 sensitive_discoveries (调试用)

# 4. 复制配置模板 + 编辑
cp config/config.example.yaml config/config.yaml
$EDITOR config/config.yaml
# 改:
#   llm.primary.api_key = your_real_key
#   database.host = 你的 PG 地址
#   database.password = 你的 PG 密码
#   embedding.base_url = 你的 embedding 服务地址

# 5. 跑流水线
python scripts/pipeline.py books/

# 6. 启动 Web UI (前台, 调试用)
streamlit run web/app.py --server.port 8501

# 7. (可选) 启动 MCP server 给 AI Agent 用
python scripts/mcp_server.py
```

### 数据库要求

`config/init_db.sql` 跑了之后会创建以下 extension 和表：

```sql
CREATE EXTENSION IF NOT EXISTS vector;  -- pgvector
```

如果你用云数据库（如阿里云 RDS PG、Supabase），确保：
- pgvector 扩展已启用
- 用户有 CREATE EXTENSION 权限

### Web UI 配置

web/app.py 启动时会读 `config/config.yaml`（不是 config-docker.yaml）。手动部署用 `config/config.yaml`；容器部署用 `config/config-docker.yaml`（通过 volume 挂载覆盖）。

---

## 方案 C：源码开发模式（贡献代码）

```bash
# 1. 克隆 + 装依赖
git clone https://github.com/fuermos/jiujiu-bookstack.git
cd jiujiu-bookstack
pip install -r requirements.txt
pip install -r requirements-dev.txt  # pytest + playwright + edge-tts

# 2. 起一个本地 PG (Docker 化, 不污染环境)
docker run -d --name local-pg \
    -e POSTGRES_PASSWORD=postgres_pwd_change_me \
    -e POSTGRES_USER=admin \
    -e POSTGRES_DB=jiujiu_mind \
    -p 15433:5432 \
    -v $(pwd)/config/init_db.sql:/docker-entrypoint-initdb.d/01-init.sql \
    pgvector/pgvector:pg16

# 3. 改 config/config.yaml
# database.host = localhost

# 4. 跑测试
pytest tests/

# 5. 启动 Web UI (热重载)
streamlit run web/app.py --server.port 8501
```

---

## 第一个 30 分钟体验路径

```bash
# 1. 拷入一本 epub
cp ~/Downloads/福尔摩斯.epub books/

# 2. 跑 pipeline (~5 分钟)
docker-compose exec app python /app/scripts/pipeline.py /app/books/

# 3. 看 Web UI
open http://localhost:8501

# 4. 注册账号 (随便邮箱 + 6 位密码)

# 5. 选福尔摩斯 → 弹层选剧本 → 选角色 → 开玩
```

---

## 🎙️ TTS 策略（v0.4 混合方案）

主人在 2026-08-22 决定：TTS **在线 + 预生成混合方案**。

| 阶段 | 行为 | 备注 |
|------|------|------|
| 剧本生成时 | 一次性预生成**所有场景旁白**（edge-tts 离线跑），存 `tts/{book_id}_{scene_id}.mp3` | 覆盖 100% 场景描述 |
| 运行时 | 优先用预生成缓存（命中率 ~100%）| 缓存命中 0.004 秒（550x 提速）|
| 缓存 miss | fallback 到在线 edge-tts（2-3 秒/段）| 兜底 |

**配置**：edge-tts 默认 `zh-CN-YunxiNeural`（男声，云希）；Web UI 可切 `zh-CN-XiaoxiaoNeural`（女声，晓晓）。

**预生成触发**：pipeline step 6 (tts) 自动跑。如果你只想跑不预生成 TTS：
```bash
docker-compose exec app python /app/scripts/pipeline.py /app/books/my_book.epub --skip tts
```

---

## 🔧 常见问题

### Q: 必须用 PostgreSQL 吗？

A: 是的，本项目用 pgvector 做向量检索。MySQL / SQLite 暂不支持。

### Q: 必须用 Claude / GPT 吗？

A: 任何支持 OpenAI 协议或 Anthropic 协议的 LLM 都可以，包括：
- 通义千问 / 文心一言 / 智谱 GLM（OpenAI 兼容）
- Ollama / LM Studio（本地）
- MiniMax / Anthropic / OpenAI（默认）

### Q: 必须用 embedding 服务吗？

A: 是的，但可以本地（推荐 LM Studio 跑 bge-m3）或云端（OpenAI text-embedding-3-small 等）。

### Q: 不用 edge-tts 怎么办？

A: 改 `config/config.yaml` 的 `tts.voice` 字段，或直接 `pipeline.py --skip tts`。

### Q: 数据量大了会怎样？

A: 532 本书 + 67 万 chunks 测过，PG 单机足够。百万级建议上分布式 PG (Citus)。

### Q: 容器化部署和手动部署能共存吗？

A: 可以。容器化用 `config-docker.yaml` + `docker-compose.yml`；手动部署用 `config/config.yaml`。两者互不影响。

---

## 下一步

- 阅读 [架构文档](ARCHITECTURE.md) 深入理解"三层数据流闭环"
- 查看 [API 文档](API.md) 了解 MCP 12 工具
- 看 [DeepAgent 设计文档](DEEP_AGENT_DESIGN.md) 了解剧本杀多 Agent 评分
- 看公众号文 [docs/article-wechat.md](article-wechat.md)