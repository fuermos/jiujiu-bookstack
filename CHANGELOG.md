# 更新日志

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
