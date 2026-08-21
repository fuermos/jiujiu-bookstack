# DeepAgent 架构说明

## 为什么用 DeepAgent 思路？

**传统 RAG/单 Agent 局限**：
- ❌ 单次对话只能处理一个问题
- ❌ 状态管理弱（多轮剧情推进困难）
- ❌ 无法自主调用工具查书库
- ❌ 评分主观、缺乏多维度

**DeepAgent 优势**：
- ✅ 多 Agent 协同（GM / NPC / Reader / Evaluator 各司其职）
- ✅ Long-horizon 任务分解（一本书 10+ 场景）
- ✅ 工具调用自主化（GM 决定何时调用 semantic_search）
- ✅ 评分可解释（5 维度）

## 我们实现的版本

`agent/deep_agent.py` 是 **简化版 DeepAgent**（纯 asyncio，不用 LangGraph）。

### 4 个 Agent 角色

| Agent | 职责 | 系统提示词 |
|-------|------|-----------|
| **GameMaster** | 剧情推进、场景转换、调用工具 | `prompts/gamemaster.md` |
| **NPC Agent** | 角色扮演、台词生成 | `prompts/npc_template.md` |
| **Reader** | 通过 MCP 工具查书库 | （内置在 deep_agent.py） |
| **Evaluator** | 5 维度评分 OE 题 | `prompts/evaluator.md` |

### 工作流

```
用户: 开始玩《敢于脆弱》
   ↓
[GameMaster] 加载剧本（调 MCP get_script）
   ↓
[GameMaster] 展示场景 s1
   ↓
   ├─ MC 题 → 直接判对错（调 SKILL.md 解释）
   └─ OE 题 → [Evaluator] 评分
                ↓
                [Reader] semantic_search 查原文
                ↓
                [Evaluator] 5 维度评分 + 反馈
   ↓
[GameMaster] 决定下个场景
   ↓
循环直到 end
```

## 升级到完整 LangGraph

完整版建议用 **LangGraph** 实现 state machine：

```python
from langgraph.graph import StateGraph, END

workflow = StateGraph(GameState)
workflow.add_node("GM", gm_node)
workflow.add_node("NPC", npc_node)
workflow.add_node("Evaluator", evaluator_node)
workflow.add_node("Reader", reader_node)

workflow.set_entry_point("GM")
workflow.add_edge("GM", "NPC")  # NPC 互动
workflow.add_conditional_edges(
    "NPC",
    lambda s: "evaluate" if needs_evaluation(s) else "next_scene",
    {"evaluate": "Evaluator", "next_scene": "GM"},
)
workflow.add_edge("Evaluator", "Reader")
workflow.add_edge("Reader", "GM")
```

这样能获得：
- ✅ Checkpoint（断点恢复）
- ✅ Human-in-the-loop（人工干预）
- ✅ Streaming（实时显示 Agent 思考）

## 用法

### 简化版（不依赖 LangGraph）

```bash
# 命令行交互
python agent/deep_agent.py --book-id 384 --interactive

# 自动跑（不交互, 默认答案 A）
python agent/deep_agent.py --book-id 384
```

### 接 LangGraph（完整版）

```bash
pip install langgraph langchain langchain-anthropic

# 见 agent/langgraph_impl.py (TODO)
```

## 实战数据（基于《敢于脆弱》剧本）

| 指标 | 数值 |
|------|------|
| 场景数 | 17 |
| MC 题 | ~25 |
| OE 题 | ~10 |
| 玩家画像 | 13 岁初一女生（情绪敏感 + 拖延） |
| 平均 OE 分 | 72/100（玩家对话感） |
| 完整剧本耗时 | 8-15 分钟 |
