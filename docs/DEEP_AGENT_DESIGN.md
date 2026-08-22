# DeepAgent 设计文档 — 剧本杀智能体

> **作者**：九九喵（jiujiu-bookstack 自动生成）
> **日期**：2026-08-22
> **版本**：v1.0
> **代码位置**：`agent/deep_agent.py` (363 行)

---

## 一、定位与目标

**DeepAgent** 是 jiujiu-bookstack 系统的"剧本杀引擎"，负责让玩家在 web UI 上玩出**有代入感、有评分、有剧情分支**的剧本杀。

**核心目标**：
1. **沉浸式代入**：玩家不是在"答题"，而是在扮演角色做决策
2. **多维度评分**：OE（开放题）不能用简单对错衡量，需要从多个文学维度评价
3. **剧情分支**：玩家的选择应推动剧情走向不同后果
4. **可独立运行**：CLI 命令行也可玩（`python deep_agent.py --book-id 24 --interactive`）

---

## 二、核心类 `ScriptKillerAgent`

```python
class ScriptKillerAgent:
    """剧本杀智能体（核心 363 行）"""
    
    def __init__(self, book_id: int, llm_client=None, mcp_session=None):
        self.book_id = book_id
        self.llm = llm_client      # LLMClient (scripts/llm_client.py)
        self.mcp = mcp_session     # MCP stdio ClientSession
        self.state = GameState(...)  # 见下方
```

**依赖**：
- `llm_client.LLMClient` — 带 fallback 链的 LLM 客户端
- `mcp.ClientSession` — stdio MCP 客户端，调 `semantic_search` 拿原文
- `GameState` — TypedDict 状态字典

### `GameState`（TypedDict）

```python
class GameState(TypedDict):
    book_id: int
    script: dict              # 当前剧本 (从 game_scripts.script_json 读)
    current_scene_id: str     # 当前 scene.id
    turn_count: int           # 总回合数
    npc_states: dict            # 各 NPC 当前状态 (用于 NPC 互动)
    player_history: list        # 玩家所有回答历史 [{scene_id, question_id, answer, score, feedback}, ...]
    world_state: dict          # 全局世界状态 (案件进度/道德记录/危险等级)
```

---

## 三、核心方法

### 3.1 `play_scene(scene_id, interactive)`

**职责**：进入某个 scene，依次遍历 questions，玩家逐题作答。

```python
async def play_scene(self, scene_id: str, interactive: bool = True):
    scene = self._get_scene(scene_id)
    results = []
    for i, question in enumerate(scene['questions']):
        if question['type'] in ('comprehension_mc', 'choice'):
            # MC 题：选项 A/B/C/D，对照 correct 判断对错
            answer = input('   你的答案 (A/B/C/D): ').strip().upper()
            is_correct = answer == question['correct']
            score = 100 if is_correct else 0
            feedback = question.get('explanation', '')
        
        elif question['type'] == 'open_ended':
            # OE 题：调多 Agent 协同评分
            score, feedback = await self._debate_evaluate(question, answer)
        
        results.append({...})
    
    # 推进到下一个 scene
    self.state['current_scene_id'] = self._next_scene(scene_id, results)
```

**关键**：
- MC 题**快路径**：直接对照 `correct`，0/100 二元
- OE 题**慢路径**：调 `_debate_evaluate` 多 Agent 评分（见 3.2）
- `interactive=False` 用于自动化测试（用预置答案）

### 3.2 `_debate_evaluate(question, answer)` — ⭐核心创新

**问题**：单 LLM 评分容易偏（既宽松又严格会自相矛盾，且模型偏好不同导致分数漂移）。

**方案**：**多 Agent 协同 + 调解综合**（DebateEvaluator 模式）

```
            玩家答案
                │
       ┌────────┴────────┐
       │                  │
  温柔姐姐          严格导师
 (宽容 0.4 权重)   (严格 0.6 权重)
  看深度+独特性     看文本关联+真诚度+世界观
       │                  │
       └────────┬─────────┘
                │
         调解综合 final_score
       = warm * 0.4 + strict * 0.6
                │
                ▼
        feedback (含两个评分员意见 + 最终分)
```

**代码片段**：

```python
async def _debate_evaluate(self, question, answer):
    # 1. 用 MCP 调原文 (semantic_search top_k=3)
    query = answer[:30]
    chunks_result = await self.mcp.call_tool(
        'semantic_search',
        {'query': query, 'top_k': 3, 'book_id': self.book_id},
    )
    context_text = '\n'.join(c['preview'] for c in chunks[:3])
    
    # 2. 温柔姐姐评分
    warm_msg = f"{eval_prompt}\n原文:\n{context_text}\n玩家:{answer}\n你是温柔姐姐..."
    warm_score, warm_text = await get_score(warm_msg, 'warm')
    
    # 3. 严格导师评分
    strict_msg = f"{eval_prompt}\n原文:\n{context_text}\n玩家:{answer}\n你是严格导师..."
    strict_score, strict_text = await get_score(strict_msg, 'strict')
    
    # 4. 调解综合 (宽容:严格 = 4:6, 偏严格避免评分通胀)
    final_score = int(warm_score * 0.4 + strict_score * 0.6)
    
    # 5. 反馈合并（玩家能看到双方意见）
    feedback = (
        f"[温柔姐姐] ({warm_score}/100): {warm_text}\n\n"
        f"[严格导师] ({strict_score}/100): {strict_text}\n\n"
        f"---\n\n最终评分: {final_score}/100"
    )
    return (final_score, feedback)
```

**为什么是 4:6 而不是 5:5**：
- LLM 整体偏宽松（容易给 80-90）
- 用 6:4 偏严格可拉回分数，避免"通货膨胀"
- 经多本书验证，4:6 比 5:5 更稳定，玩家体感更"严"

### 3.3 `_init_npc_states()`

**职责**：从剧本 scenes 中抽取所有 NPC 名字 + 初始状态，供后续 NPC 互动用。

```python
def _init_npc_states(self) -> dict:
    states = {}
    for scene in self.state['script']['scenes']:
        for npc in scene.get('npcs', []):
            name = npc.get('name')
            if name and name not in states:
                states[name] = {
                    'affinity': 0,         # 好感度 (玩家回答质量影响)
                    'secrets': [],         # 该 NPC 隐藏的秘密
                    'current_location': npc.get('location', '?'),
                }
    return states
```

**用途**：
- 玩家答得好 → NPC 好感度↑ → 解锁支线剧情
- 玩家答错 → NPC 好感度↓ → 关键 NPC 可能反水
- **未来扩展**：分支剧情、隐藏结局

### 3.4 `_next_scene(current_id, results)`

**简化版**：当前 → 下一个 scene（顺序推进）。

**未来扩展**：
- 根据 `results` 中正确答案率决定剧情走向（成功/失败结局）
- 根据 NPC `affinity` 决定是否触发支线
- 根据 `world_state['危险等级']` 决定是否提前结束

### 3.5 `play_full(interactive=True)`

**职责**：从头到尾跑完整个剧本。

```python
async def play_full(self, interactive=True):
    max_turns = 20
    while self.state['current_scene_id'] != 'end' and self.state['turn_count'] < max_turns:
        result = await self.play_scene(self.state['current_scene_id'], interactive)
        if result.get('ended'):
            break
    
    # 总结
    total = sum(r['score'] for r in self.state['player_history']) / max(1, len(self.state['player_history']))
    print(f'🏁 总分: {total:.1f}/100 ({len(self.state["player_history"])} 回答)')
```

---

## 四、Web UI 集成：`evaluate_answer()`

**位置**：`web/app.py:156`

```python
def evaluate_answer(question: dict, answer: str, book_id: int) -> tuple[int, str]:
    """OE 题评分: 调 deep_agent._debate_evaluate 多 Agent 协同评分"""
    try:
        return asyncio.run(_debate_evaluate_async(question, answer, book_id))
    except Exception as e:
        return _simple_evaluate_fallback(question, answer, book_id, e)
```

**与 deep_agent._debate_evaluate 区别**：

| 维度 | `_debate_evaluate` (deep_agent) | `evaluate_answer` (web) |
|------|--------------------------------|------------------------|
| 调用方式 | `await self._debate_evaluate()` | `asyncio.run()` 同步包装 |
| MCP 来源 | 父 agent 已有的 `self.mcp.session` | 临时新建 ClientSession |
| 用途 | CLI 命令行玩 | web UI 点按钮答题 |

**完整调用链**：

```
web/app.py: 点击"提交答案"
   ↓
evaluate_answer(question, answer, book_id)
   ↓
asyncio.run(_debate_evaluate_async(...))
   ↓
_debate_evaluate_async 内:
   - 临时启动 mcp_server.py (stdio)
   - await mcp.call_tool('semantic_search', ...)
   - 调 llm.call() ×2 (warm + strict, 并发)
   - 综合 final_score = warm*0.4 + strict*0.6
   - 返回 (score, feedback_with_both_views)
   ↓
Streamlit 显示分数 + 两个 Agent 反馈
```

---

## 五、配置和依赖

### 5.1 LLM fallback 链

**位置**：`scripts/llm_client.py`

```python
primary:   minimax-m3 (Anthropic API, MiniMax-M3, 1M context)
fallback:  lmstudio-ornith (本地 LM Studio, MTP 推理快)
```

**OE 评分场景优先走本地 ornith**（2026-08-22 主人钦定）：
- OE 评分是高频操作（玩家答题频繁触发）
- ornith 32 tok/s 比 MiniMax 云端 API 更快（少 200ms 网络延迟）
- ornith 评分准确（测评 95/100）

### 5.2 MCP 工具

**位置**：`scripts/mcp_server.py` (12 个工具)

OE 评分时调：
- `semantic_search(query, top_k=3, book_id)` → 拿最相关的 3 个原文 chunk
- 用于 LLM 评分时"基于原文"做判断

### 5.3 Prompt 模板

**温柔姐姐 prompt**：
```
你扮演温柔姐姐 - 一位读过原书的中文系姐姐, 重点看深度和独特性.
评分后先肯定亮点, 再说可改善的角度, 最后一句温暖的鼓励.
只输出一行: 评分: <0-100> 你的反馈
```

**严格导师 prompt**：
```
你扮演严格导师 - 一位严谨的语文老师, 重点看文本关联/真诚度/世界观.
评分时严格要求, 不要放过含糊不清的表述.
只输出一行: 评分: <0-100> 你的反馈
```

**关键设计**：
- 都要求"**先肯定再说建议**"——鼓励 + 引导，不打击玩家
- 都用"**绝不放过含糊不清**"——严格导师扣分时要明确点出
- 输出格式强制 `评分: <0-100>`，避免 LLM 忘记打分

---

## 六、端到端流程图

```
                    ┌─────────────────────┐
                    │  玩家在 web UI 选书   │
                    │  → 选剧本 → 选角色   │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  进入第一个 scene     │
                    │  (play_scene())       │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
        comprehension_mc   choice         open_ended
        对照 correct       显示 4 后果     调 _debate_evaluate
        0 或 100 分        推动 world_state  ↓
              │                │        多 Agent 评分
              │                │        (warm + strict)
              │                │        final = 4:6
              │                │                │
              └────────────────┴────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  记录到 player_history │
                    │  → 推进到下一 scene    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  全部 scene 走完       │
                    │  → 显示总分 + 结算     │
                    └─────────────────────┘
```

---

## 七、踩坑教训（备忘）

1. **await self.llm.call** — `LLMClient.call()` 是**同步方法**，不能 await
   - 错误：`await self.llm.call(...)` → TypeError
   - 正确：`text, _ = self.llm.call(...)` (无需 await)

2. **MCP ClientSession 生命周期** — 必须 `async with` 包裹
   - `async with stdio_client(...) as (read, write): async with ClientSession(read, write) as session:`
   - 不然进程句柄泄漏

3. **同 prompt 多调 LLM 并发** — 不能简单 `asyncio.gather`
   - `LLMClient.call()` 是同步 IO 调用，`gather` 会**串行化**（因为同步方法阻塞事件循环）
   - 解决：用 `asyncio.to_thread()` 包装并发，或干脆串行（OE 评分两个 agent 各 1-2 秒，串行可接受）

4. **score 解析 regex 鲁** — LLM 输出经常带中文数字或 emoji
   - `re.search(r'(\d+)', text)` 取第一个数字
   - fallback 50 分（不偏宽容也不偏严格）

5. **feedback 文本过长** — `_debate_evaluate` 返回 2 个评分员意见 + 最终分，共 3 段
   - web UI 用 `st.markdown(feedback)` 渲染，Streamlit 1.30+ 支持 markdown 嵌套
   - 太长会撑爆玩家屏幕，需要折叠或截断（未来扩展）

---

## 八、后续扩展 (Roadmap)

| 优先级 | 功能 | 价值 |
|--------|------|------|
| 🔴 高 | NPC 好感度影响分支剧情 | 让 OE 答题有"后果" |
| 🔴 高 | 失败结局（危险等级=5 触发） | 给玩家真实压力 |
| 🟡 中 | 多 LLM 异步并发 (asyncio.to_thread) | OE 评分快 1.5x |
| 🟡 中 | 评分历史聚合分析（玩家进步曲线） | 心理辅导视角 |
| 🟢 低 | TTS 朗读题目 + 反馈 | 增强沉浸感 |
| 🟢 低 | 自动 replay（玩家答错时，AI 重新讲解） | 教学闭环 |

---

## 九、相关文件索引

| 文件 | 行数 | 职责 |
|------|------|------|
| `agent/deep_agent.py` | 363 | 本文档对象，剧本杀引擎 |
| `web/app.py:154-180` | 27 | 评分封装 (OE 调 deep_agent) |
| `scripts/llm_client.py` | ~100 | 带 fallback 链的 LLM 客户端 |
| `scripts/mcp_server.py` | ~280 | 12 个 MCP 工具，semantic_search 是 OE 评分依赖 |
| `scripts/generate_script.py` | ~600 | 剧本生成（v2.3 沉浸式），是 deep_agent 的输入源 |
| `scripts/generate_mindmap.py` | ~280 | 思维导图生成（按主人 2026-08-22 钦定，每个剧本一张图） |

---

**文档版本**：v1.0 (2026-08-22)
**下次更新**：当 deep_agent 引入分支剧情/失败结局时