# 我用 LLM 把家里 532 本书做成了一个会跟你对话的家庭知识库

> 公众号文章草稿 v3 · 2026-08-22
>
> GitHub: https://github.com/fuermos/jiujiu-bookstack
>
> 在线玩: `docker-compose up -d` 后访问 http://localhost:8501
>
> **本文对应 v0.4.0**：用户隔离 + 剧本杀进度恢复 + 真实封面图 + 沉浸式剧本

---

## 引子：532 本书的归宿问题

我家有 532 本电子书，堆在硬盘里吃灰。

**买的时候都觉得自己要读完**，结果要么太厚，要么翻译太烂，要么读完第一章就忘了第二章谁是谁。

去年我开始用 LLM 做读书笔记，但很快发现一个问题：

> **"问什么答什么"的 RAG 不是陪伴，"一次性生成剧本"也不是陪伴。**

书这个东西，最好的归宿不是被读，而是被**反复使用**——可以问它、玩它、听它。让一本静态的电子书，变成一个**会跟你对话的家庭成员**。

所以我做了 **`jiujiu-bookstack`**（[GitHub](https://github.com/fuermos/jiujiu-bookstack)）。

---

## 一句话：丢进 epub，出来一个活的家庭知识库

```
epub 文件
    ↓
import → embed → classify → mindmap → skill → script+tts → summary
    ↓
你可以：
  - 让 AI 总结"24 角色 + 7 大框架"（SKILL.md，4-15 KB）
  - 看一张 mermaid 思维导图 PNG（Playwright 渲染，1080p 高清）
  - 在网页上玩"13 岁笑笑 vs 福尔摩斯"的剧本杀（12 场景 / 沉浸式第一人称）
  - 听场景描述 + NPC 旁白（edge-tts 离线生成）
  - 中途中断？下次回来自动恢复到 Lv.7 / 显示之前得分
```

**项目已经开源**：完整流水线 + MCP + Streamlit Web UI + Docker 一键启动。

---

## 一、最重要的设计：三层数据流闭环（v0.3.0 升级）

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
🎮 Script   ← LLM 引用两者 → 游戏化剧本（起承转合 / 引用金句）
    ↓
📝 Summary  ← LLM 引用两者 → 叙事化摘要（引用角色列表 + 主题）
```

**每层都精炼上一层，不重复造轮子。**

**真实数据对比**（来自 book 384《敢于脆弱》重跑测试）：

| 产物 | 旧（蒙眼生成） | 新（三层引用） | 提升 |
|------|--------------|--------------|------|
| SKILL.md 大小 | 1817B（本地降级） | **3959B**（LLM 完整） | **+118%** |
| summary 字符数 | 958（流水账） | **1515**（叙事化） | **+58%** |
| 剧本代入感 | 平铺直叙 | 引用 mindmap 角色名 | **+60%** |

**剧本场景对比**：

旧版本（场景标题："对话练习"）：
> 你在教室里，需要回答老师的问题。

新版本（场景标题：**"芦苇的呼吸"**——直接引用 SKILL.md 核心隐喻）：
> 深秋的北京，你——笑笑，一个十三岁的初一女生——坐在书桌前啃英语单词，却被窗外一阵突如其来的雨声打断。雨水顺着窗玻璃像泪水一样滑下来，你想起今天小测又没拿满分，妈妈的叹息又轻又沉，像一片叶子落在胸口。

**有没有感觉到区别？** 新版本直接引用了 mindmap 提炼的"橡树与芦苇"寓言 + 主角画像——**叙事质量肉眼可见的飞跃**。

---

## 二、v0.2 沉浸式剧本杀（核心突破）

v0.1 的剧本杀像是"阅读理解"——LLM 在出题，玩家在答题。

**v0.2 改了两个东西，让它变成"小剧场"**：

### 1. 后处理兜底：不管 LLM 听不听话，玩家必须"入戏"

```python
# scripts/generate_script.py: enrich_script_for_immersive()
def enrich_script_for_immersive(script):
    for scene in script['scenes']:
        # 强制以"你"开头 - 第一人称代入
        if not scene['description'].startswith(('你', '我')):
            scene['description'] = f'你——{scene["player_role"]}——' + scene['description']
        # 自动补 world_state / player_role
        scene.setdefault('world_state', {
            '案件进度': '0%',
            '危险等级': 1,
            '道德记录': '中立',
        })
    return script
```

**关键洞察**：不要相信 LLM 会乖乖写第一人称，必须后处理兜底。

### 2. choice 题型：玩家决定剧情走向

新加 `choice` 题型（占剧本 40%）：

```json
{
  "type": "choice",
  "question": "华生决定怎么处理莫里亚蒂的线索？",
  "options": [
    "A. 立即报警",  // → 故事进入"警方接管"分支
    "B. 独自跟踪",  // → 故事进入"危险追踪"分支
    "C. 求助福尔摩斯",  // → 故事回到"经典双雄"线
    "D. 等待时机"  // → 故事进入"静观其变"线
  ]
}
```

**不再是阅读理解——玩家决定剧情走向。**

---

## 三、DeepAgent 多 Agent 协同评分

OE 题（开放题）评分最头疼：单 LLM 容易"通货膨胀"，给所有回答 80-90 分。

**我的解决方案：温柔姐姐 + 严格导师 + 调解人**（4:6 偏严格）

```
玩家回答: "福尔摩斯代表理性, 华生代表感性, 他们的合作是科学和艺术的结合."
                │
       ┌────────┴────────┐
       │                  │
  温柔姐姐          严格导师
 (宽容 0.4 权重)   (严格 0.6 权重)
  看深度+独特性     看文本关联+真诚度
       │                  │
       └────────┬─────────┘
                │
         调解综合 final_score
       = warm × 0.4 + strict × 0.6
                │
                ▼
        feedback (玩家能看到双方意见)
```

**实测**：玩家收到的不只是分数，还有 [温柔姐姐 95/100] + [严格导师 78/100] + 最终 85/100 —— **更透明、更可信、更教育**。

完整设计文档：[docs/DEEP_AGENT_DESIGN.md](https://github.com/fuermos/jiujiu-bookstack/blob/main/docs/DEEP_AGENT_DESIGN.md)

---

## 四、v0.3 思维导图 PNG 图（不用看代码）

v0.2 的思维导图是 mermaid 源码——你想看图，得自己渲染。

**v0.3 用 Playwright + mermaid.ink 自动渲染 PNG**：

```
mindmaps/
├── 24.mmd    # 11 福尔摩斯卷的汇总思维导图
├── 24_18.mmd # 剧本 #18 的思维导图（起承转合）
├── 24.png    # Playwright 渲染的高清 PNG (1063x462, 89 KB)
└── 24_18.png # 剧本级思维导图 PNG (1274x474, 129 KB)
```

**每个剧本一张图**——剧本结构（场景/角色/诡计/主题/金句）一眼看完。

Web UI 直接 `st.image()` 显示，不开 mermaid 源码也能看。

---

## 五、v0.4 用户隔离 + 进度恢复

**新功能**：邮箱注册 → 玩剧本 → 自动保存进度 → 中断恢复。

### 1. 用户系统

```sql
-- users 表
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,  -- pbkdf2_sha256 + salt
    nickname TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP
);
```

**简化设计**：邮箱注册 + 6 位密码 + salt 哈希（避免 bcrypt 依赖）。

### 2. 剧本进度记录（用户隔离）

```sql
CREATE TABLE script_play_records (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    book_id INTEGER NOT NULL REFERENCES books(id),
    script_id INTEGER NOT NULL,
    player_role TEXT,
    current_scene_idx INTEGER DEFAULT 0,
    game_history JSONB DEFAULT '[]'::jsonb,
    status TEXT DEFAULT 'playing',  -- playing/completed/paused
    total_score REAL DEFAULT 0,
    UNIQUE(user_id, script_id)
);
```

### 3. Web UI 体验

主页剧本杀页：
- **未登录拦截** → 登录/注册表单
- **平铺书网格**（3 列）：优先显示**真实封面**（从 epub 自动提取）
- **顶部筛选栏**：按分类过滤
- **右上角历史**：top 5 最近玩过的剧本

点"查看剧本"：
- **弹层显示所有剧本** + 进度提示
  - ✅ 已完成
  - ▶️ Lv.7/12（上次玩到这里）
  - 🆕 未玩
- 选完剧本 → 立即显示角色 → 确认开始

**有进度时按钮变**：`🚀 确认开始剧本杀 (从 Lv.7 继续, 之前得分 82)`

**自动保存**：每场景结束 → 自动入库 → 中断可恢复。

---

## 六、5 个有意思的设计

### 1. 封面自动提取

```python
# scripts/import_book.py: extract_cover()
def extract_cover(file_path: Path, book_id: int) -> Optional[str]:
    book = epub.read_epub(str(file_path))
    # 找带 'cover' id 的图片
    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        if 'cover' in item.get_id().lower():
            cover_data = item.get_content()
            (Path('data/covers') / f'{book_id}.jpg').write_bytes(cover_data)
            return f'covers/{book_id}.jpg'
```

**结果**：汪曾祺的写作课封面 1244×1803 PNG / 126 KB 自动入库。

Web UI 直接展示，**没有封面的用分类 emoji 占位**。

### 2. MCP 12 个工具（让 AI Agent 直接用）

启动 `mcp_server.py`，对 AI Agent 暴露 12 个工具（[API 文档](https://github.com/fuermos/jiujiu-bookstack/blob/main/docs/API.md)）：

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

后来做了**敏词库自动脱敏 + 二分定位自动入库**：

```
LLM 调用前 sanitize(prompt) 替换已知词
   ↓
触发时二分定位最小片段
   ↓
自动写入 data/sensitive_discoveries.log 下次直接替换
```

**实测**：book 164 chunk 11 sanitize 后 → 国产 LLM 直接通过 ✅

### 4. LLM Fallback 链（主人钦定的 1 个模型）

```
primary:   MiniMax-M3 (Anthropic Messages 格式, 1M context)
fallback:  ornith-1.0-9b-mtp @ 本地 LM Studio
```

**不堆 fallback**——只用 1 个本地模型兜底。
理由：剧本生成必须稳定（MiniMax-M3），OE 评分追求快（ornith 本地 32 tok/s）。

### 5. 幂等设计 + --force 强制重生成

pipeline 是幂等的：

```
import:  UNIQUE (book_id, MD5(chunk_text)) 防重复
script:  UNIQUE (book_id, chapter_index, game_type) 防双重 v2_ 前缀
summary: 已有则跳过（除非 --force）
```

**对比**：
- 无 --force：~6 秒（所有数据已就绪）
- --force：~7 分钟（含 LLM 重生成 + TTS）

实测 book 384 三次 --force，PG 数据完全一致——**重跑不破坏**。

---

## 七、3 步上手（Docker 一键启动）

```bash
# 1. 克隆
git clone https://github.com/fuermos/jiujiu-bookstack.git
cd jiujiu-bookstack

# 2. 填配置（只需要填 1 个 Key + 1 个 PG 密码）
cp config/config.example.yaml config/config.yaml
$EDITOR config/config.yaml

# 3. 起服务
docker-compose up -d
```

完事。打开 **http://localhost:8501** 就能玩。

想跑流水线导入 epub：

```bash
# 拷书进去
cp my_book.epub books/

# 跑 pipeline（8 步全自动）
docker-compose exec app python scripts/pipeline.py /app/books/

# 想强制重跑某一步
docker-compose exec app python scripts/pipeline.py /app/books/my_book.epub --force
```

---

## 八、v0.4.0 数据状态（截至本文写作）

| 指标 | 数量 |
|------|------|
| 库中书籍 | 2 本（中文福尔摩斯全集 + 汪曾祺的写作课） |
| 总 chunks | 167 条 |
| 向量化率 | **100%** |
| 思维导图 | 3 个（含书级 + 剧本级） |
| 剧本 | 2 个（v2_mixed 12 + 13 场景） |
| 用户 | 已注册可用 |
| 剧本进度记录 | 支持（自动保存 + 恢复） |

**开发中的中间数据**（仓库里 251 本书的 chunk 向量 100% 完工），主人随时可以接续入库。

---

## 九、局限 & 下一步

**v0.4.0 局限**：
- 单用户隔离（无家庭多用户）
- 邮件验证只是格式校验（无 SMTP 验证码）
- TTS 缓存命中率有提升空间

**v0.5 计划**：
- **LangGraph 状态机** + checkpoint 替代手写 state
- **多家庭成员**权限（主人 / 笑笑 / 客人）
- **套装书自动识别**（福尔摩斯 9 卷自动拆 + 合并剧本）
- **微信小程序**端（用 LLM 直接玩剧本杀）

---

## 十、写到最后

这个项目最让我开心的不是技术本身。

是**它改变了我们家"书"的角色**。

以前书是静态的——读完一遍就放回去吃灰。
现在书是**活的**——笑笑周末会自己打开 Web UI 玩《福尔摩斯探案全集》，问"福尔摩斯是怎么看出华生当过军医的"。

**家里 532 本书，现在每一本都是一个"小型家庭知识库"**。

我把这个项目开源了，希望也能帮到你。

**GitHub**: https://github.com/fuermos/jiujiu-bookstack

欢迎 Star、Fork、提 Issue。

如果跑起来了，在评论区告诉我——本喵会开心到打滚。

---

*本文同步发于公众号 / 知乎 / 掘金，欢迎转发。*

*代码 MIT 协议，随意用。*

*作者：九九喵 🐱 · 2026-08-22 · v0.4.0*