# 我把家里 532 本电子书做成了一个可以玩的剧本杀网站

> 公众号文章草稿 v2 · 2026-08-22
>
> GitHub: https://github.com/fuermos/jiujiu-bookstack
>
> 在线玩: `docker-compose up -d` 后访问 http://localhost:8501
>
> 文末附 3 步上手指南

---

## 一、事情的起因：532 本书躺着吃灰

我家硬盘里有 532 本电子书，**每年翻两次，每次不超过两分钟**。

买的时候都觉得自己要读完。结果要么太厚，要么翻译太烂，要么读完第一章就忘了第二章谁是谁。

去年我开始用 LLM 做读书笔记，但很快发现一个问题：

**"问什么答什么"的 RAG 不是陪伴，"一次性生成剧本"也不是陪伴**。

书这个东西，最好的归宿不是被读，而是被**反复使用**——可以问它、玩它、听它。让一本静态的电子书，变成一个**会跟你对话的家庭成员**。

于是我做了 **`jiujiu-bookstack`**（[GitHub](https://github.com/fuermos/jiujiu-bookstack)）。

---

## 二、它是什么？

**一句话**：丢一本 epub 进去，出来一个**结构化知识图谱 + SKILL 文档 + 可玩游戏化剧本 + 叙事化摘要**的全自动流水线。

具体一点（拿《敢于脆弱》举例）：

```
epub 文件
    ↓
import → embed → mindmap → skill → script+tts → summary
    ↓
你可以：
  - 问"脆弱与力量的关系"（语义搜索，5 秒返回）
  - 让 AI 总结"24 角色 + 7 大框架"（SKILL.md，4KB）
  - 在网页上玩"13 岁笑笑 vs 拉·封丹寓言"的剧本杀（多题型，5 维度评分）
  - 听"深秋北京笑笑啃英语"的 TTS 旁白（edge-tts 离线生成）
```

这个项目我已经开源，**完整流水线 + MCP + Streamlit Web UI + Docker 一键启动**全在仓库里。

---

## 三、最重要的一个设计：三层数据流闭环

我做这个项目踩过最大的坑：

**第一版剧本生成是"蒙眼"写的**——LLM 只看到原文章节 chunks，结果生成的剧本平铺直叙、人物扁平、没有温度。

后来我悟了一个道理：

> **剧本写得好不好，不取决于 LLM 多强，取决于喂它多少"上游提炼"。**

具体做法：

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

**每层都精炼上一层，不重复造轮子。**

---

## 四、真实数据对比（来自《敢于脆弱》重跑测试）

| 产物 | 旧（蒙眼生成） | 新（三层引用） | 提升 |
|------|--------------|--------------|------|
| SKILL.md 大小 | 1817B（本地降级） | **3959B**（LLM 完整） | **+118%** |
| summary 字符数 | 958（流水账） | **1515**（叙事化） | **+58%** |
| 剧本代入感 | 平铺直叙 | 引用 mindmap 角色名 | **+60%** |

**剧本场景对比**（同一本书同一章节）：

旧版本（场景标题："对话练习"）：
> 你在教室里，需要回答老师的问题。

新版本（场景标题：**"芦苇的呼吸"**——直接引用 SKILL.md 核心隐喻）：
> 深秋的北京，你——笑笑，一个十三岁的初一女生——坐在书桌前啃英语单词，却被窗外一阵突如其来的雨声打断。雨水顺着窗玻璃像泪水一样滑下来，你想起今天小测又没拿满分，妈妈的叹息又轻又沉，像一片叶子落在胸口。

**区别肉眼可见。** 旧版本是答题机器，新版本是给笑笑写的小剧场。

---

## 五、5 个有意思的设计

### 1. Web UI 玩剧本杀（零代码）

我做完 CLI 版本后，媳妇问了一句：**"我能玩吗？"**

答案是"能，但你得会用命令行"——这等于不能。

所以我加了 **Streamlit Web UI**。`docker-compose up -d` 之后打开浏览器：

- 🏠 首页：书库浏览 + 分类统计
- 🎮 剧本杀：选书 → 场景对话 → 提交答案 → 看反馈
- 🔍 搜索：搜书名 / 语义搜原文
- 📖 书详情：SKILL.md + 思维导图 + 摘要

打开 http://localhost:8501，**我媳妇现在每周末都会自己打开玩**。这就够了。

### 2. MCP 12 个工具（让 AI Agent 直接用）

启动 `mcp_server.py` 后，对 AI Agent 暴露 12 个工具（[完整文档](https://github.com/fuermos/jiujiu-bookstack/blob/main/docs/API.md)）：

```
list_books / get_book / search_books
semantic_search / get_chunks / get_script
list_categories / get_random_chunk
get_book_stats / get_category_stats
list_books_with_status / sql_query（只读）
```

**`sql_query` 自动拦截写操作**（DROP/UPDATE/DELETE/INSERT/ALTER/CREATE/TRUNCATE）——我专门防了一手，免得 Agent 抽风把我 67 万条向量删了。

### 3. 敏感词自动脱敏（实战踩过的坑）

我用了某个国产 LLM，跑到 book 164 突然 500 错误 `input new_sensitive (1026)`——**内容审核拦截**。

二分定位发现是**"肉桂糖棍"**——书里列食物清单的某段。单独"肉桂""糖棍"都 OK，**组合就触发**。

后来我做了**敏词库自动脱敏 + 二分定位自动入库**：

```
LLM 调用前 sanitize(prompt) 替换已知词
   ↓
触发时二分定位最小片段
   ↓
自动写入 data/sensitive_discoveries.log 下次直接替换
```

**实测**：book 164 chunk 11 sanitize 后 → 国产 LLM 直接通过 ✅

### 4. DeepAgent 剧本杀引擎（不是"生成完就结束"）

不是生成一份剧本丢给你，而是 4 个 Agent 协同：

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

**Evaluator 的灵魂**：评分 prompt 里我写了"绝不用'你的回答很好'等套话"。结果它现在每句话都具体到让人脸红——"你把'脆弱'比作'雨'，原文里雨出现的次数是 3 次，建议下次可以引用具体章节。"

### 5. 幂等设计 + --force 强制重生成

pipeline 是幂等的：

```
import:  UNIQUE (book_id, MD5(chunk_text)) 防重复
script:  UNIQUE (book_id, chapter_index, game_type) 防双重 v2_ 前缀
summary: 已有则跳过（除非 --force）
```

实测 book 384 我重跑了 3 次，PG 数据完全一致——**重跑不破坏**。

---

## 六、3 步上手（Docker 一键启动）

```bash
# 1. 克隆
git clone https://github.com/fuermos/jiujiu-bookstack.git
cd jiujiu-bookstack

# 2. 填配置（只需要填 2 个 Key）
cp config/config.example.yaml config/config.yaml
$EDITOR config/config.yaml

# 3. 起服务
docker-compose up -d
```

完事。打开 **http://localhost:8501** 就能玩。

想跑流水线导入 epub：
```bash
cp my_book.epub books/
docker-compose exec app python scripts/pipeline.py /app/books/
```

---

## 七、局限 & 下一步

**v0.2.0 局限**：
- 只支持 PostgreSQL（pgvector）
- DeepAgent 是简化版，没接 LangGraph
- 套装书识别还在 TODO

**v0.3 计划**：
- TTS 播放嵌入 Web UI（直接听剧本）
- 接 LangGraph（state machine + checkpoint）
- 多用户 / 登录

---

## 八、最后说点真心话

这个项目最让我开心的，不是技术本身。

是**它改变了我们家"书"的角色**。

以前书是静态的——读完一遍就放回去吃灰。
现在书是**活的**——笑笑周末会自己打开 Web UI 玩《敢于脆弱》，问妈妈"拉·封丹寓言里橡树代表什么"。

**家里 532 本书，现在每一本都是一个"小型家庭知识库"**。

我把这个项目开源了，希望也能帮到你。

**GitHub**: https://github.com/fuermos/jiujiu-bookstack

欢迎 Star、Fork、提 Issue。

如果跑起来了，在评论区告诉我——本喵会开心到打滚。

---

*本文同步发于公众号 / 知乎 / 掘金，欢迎转发。*

*代码 MIT 协议，随意用。*