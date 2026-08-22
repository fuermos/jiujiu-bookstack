#!/usr/bin/env python3
"""deep_agent.py - DeepAgent 风格剧本杀交互引擎

核心设计:
1. 多 Agent 协同:
   - GameMaster: 主控 agent, 负责剧情推进
   - NPCAgent: 角色扮演 agent (3 个 NPC 各一个)
   - Reader: 阅读陪伴 agent (查 SKILL.md / chunks)
   - Evaluator: 评分 agent (评分 OE 答案)
2. MCP 工具调用:
   - 通过 jiujiu-bookstack MCP 12 工具实时查书库
   - 用 semantic_search 找玩家引用的原文
3. Long-horizon 任务分解:
   - 每个场景是一个 sub-task
   - State machine 管理进度

使用:
    python agent/deep_agent.py --book-id 384
    python agent/deep_agent.py --book-id 384 --interactive   # 命令行交互

依赖:
    pip install langchain langchain-anthropic langgraph mcp  (可选, 也可不装走简化版)
"""
import asyncio
import json
import argparse
from typing import Optional, TypedDict, Annotated
from pathlib import Path

# MCP client (stdio)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# ====== State 定义 ======
class GameState(TypedDict):
    """游戏状态 (LangGraph state)"""
    book_id: int
    book_name: str
    script: dict
    current_scene_id: str
    player_history: list[dict]   # [{scene_id, question_id, answer, score, feedback}]
    npc_states: dict             # NPC 当前状态
    world_state: dict            # 世界观状态
    turn_count: int


# ====== DeepAgent 主类 ======
class ScriptKillerAgent:
    """剧本杀 DeepAgent (简化版, 不依赖 LangGraph, 纯 asyncio)

    完整版建议接 LangGraph: https://langchain-ai.github.io/langgraph/
    """

    def __init__(self, book_id: int, llm_client=None, mcp_session=None):
        self.book_id = book_id
        self.llm = llm_client
        self.mcp = mcp_session
        self.state: GameState = {
            'book_id': book_id,
            'book_name': '',
            'script': {},
            'current_scene_id': 's1',
            'player_history': [],
            'npc_states': {},
            'world_state': {},
            'turn_count': 0,
        }

    async def initialize(self):
        """从 MCP 加载剧本 + 书名"""
        # 拿书名
        book_result = await self.mcp.call_tool('get_book', {'book_id': self.book_id})
        self.state['book_name'] = json.loads(book_result[0].text)['name']

        # 拿剧本
        script_result = await self.mcp.call_tool(
            'get_script',
            {'book_id': self.book_id, 'game_type': 'v2_mixed'},
        )
        scripts = json.loads(script_result[0].text)
        if not scripts:
            raise ValueError(f'book {self.book_id} 无剧本')
        self.state['script'] = scripts[0]['script_json']

        # 初始化 NPC 状态
        self.state['npc_states'] = self._init_npc_states()
        print(f'✅ 剧本加载: {self.state["book_name"]} ({len(self.state["script"]["scenes"])} 场景)')

    def _init_npc_states(self) -> dict:
        """从剧本里提取 NPC"""
        npcs = {}
        for scene in self.state['script'].get('scenes', []):
            for q in scene.get('questions', []):
                npc_name = q.get('role_perspective', '')
                if npc_name and npc_name not in npcs:
                    npcs[npc_name] = {
                        'name': npc_name,
                        'mood': 'neutral',
                        'relationship': 0,  # 与玩家的关系值
                        'first_seen': scene['id'],
                    }
        return npcs

    async def play_scene(self, scene_id: str, interactive: bool = True) -> dict:
        """玩一个场景

        Returns: {scene_id, results: [{question, answer, score, feedback}], ended: bool}
        """
        scene = self._get_scene(scene_id)
        if not scene:
            return {'scene_id': scene_id, 'results': [], 'ended': True}

        print(f'\n{"="*60}')
        print(f'🎬 场景 {scene["id"]}: {scene["title"]}')
        print(f'   ({scene["act"]}幕) {scene["narrator_intro"]}')
        print(f'   {scene["description"][:200]}...')
        print(f'{"="*60}')

        results = []
        for i, question in enumerate(scene.get('questions', []), 1):
            print(f'\n[问题 {i}/{len(scene["questions"])}] ({question["type"]})')
            print(f'  {question["question"]}')

            if question['type'] == 'comprehension_mc':
                for j, opt in enumerate(question['options']):
                    print(f'   {chr(65+j)}. {opt}')

                if interactive:
                    answer = input('   你的答案 (A/B/C/D): ').strip().upper()
                else:
                    answer = 'A'

                is_correct = answer == question['correct']
                score = 100 if is_correct else 0
                feedback = question.get('explanation', '')
                print(f'   {"✅ 正确!" if is_correct else "❌ 错误"} {feedback}')

                results.append({
                    'question_id': question.get('id', f'{scene_id}_q{i}'),
                    'type': 'mc',
                    'answer': answer,
                    'correct': question['correct'],
                    'score': score,
                    'feedback': feedback,
                })

            elif question['type'] == 'open_ended':
                if interactive:
                    answer = input('   你的回答: ').strip()
                else:
                    answer = '我选择勇敢地面对脆弱'

                # DebateEvaluator 多 agent 协同评分 (温柔姐姐 + 严格导师 + 调解)
                score, feedback = await self._debate_evaluate(question, answer)
                print(f'   📊 评分: {score}/100')
                print(f'   💬 {feedback}')

                results.append({
                    'question_id': question.get('id', f'{scene_id}_q{i}'),
                    'type': 'oe',
                    'answer': answer,
                    'score': score,
                    'feedback': feedback,
                })

        self.state['current_scene_id'] = self._next_scene(scene_id, results)
        self.state['turn_count'] += 1
        self.state['player_history'].extend(results)

        return {'scene_id': scene_id, 'results': results, 'ended': False}

    async def _evaluate_answer(self, question: dict, answer: str) -> tuple[int, str]:
        """Evaluator agent: 评分 OE 答案

        5 维度: 深度/独特性/文本关联/真诚度/世界观对齐
        """
        if not self.llm:
            return (50, '（未配置 LLM, 跳过评分）')

        # 用 MCP 查原文（如果玩家答案里引用了某些词）
        # 简单实现: 截取答案前 30 字符当查询词
        query = answer[:30] if len(answer) > 30 else answer
        try:
            chunks_result = await self.mcp.call_tool(
                'semantic_search',
                {'query': query, 'top_k': 2, 'book_id': self.book_id},
            )
            context = json.loads(chunks_result[0].text)
            context_text = '\n'.join(c.get('preview', '') for c in context[:2])
        except Exception:
            context_text = ''

        # 调 LLM 评分
        eval_prompt = question.get('evaluation_prompt', '5 维度评分')
        user_msg = f'''{eval_prompt}

原文参考:
{context_text}

玩家回答: {answer}

请评分 (0-100), 然后给一句温暖鼓励。'''

        text, _ = self.llm.call(
            '你是温暖姐姐 + 语文老师, 5 维度评分 (深度/独特性/文本关联/真诚度/世界观), 先肯定再说建议, 最后一句鼓励。绝不用"你的回答很好"等套话。',
            user_msg,
            max_tokens=300,
        )

        # 解析分数 (假设 LLM 输出 "评分: 80")
        import re
        m = re.search(r'(\d+)', text)
        score = int(m.group(1)) if m else 50
        return (min(100, max(0, score)), text)

    async def _debate_evaluate(self, question: dict, answer: str) -> tuple[int, str]:
        """多 Agent 协同评分 (Debate 模式)

        3 个评分员各自打分 + 调解综合:
        - 温柔姐姐 (Warm) - 看深度和独特性，宽容
        - 严格导师 (Strict) - 看文本关联和真诚度，严格
        - 调解人 (Mediator) - 综合两者，给最终评分和反馈

        好处: 减少单 LLM 偏差，更准更稳
        """
        if not self.llm:
            return (50, "(未配置 LLM, 跳过评分)")

        # 1. 调原文 (MCP semantic_search)
        query = answer[:30] if len(answer) > 30 else answer
        context_text = ""
        try:
            chunks_result = await self.mcp.call_tool(
                "semantic_search",
                {"query": query, "top_k": 3, "book_id": self.book_id},
            )
            context = json.loads(chunks_result[0].text)
            context_text = "\n".join(c.get("preview", "") for c in context[:3])
        except Exception:
            pass

        eval_prompt = question.get("evaluation_prompt", "5 维度评分 (深度/独特性/文本关联/真诚度/世界观)")

        # 2. 温柔姐姐评分 (宽容维度: 深度 + 独特性)
        warm_msg = (
            eval_prompt + "\n\n"
            "原文参考:\n" + context_text + "\n\n"
            "玩家回答: " + answer + "\n\n"
            "你扮演温柔姐姐 - 一位读过原书的中文系姐姐, 重点看深度和独特性.\n"
            "评分后先肯定亮点, 再说可改善的角度, 最后一句温暖的鼓励.\n"
            "只输出一行: 评分: <0-100> 你的反馈"
        )

        # 3. 严格导师评分 (严格维度: 文本关联 + 真诚度 + 世界观)
        strict_msg = (
            eval_prompt + "\n\n"
            "原文参考:\n" + context_text + "\n\n"
            "玩家回答: " + answer + "\n\n"
            "你扮演严格导师 - 一位严谨的语文老师, 重点看文本关联/真诚度/世界观.\n"
            "评分时严格要求, 不要放过含糊不清的表述.\n"
            "只输出一行: 评分: <0-100> 你的反馈"
        )

        # 4. 并发调两个 agent
        import re
        async def get_score(prompt: str, role: str):
            sys_prompt = (
                "你是温暖姐姐 + 语文老师, 5 维度评分, 先肯定再说建议, 最后一句鼓励. 绝不用 你的回答很好 等套话."
                if role == "warm"
                else "你是严格的语文老师, 评分必须基于原文, 不接受套话和空话."
            )
            text, _ = self.llm.call(sys_prompt, prompt, max_tokens=300)
            m = re.search(r"(\d+)", text)
            score = int(m.group(1)) if m else 50
            return min(100, max(0, score)), text

        warm_score, warm_text = await get_score(warm_msg, "warm")
        strict_score, strict_text = await get_score(strict_msg, "strict")

        # 5. 调解综合 (宽松 4 : 严格 6)
        final_score = int(warm_score * 0.4 + strict_score * 0.6)

        # 6. 反馈 = 两个评分员 + 最终分
        feedback = (
            "[温柔姐姐] (" + str(warm_score) + "/100): " + warm_text.strip() + "\n\n"
            "[严格导师] (" + str(strict_score) + "/100): " + strict_text.strip() + "\n\n"
            "---\n\n"
            "最终评分: " + str(final_score) + "/100"
        )

        return (final_score, feedback)


    def _get_scene(self, scene_id: str) -> Optional[dict]:
        for s in self.state['script'].get('scenes', []):
            if s['id'] == scene_id:
                return s
        return None

    def _next_scene(self, current_id: str, results: list) -> str:
        """决定下一个场景

        简化: 顺序推进, 分支点根据答案正确性决定
        """
        scenes = self.state['script']['scenes']
        for i, s in enumerate(scenes):
            if s['id'] == current_id:
                if i + 1 < len(scenes):
                    return scenes[i + 1]['id']
                break
        return 'end'

    async def play_full(self, interactive: bool = True):
        """玩完整剧本"""
        await self.initialize()

        max_turns = 20
        while self.state['current_scene_id'] != 'end' and self.state['turn_count'] < max_turns:
            result = await self.play_scene(self.state['current_scene_id'], interactive)
            if result.get('ended'):
                break

        # 总结
        total_score = sum(r['score'] for r in self.state['player_history']) / max(1, len(self.state['player_history']))
        print(f'\n{"="*60}')
        print(f'🏁 剧本完成!')
        print(f'   总分: {total_score:.1f}/100')
        print(f'   回答数: {len(self.state["player_history"])}')
        print(f'{"="*60}')


async def main():
    parser = argparse.ArgumentParser(description='jiujiu-bookstack DeepAgent 剧本杀')
    parser.add_argument('--book-id', type=int, required=True, help='剧本的书 ID')
    parser.add_argument('--interactive', action='store_true', help='命令行交互模式')
    args = parser.parse_args()

    # 启动 MCP stdio server 子进程
    server_params = StdioServerParameters(
        command='python',
        args=[str(Path(__file__).parent / '..' / 'scripts' / 'mcp_server.py')],
    )

    # 启动 LLM client (可选)
    try:
        from llm_client import LLMClient
        from config_loader import load_config
        config = load_config()
        llm = LLMClient(config['llm'])
    except Exception as e:
        print(f'⚠️  LLM 未配置 ({e}), 跳过评分')
        llm = None

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            agent = ScriptKillerAgent(args.book_id, llm, session)
            await agent.play_full(interactive=args.interactive)


if __name__ == '__main__':
    asyncio.run(main())
