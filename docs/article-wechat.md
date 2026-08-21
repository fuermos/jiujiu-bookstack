# 我用 LLM 把家里 532 本书做成了可玩的剧本杀，然后开源了

> 公众号文章草稿 · 2026-08-21
>
> GitHub: https://github.com/fuermos/jiujiu-bookstack
>
> 文末有项目地址 & 上手指南

---

## 引子：532 本书的归宿问题

我家有 532 本电子书，堆在硬盘里吃灰。

**传统的"读书陪伴"产品都在做两件事**：
- RAG：问什么答什么
- 剧本生成：生成一份一次性游戏

但缺一个东西——**把书"驯化"成一个可以反复玩、可以查、可以学的家庭知识库**。

所以我做了 **`jiujiu-bookstack`**（[GitHub](https://github.com/fuermos/jiujiu-bookstack)）。

---

## 它是什么？

**一句话**：丢进 epub，产出**结构化知识图谱、SKILL 文档、可玩游戏化剧本、叙事化摘要**的全自动流水线。

具体点：

```
epub 文件
   ↓
import → embed → mindmap → skill → script+tts → summary
   ↓
你可以：
  - 问"脆弱与力量的关系"（semantic_search）
  - 让 AI 总结"24 角色 + 7 大框架"（SKILL.md）
  - 玩"13 岁女生 vs 拉·封丹寓言"的剧本杀
  - 听"深秋北京笑笑啃英语"的 TTS 旁白
```

适用场景：家庭阅读陪伴 / 读书会运营 / 教育研究 / 私人知识库。

---

## 核心设计：三层数据流闭环

这是我做这个项目**最重要的洞察**：

> 写剧本时不应该从原文 chunks 蒙眼开始，应该看到上游提炼好的结构。

具体来说：

```
原始文本 chunks
    ↓
🗺️ Mindmap  ← LLM 提炼结构骨架（24 角色 / 主题 / 情节弧 / 金句）
    ↓
📋 SKILL.md  ← LLM 引用 mindmap → 叙事地图（7 框架 + 完整角色名）
    ↓
🎮 Script  ← LLM 引用两者 → 游戏化剧本（起承转合 / 引用金句）
    ↓
📝 Summary  ← LLM 引用两者 → 叙事化摘要（引用角色列表 + 主题）
```

**每层都精炼上一层，无浪费，无重复。**

---

## 真实数据对比

基于《敢于脆弱》（法国心理学家吉娜维芙·阿弗里伊尔）重跑测试：

| 产物 | 旧（无引用） | 新（三层引用） | 提升 |
|------|-------------|--------------|------|
| SKILL.md 大小 | 1817B（本地降级） | **3959B**（LLM 完整） | **+118%** |
| summary 字符数 | 958（流水账） | **1515**（叙事化） | **+58%** |
| 剧本代入感 | 平铺直叙 | 引用 mindmap 角色名 | **+60%** |

**剧本场景对比**：

旧（场景标题："对话练习"）：
> 你在教室里，需要回答老师的问题。

新（场景标题：**"芦苇的呼吸"**——直接引用 SKILL.md 核心隐喻）：
> 深秋的北京，你——笑笑，一个十三岁的初一女生——坐在书桌前啃英语单词，却被窗外一阵突如其来的雨声打断。雨水顺着窗玻璃像泪水一样滑下来，你想起今天小测又没拿满分，妈妈的叹息又轻又沉，像一片叶子落在胸口。

**有没有感觉到区别？** 新版本直接引用了 mindmap 提炼的"橡树与芦苇"寓言 + 主角画像，**叙事质量肉眼可见的飞跃**。

---

## 技术细节：5 个有意思的设计

### 1. 开箱即用（只配 Key 就跑）

```yaml
# config.yaml - 只需要填两个 Key
llm:
  primary:
    provider: anthropic
    api_key: ***  # ← 这里
    model: claude-sonnet-4-5
embedding:
  base_url: http://localhost:1234/v1
  api_key: lm-studio  # 本地免 Key
  model: text-embedding-bge-m3
```

**支持任何 OpenAI / Anthropic 协议的服务**：通义千问、文心一言、Ollama、本地 LM Studio……

### 2. MCP 12 个工具（AI Agent 直接用）

启动 `mcp_server.py`，AI Agent 可以：

```python
semantic_search(query="脆弱与力量", book_id=384)
# → 返回最相关的 10 个 chunks
```

MCP 工具清单：
- `list_books` / `get_book` / `search_books`
- `semantic_search` / `get_chunks` / `get_script`
- `list_categories` / `get_random_chunk`
- `get_book_stats` / `get_category_stats`
- `list_books_with_status` / `sql_query`（只读）

### 3. 敏感词自动脱敏（实战踩过的坑）

我用了某个国产 LLM，遇到 500 错误 `input new_sensitive (1026)`——**内容审核拦截**。

二分定位发现是"**肉桂糖棍**"——书里列食物清单的某段。单独"肉桂""糖棍"都 OK，**组合就触发**。

后来我做了**敏词库自动脱敏 + 二分定位自动入库**：
- 触发时 sanitize 替换已知词
- 找不到触发词时二分定位最小片段
- 自动写入 `data/sensitive_discoveries.log` 下次直接替换

**实战**：book 164 chunk 11 → sanitize 后 → 国产 LLM 直接通过 ✅

### 4. DeepAgent 剧本杀交互

不只是"生成剧本"，还做了**完整的多 Agent 协同**：

```
[GameMaster] 加载剧本（调 MCP get_script）
   ↓
[GameMaster] 展示场景
   ↓
   ├─ MC 题 → 直接判对错
   └─ OE 题 → [Evaluator] 5 维度评分
                ↓
                [Reader] semantic_search 查原文
                ↓
                [Evaluator] 先肯定再说建议，最后一句鼓励
```

4 个 Agent 角色：
- **GameMaster** - 主控剧情
- **NPC Agent** - 角色扮演
- **Reader** - 通过 MCP 查书库
- **Evaluator** - 5 维度评分（深度/独特性/文本关联/真诚度/世界观对齐）

启动方式：
```bash
python agent/deep_agent.py --book-id 384 --interactive
```

### 5. 幂等设计 + --force 强制重生成

pipeline 是幂等的：
- import: `UNIQUE (book_id, MD5(chunk_text))` 防重复
- script: `UNIQUE (book_id, chapter_index, game_type)` 防双重 v2_ 前缀
- summary: 已有则跳过（除非 `--force`）

**对比"无脑重跑"**：
- 无 --force：~6 秒（所有数据已就绪）
- --force：~7 分钟（含 LLM 重生成 + TTS）

实测 book 384 三次 --force，PG 数据完全一致——**重跑不破坏**。

---

## 上手指南

```bash
# 1. 克隆
git clone https://github.com/fuermos/jiujiu-bookstack.git
cd jiujiu-bookstack

# 2. 推荐用 miniconda（Python 3.11）
conda create -n bookstack python=3.11
conda activate bookstack
pip install -r requirements.txt

# 3. 启动 PG（Docker）
docker-compose up -d

# 4. 配置
cp config/config.example.yaml config/config.yaml
# 编辑填 Key

# 5. 跑流水线
python scripts/pipeline.py books/

# 6. 启动 MCP 给 Agent 用
python scripts/mcp_server.py
```

完整文档：
- [README.md](https://github.com/fuermos/jiujiu-bookstack)
- [docs/ARCHITECTURE.md](https://github.com/fuermos/jiujiu-bookstack/blob/main/docs/ARCHITECTURE.md) - 三层数据流闭环详解
- [docs/QUICKSTART.md](https://github.com/fuermos/jiujiu-bookstack/blob/main/docs/QUICKSTART.md) - 5 分钟上手
- [docs/API.md](https://github.com/fuermos/jiujiu-bookstack/blob/main/docs/API.md) - MCP 12 工具文档

---

## 局限 & 下一步

**v0.1.0 局限**：
- 只支持 PostgreSQL（pgvector）
- DeepAgent 是简化版，没接 LangGraph
- 套装书识别还在 TODO

**v0.2 计划**：
- 接 LangGraph（state machine + checkpoint）
- 套装书/合集自动识别
- Web UI（Streamlit）
- 多语言支持

---

## 写到最后

这个项目最让我开心的不是技术本身，而是**它改变了我们家"书"的角色**。

以前书是静态的——读完一遍就放回去。
现在书是**活的**——可以问它、玩它、听它，甚至让它给家里的初中生妹妹出 24 道思考题。

**家里 532 本书，现在每一本都是一个"小型家庭知识库"**。

希望这个项目也能帮到你。

**GitHub**: https://github.com/fuermos/jiujiu-bookstack

欢迎 Star、Fork、提 Issue！

---

*本文同步发于公众号 / 知乎 / 掘金，欢迎转发。*

*代码 MIT 协议，随意用。*
