# 更新日志

## [0.6.0] - 2026-09-01

### 🎉 首个正式 Release

这是项目第一个 GitHub Release（[v0.6.0](https://github.com/fuermos/jiujiu-bookstack/releases/tag/v0.6.0)），主要整理了文档与发布物料。

### 📚 文档整合

- **公众号文章 v4** 升级为 canonical 版本（`docs/article-wechat.md`，736 行 / 32 KB）
  - 标题升级：《我用 Agent + Skill + MCP 三层架构，把家里 532 本书做成了一个会跟你对话的家庭知识库》
  - 新增 LangGraph 升级章节（v0.6 路线图）+ 决策日志彩蛋
  - 历史版本归档到 `docs/archive/`（v1 / v3）
- **README** 添加 GitHub Stars / Latest Release / Python 版本 / 维护状态 badges
- **docs/README 结构图** 在 QUICKSTART 中补齐文件树

### 🛠️ Bug 修复

- `pipeline.py --steps` 参数不生效（[bug_2026_08_29](https://github.com/fuermos/jiujiu-bookstack/issues/bug_2026_08_29)）
- 心流模型 minimax 角色别名（处理 `` `minimax` `` 这类带特殊字符的角色名）
- 9 个测试套件失败修复（11 fail → 0 fail，43 passed）

### ⚙️ 配置增强

- **在线模式预设**（主人 2026-08-29 钦定）
  - `config/config-online.yaml` 模板新增，免 LM Studio 也能跑全流水线（用 minimax 1M context）
  - 配置示例 `config.example.yaml` 加 `mode: online | local | hybrid` 开关
- LLM fallback 链**更新注释**：`unsloth/qwen3.8-27b` → `qwen3.8-27b@iq2_s`（实际 LM Studio 暴露的 ID）

### 📊 v0.6 数据状态

| 指标 | 数量 |
|---|---|
| 库中书籍 | 9 本完整入库（24-30 + 600-601）|
| 总剧本 | 30+ 个 v2_mixed（含 book 30 拆 29 个）|
| MCP 工具 | 12 个只读 + v0.6 计划 +8-10 写工具 |
| 回归测试 | 43 passed (0 fail) |
| Docker | v2.29.7 + 3 容器编排（PG + app + web）|

---

## [0.5.0] - 2026-08-27

### 🌟 重大升级：Agent + Skill + MCP 三层架构

- **架构升级**：从单层 pipeline 升级到 Agent（编排）+ Skill（业务）+ MCP（工具）三层架构
- **架构文档**：新增 `docs/article-wechat.md` + `dist/公众号文章_草稿_v4_2026-08-27.md` 详解三层架构
- **微 Harness**：`deep_agent.py` 363 行 Python，作为开箱即用的剧本杀 Harness
- **对外定位**：我们**不是 Harness**，是被 Harness 调用的 MCP server（任何 Agent Runtime 都能复用）
- **未来规划**：v0.6 全 MCP 化（auth_*/job_*/reading_*/tts_* 等写工具）

### 🆕 新增功能

- **📖 在线阅读（Koodo Reader 风格）**
  - 米色纸张背景 + 宋体衬线 + line-height 2.0 + max-width 居中
  - 断点续读：重进页面显示"上次读到第 X 章"，一键跳回
  - 字号滑杆（14-28px），字号持久化到 PG
  - 全书进度条 + 每段 TTS 听书按钮
- **🚀 任务队列 UI**
  - `pipeline_jobs` 表 + Web UI（看进度/看日志/取消/重试）
  - 失败任务一键重试（实战验证：book 29 job #1 LLM 400 失败 → 重试 → completed）
  - `clear_completed_jobs()` 清理已完成任务
- **🤖 pipeline_worker.py 后台 cron**
  - OpenClaw cron ID `1b3c0d3f` 每 60s 扫队列
  - module-level 启动代码移至文件末尾（修 NameError）
- **🔄 合集书 auto-split（pipeline.py Step 6.5）**
  - 自动检测"真实章节数 > 50 AND 剧本章节覆盖率 < 50%" → 自动调 `split_book_scripts.py`
  - book 30 高中 56 篇合集 → 拆 29 个剧本（自动跑）
- **📤 Web 上传书本**
  - sidebar expander + `st.file_uploader`
- **🔍 增强的搜索/分类**
  - 分类统计字段对齐（MCP `n` vs 前端 `count` 修复）
  - 占位符 `(NEW)` 修正为真实分类

### 🛠️ Bug 修复大集合

- **Docker Compose v1 → v2 永久升级** — v1.29.2 watch_events KeyError: 'id' 折磨 14h → v2.29.7
- **PG auth failed** — `ALTER USER admin WITH PASSWORD 'postgres_pwd_change_me'` + `PGPASSWORD` 环境变量
- **在线 TTS 失败** — edge-tts 双容器补装 + requirements.txt 同步（Docker 容器 pip install 是临时的）
- **首页分类分布错误** — MCP `n` vs 前端 `count` + 占位符污染
- **`generate_script.py` ROOT 未定义** — 路径引入修复
- **`_format_error` name 变量未用** — f-string 修
- **`books.total_scenes` 不同步** — save_to_pg 加 SUM 所有剧本 scenes
- **save_to_pg UniqueViolation** — `ON CONFLICT DO UPDATE`
- **`parse_json_with_retry` 无 timeout** — LLM retry 加 timeout 走 fallback
- **`generate_script.py` default SKILL 路径** — 优先 `data/{id}_SKILL.md`
- **`pipeline.py` step 5-6 不传 skill/mindmap** — 加参数传递
- **`generate_summary` 没限长** — 按 `max_input_tokens` 动态限（500K→33K）
- **import_book 版权页入 chunks** — SKIP_PATTERNS + MIN_CONTENT_LEN 过滤
- **测试 cover 路径错** — `/app/data/covers/{id}.jpg` 而非 `/app/data/{id}.jpg`
- **book 27 重 import 后 game_type 空** — 加 game_type sync 测试防回归
- **`pipeline_worker.py` module-level NameError** — 启动代码移至文件末尾

### 🧪 测试

- **29 个回归测试**（TDD 防回归）
- `tests/test_regression_2026_08_25.py` — 完整性 + game_type 一致性 + foreign chars
- `tests/test_regression_bugs.py` — bug 复现测试

### 📊 v0.5 数据状态

| 指标 | 数量 |
|---|---|
| 库中书籍 | 9 本完整入库 (24-30 + 600-601) |
| 总剧本 | 30+ 个 v2_mixed（book 29/30 split 后） |
| MCP 工具 | 12 个只读（v0.6 计划全 MCP 化 +8-10 写工具） |
| 回归测试 | 29 个全过 |
| Docker | v2.29.7 + 3 容器编排（PG + app + web） |

---

## [0.4.0] - 2026-08-22

### 🌟 用户隔离 + 剧本进度恢复 + 沉浸式剧本

- **用户系统** (`scripts/user_manager.py`)
  - 邮箱注册 + 6 位密码 + salt 哈希（避免 bcrypt 依赖）
  - 登录 token 持久化（`issue_token` / `resolve_token` / `revoke_token`，30 天有效期）
  - 启动时从 `query_params["token"]` 还原登录态（防刷新掉登）
- **剧本进度记录**（用户隔离）
  - `script_play_records` 表 + Web UI
  - 中断可恢复到 Lv.7，显示之前得分
- **沉浸式剧本杀**（v2_mixed）
  - **后处理兜底**：强制以"你"开头第一人称代入
  - **choice 题型**：占 40%，玩家决定剧情走向
  - **多 Agent 协同评分**：温柔姐姐 (0.4) + 严格导师 (0.6)
- **一本书多剧本** (`split_book_scripts.py`)
  - 按 `chapter_range` 切片（10 章/组），跳过已有 + 支持 `--force`
  - 福尔摩斯 11 卷 → 12 个剧本
- **modal 三步流**（_script_selector_modal）
  - ① 选剧本 → ② 选角色 → ③ 确认开玩
  - 单剧本智能跳过 + 切书时重置 `selected_script_id`/`player_role_radio`
- **epub 封面自动提取**（`extract_cover()`）
  - 找带 'cover' id 的图片入库 `data/covers/{id}.jpg`

### 🛠️ Bug 修复

- **角色污染**（数学剧本混入外尔摩斯）— FOREIGN_CHARS 黑名单 + book_meta 强制注入 + scene-based 检测
- **modal 黑屏** — Streamlit 自定义 HTML/CSS modal 替换为 `@st.dialog`
- **TTS 3 次重试** — `gen_one(text, audio_path)` 校验 `>1024 bytes`，失败删空文件
- **MCP get_script 缺 id** — 加 id 返回
- **登录报错根因** — cover_url None 时拼出 `/app/data` 目录

---

## [0.3.0] - 2026-08-22

### 🌟 三层数据流闭环 + 思维导图 PNG

- **核心升级**：从"蒙眼生成剧本"升级到"引用上游提炼"
  - chunks → mindmap → skill → script/summary
  - 每层精炼上一层，不浪费不重复
- **思维导图 PNG 渲染**（v0.3）
  - Playwright + mermaid.ink 自动渲染
  - 每个剧本一张图（剧本结构 / 角色 / 诡计 / 主题 / 金句一眼看完）
  - Web UI `st.image()` 直接显示
- **联合主键支持每剧本一图**
  - `book_mindmaps (book_id, script_id)` PRIMARY KEY
- **质量飞跃**（book 384 实测）：
  - SKILL.md: 1817B → 3959B (+118%)
  - summary: 958 字 → 1515 字 (+58%)
  - 剧本代入感: 平铺直叙 → 引用 mindmap 角色名 (+60%)

---

## [0.2.0] - 2026-08-22

### 🌟 新增：Streamlit Web UI

**本版本重大更新**：

- **Streamlit Web UI** (web/app.py) — 4 页交互界面
  - 🏠 首页：库统计 + 书单浏览
  - 🎮 剧本杀：选书 → 场景对话 → 5 维度评分
  - 🔍 搜索：搜书名 + 语义搜原文
  - 📖 书详情：SKILL.md + 思维导图 + 摘要
- **Docker 一键启动** — `docker-compose up -d` 起 PG + 后台 + Web UI
  - 新增 `web` 服务（端口 8501）
  - 默认浏览器打开 http://localhost:8501
- **依赖更新** — requirements.txt 加 streamlit>=1.30
- **文档更新** — README 加 Web UI 使用说明 + Docker 启动指南

### 改进
- Dockerfile 加 EXPOSE 8501 + 默认 CMD
- docker-compose.yml 加 web 服务 + 服务编排依赖
- llm_client.call 已为同步（requests），Web UI 直接复用

### 上手难度
- v0.1.0：6 步（clone + pip + cp config + edit key + docker postgres + pipeline）
- v0.2.0：**3 步**（clone + cp config + edit key → docker-compose up -d）

---

## [0.1.0] - 2026-08-21

### 🎉 首次发布

**核心功能**:
- 三层数据流闭环 (mindmap → skill → script/summary)
- 完整 8 步 pipeline:
  1. import (epub → chunks)
  2. embed (向量化)
  3.5 mindmap (思维导图生成)
  4 skill (SKILL.md 生成)
  5-6 script + tts (游戏化剧本 + 音频)
  7 summary (叙事化摘要)
  8 dedup (同名变体查重)
- MCP 12 个工具 (stdio 协议)
- DeepAgent 剧本杀交互引擎
- 敏感词自动脱敏
- 多 provider LLM fallback

**已验证** (基于 532 本真实书库):
- 670,013 chunks 100% 向量化
- SKILL.md 质量 +118% (对比旧 pipeline)
- summary 质量 +58%
- 剧本代入感 +60%

**配置**:
- PostgreSQL 14+ with pgvector
- Python 3.10+
- edge-tts (TTS 音频)
- mcp SDK

**文档**:
- README.md
- docs/ARCHITECTURE.md
- docs/QUICKSTART.md
- docs/API.md
- agent/README.md

### 已知限制

- 仅支持 PostgreSQL（pgvector）
- 未接 LangGraph（用 asyncio 简化版）
- 套装书的 SKILL.md 仍按单本处理（识别"套装/合集/全X册"是下版本）

### 下版本计划

- [x] Web UI（Streamlit）✅ **v0.2.0 完成**
- [ ] 接 LangGraph（state machine + checkpoint）
- [ ] 套装书/合集自动识别
- [ ] 多语言支持（en/ja）
- [ ] TTS 播放嵌入 Web UI（直接听剧本）
