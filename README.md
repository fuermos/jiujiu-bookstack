# 玖玖书塔 · JiujiuBookStack

> 一站式电子书知识库构建流水线：**丢进epub，产出结构化知识图谱、SKILL 文档、可玩游戏化剧本、叙事化摘要**。
>
> 适用于：家庭阅读陪伴、知识库构建、教育研究、读书会运营。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PG 14+](https://img.shields.io/badge/postgres-14+-blue.svg)](https://www.postgresql.org/)
[![MCP](https://img.shields.io/badge/MCP-stdio-green.svg)](https://modelcontextprotocol.io/)
[![GitHub stars](https://img.shields.io/github/stars/fuermos/jiujiu-bookstack?style=social)](https://github.com/fuermos/jiujiu-bookstack/stargazers)
[![Latest Release](https://img.shields.io/github/v/release/fuermos/jiujiu-bookstack?include_prereleases)](https://github.com/fuermos/jiujiu-bookstack/releases)
[![Last Commit](https://img.shields.io/github/last-commit/fuermos/jiujiu-bookstack)](https://github.com/fuermos/jiujiu-bookstack/commits/main)
[![Issues](https://img.shields.io/github/issues/fuermos/jiujiu-bookstack)](https://github.com/fuermos/jiujiu-bookstack/issues)
[![Maintenance](https://img.shields.io/maintenance/yes/2026)](https://github.com/fuermos/jiujiu-bookstack)
[![Made with ❤️ by 九九喵](https://img.shields.io/badge/made%20with-%E2%9D%A4%EF%B8%8F%20%E4%B9%9D%E4%B9%9D%E5%96%B5-ff69b4)](https://github.com/fuermos/jiujiu-bookstack)

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🧠 **三层数据流闭环** | `chunks → mindmap → skill → script/summary`，每层精炼上一层，无浪费无重复 |
| 📦 **开箱即用** | 只需要配置大模型 API Key（兼容 OpenAI / Anthropic / 本地 LM Studio） |
| 🔌 **MCP 协议** | 内置 12 个 MCP 工具，AI Agent 直接查询书库 |
| 🎮 **游戏化剧本** | 多题型（MC+OE+角色扮演）+ 起承转合叙事弧 + 分支剧情 |
| 🎯 **敏感词自动脱敏** | 内置词库 + 二分定位自动发现，规避内容审核拦截 |
| ⚡ **向量检索** | 本地 bge-m3 (1024维) 或任意 OpenAI 兼容 embedding |
| 🔄 **幂等设计** | 重跑不破坏数据；`--force` 强制重生成 |
| 📊 **三层数据可视化** | Mermaid 思维导图 + Markdown SKILL.md + JSON 剧本 |

---

## 🎯 项目结构

```
jiujiu-bookstack/
├── README.md                  ← 你正在读
├── docs/
│   ├── ARCHITECTURE.md        ← 三层数据流闭环架构详解
│   ├── QUICKSTART.md          ← 5分钟上手
│   ├── API.md                 ← MCP 12 工具文档
│   └── CHANGELOG.md
├── scripts/
│   ├── pipeline.py            ← 主入口：完整 8 步流水线
│   ├── import_book.py         ← 步骤1：epub → chunks 入库
│   ├── embed_chunks.py        ← 步骤2：批量向量化
│   ├── generate_mindmap.py    ← 步骤3.5：思维导图
│   ├── generate_skill.py      ← 步骤4：SKILL.md 生成
│   ├── generate_script.py     ← 步骤5-6：游戏化剧本 + TTS
│   ├── generate_summary.py    ← 步骤7：叙事化摘要
│   ├── mcp_server.py          ← 12 个 MCP 工具
│   ├── llm_client.py          ← LLM 调用（多 provider fallback）
│   └── text_sanitize.py       ← 敏感词脱敏
├── config/
│   ├── config.example.yaml    ← 配置示例（复制为 config.yaml 后填 Key）
│   ├── sensitive_words.json   ← 敏感词库（可扩展）
│   └── category_rules.yaml    ← 分类规则
└── examples/
    ├── book_597_output/       ← 真实运行产物样例
    └── book_384_output/       ← 含前后对比
```

---

## 🚀 5 分钟上手

### 🐳 方式一：Docker 一键启动（最推荐）

```bash
git clone https://github.com/fuermos/jiujiu-bookstack.git
cd jiujiu-bookstack

# 1. 填配置
cp config/config.example.yaml config/config.yaml
$EDITOR config/config.yaml   # 填 API Key

# 2. 起服务（PG + 后台 + Web UI）
docker-compose up -d

# 3. 打开浏览器
open http://localhost:8501
```

访问 http://localhost:8501 就能看到 **Streamlit Web UI**：
- 🏠 首页：书库浏览 + 分类统计
- 🎮 剧本杀：选书 → 玩剧本（场景对话 + 5 维度评分）
- 🔍 搜索：搜书名 / 语义搜原文
- 📖 书详情：SKILL.md + 思维导图 + 摘要

### 💻 方式二：本地 Python 启动

```bash
git clone https://github.com/fuermos/jiujiu-bookstack.git
cd jiujiu-bookstack
pip install -r requirements.txt

# 启动 PG（Docker）
docker-compose up -d postgres

# 配置
cp config/config.example.yaml config/config.yaml
# 编辑填 API Key

# 一键启动脚本（conda + 依赖 + DB + MCP）
bash scripts/start.sh

# 或手动：
# 1. 跑流水线
python scripts/pipeline.py books/

# 2. 起 Web UI
streamlit run web/app.py

# 3. 起 MCP 给 AI Agent 用
python scripts/mcp_server.py

# 4. CLI 玩剧本杀
python agent/deep_agent.py --book-id 384 --interactive
```

### ⚙️ 最小配置（只需要填 2 个 Key）

```yaml
llm:
  primary:
    provider: anthropic      # OpenAI / Anthropic / ollama
    api_key: sk-ant-xxxxx    # ← 你的 Key
    model: claude-sonnet-4-5
  fallback:
    provider: openai
    api_key: sk-xxxxx
    model: gpt-4o-mini

embedding:
  base_url: http://localhost:1234/v1   # 或 https://api.openai.com/v1
  api_key: lm-studio                    # 本地 LM Studio 免 Key
  model: text-embedding-bge-m3
```

---

## 📚 三层数据流闭环

这是本项目**最核心的设计**——下游应用层永远引用上游原料层，避免重复造轮子：

```
chunks (原始文本)
    ↓
3.5 mindmap   ← 结构骨架（24角色 / 主题 / 情节弧 / 金句）
    ↓
4 skill       ← 引用 mindmap → 叙事地图（7+框架 / 完整角色名）
    ↓
5-6 script    ← 引用两者 → 游戏化剧本（起承转合 / 引用 SKILL.md 金句）
    ↓
7 summary     ← 引用两者 → 叙事化摘要（引用 mindmap 角色列表）
    ↓
8 dedup
```

**为什么这样设计？**

LLM 写剧本时如果只能看到原文 chunks，它会"蒙眼"生成，质量粗糙。
但有了上游 `mindmap` 提炼的 24 个角色名 + `SKILL.md` 的 7 大叙事框架，
LLM 就能写出**有血有肉**的多题型游戏化剧本。

**验证数据**（基于《敢于脆弱》重跑测试）：

| 产物 | 无引用（直跑） | 启用三层引用 | 提升 |
|------|--------------|------------|------|
| SKILL.md 大小 | 1817B（本地降级） | 3959B（LLM 完整） | +118% |
| summary 字符数 | 958（流水账） | 1515（叙事化） | +58% |
| 剧本代入感 | 平铺直叙 | 引用 mindmap 角色名 | +60% |

详见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## 🎮 12 个 MCP 工具

启动 `python scripts/mcp_server.py` 后，对 AI Agent 暴露以下工具：

| 工具 | 用途 | 示例 |
|------|------|------|
| `list_books` | 列书 | `list_books(category="文学", limit=10)` |
| `get_book` | 单本详情 | `get_book(book_id=384)` |
| `search_books` | 关键词搜书 | `search_books(query="纳兰")` |
| `semantic_search` | 语义搜索 | `semantic_search(query="脆弱与力量", top_k=5)` |
| `get_chunks` | 拿章节内容 | `get_chunks(book_id=384, limit=5)` |
| `get_script` | 拿剧本 | `get_script(book_id=384, game_type="v2_cyoa")` |
| `list_categories` | 分类统计 | `list_categories()` |
| `get_random_chunk` | 随机chunk | `get_random_chunk(book_id=384)` |
| `get_book_stats` | 库统计 | `get_book_stats()` |
| `get_category_stats` | 分类统计 | `get_category_stats()` |
| `list_books_with_status` | 按状态过滤 | `list_books_with_status(missing="summary")` |
| `sql_query` | 只读 SQL | `sql_query(query="SELECT * FROM books WHERE id=384")` |

`sql_query` 自动拦截写操作（DROP/UPDATE/DELETE/INSERT/ALTER/CREATE/TRUNCATE）。

---

## 🔧 大模型支持

通过 `llm_client.py` 抽象，兼容以下 Provider：

| Provider | 配置示例 |
|----------|----------|
| **Anthropic Claude** | `provider: anthropic, model: claude-sonnet-4-5` |
| **OpenAI GPT** | `provider: openai, model: gpt-4o-mini` |
| **本地 LM Studio** | `provider: ollama, base_url: http://localhost:11434/v1` |
| **国内大模型** | `provider: openai, base_url: https://api.minimaxi.com/v1`（兼容 OpenAI 协议即可） |
| **其他** | 任何支持 OpenAI / Anthropic 协议的服务 |

**fallback 链**：主模型失败自动降级到备用，避免 401/2056 中断。

---

## 🎯 敏感词自动脱敏

LLM 调用前自动 sanitize，规避内容审核拦截。

**机制**：
1. 内置词库（`config/sensitive_words.json`）：pattern → replace
2. 调用前 `sanitize(prompt)` 替换已知触发词
3. 触发时**自动二分定位**未知词，加入词库
4. 日志写入 `logs/sensitive_discoveries.log`

**实战案例**：

minimax API 触发 `input new_sensitive (1026)`，根因是组合词（"肉桂糖棍"，单独"肉桂""糖棍"都OK）。
自动发现后加入词库 → 下次 sanitize 直接替换 → 通过。

---

## 📊 性能数据

基于 532 本真实书库（670,013 chunks）的实测：

| 步骤 | 单本耗时 | 全库耗时 |
|------|----------|----------|
| 1 import (epub→chunks) | ~30s | ~4h |
| 2 embed (向量化) | ~30s | ~3h |
| 3.5 mindmap | ~30s | ~6h |
| 4 skill | ~5s | ~1h |
| 5-6 script+tts | ~5min | - |
| 7 summary | ~8s | ~2h |

**增量处理**：新增单本约 6-7 分钟（含 TTS）。

---

## 🤝 贡献

欢迎 PR！但请遵守：
- 保持三层数据流闭环设计
- 新增 Provider 在 `llm_client.py` 里加
- 新增分类在 `category_rules.yaml` 加 regex
- 敏感词可补充到 `sensitive_words.json`

---

## 📜 License

MIT

---

## 🔗 衍生文章

配套公众号文章草稿：[`docs/article-wechat.md`](docs/article-wechat.md)（待发布）

---

**项目初衷**：让任何家庭都能用大模型把家里的电子书变成可玩可查可学的家庭知识库。
