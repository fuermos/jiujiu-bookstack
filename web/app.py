#!/usr/bin/env python3
"""web/app.py - Streamlit Web UI for jiujiu-bookstack

启动: streamlit run web/app.py --server.port 8501

页面:
- 🏠 首页: 库统计 + 书单
- 🎮 剧本杀: 选书 → 玩剧本（场景对话 + 评分）
- 🔍 搜索: 语义搜书 / 搜原文
- 📊 书详情: SKILL.md / 摘要 / 思维导图

设计原则:
- 不重写 deep_agent 逻辑，直接通过 MCP stdio 调工具
- session_state 缓存 MCP session，避免每次重连
- 评分走 deep_agent._evaluate_answer（5 维度）
"""
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import streamlit as st

# 把 scripts/ 和 agent/ 加进 path，方便复用 deep_agent 和 MCP client
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'agent'))

# MCP stdio client
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# ============== MCP 连接管理 ==============

@st.cache_resource(show_spinner="🐾 连接 MCP server...")
def get_mcp_session():
    """全局缓存 MCP session（一次连接，多次使用）"""
    # 用同步上下文包异步，不能直接在 cache_resource 里 await
    # 改用 streamlit 的 session_state 持有
    raise NotImplementedError  # 占位，实际用 _MCP 类


class MCPClient:
    """异步 MCP client，包装在一个全局 loop 里给 streamlit 调用"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.session: Optional[ClientSession] = None
        self._cm = None

    def connect(self):
        server_params = StdioServerParameters(
            command='python',
            args=[str(ROOT / 'scripts' / 'mcp_server.py')],
        )
        self._cm = stdio_client(server_params)
        read, write = self.loop.run_until_complete(self._cm.__aenter__())
        session_cm = ClientSession(read, write)
        self.session = self.loop.run_until_complete(session_cm.__aenter__())
        self.loop.run_until_complete(self.session.initialize())

    def call(self, tool: str, args: dict) -> list:
        result = self.loop.run_until_complete(self.session.call_tool(tool, args))
        return result

    def parse(self, result) -> dict:
        """把 MCP 返回的 list[TextContent] 解析成 dict"""
        return json.loads(result[0].text)


@st.cache_resource(show_spinner="🐾 连接 MCP server...")
def get_mcp() -> MCPClient:
    client = MCPClient()
    client.connect()
    return client


# ============== 工具函数 ==============

def list_books(category: Optional[str] = None, limit: int = 50) -> list:
    mcp = get_mcp()
    args = {'limit': limit}
    if category and category != '全部':
        args['category'] = category
    result = mcp.call('list_books', args)
    return mcp.parse(result)


def get_book(book_id: int) -> dict:
    mcp = get_mcp()
    result = mcp.call('get_book', {'book_id': book_id})
    return mcp.parse(result)


def get_script(book_id: int) -> Optional[dict]:
    mcp = get_mcp()
    result = mcp.call('get_script', {'book_id': book_id, 'game_type': 'v2_mixed'})
    scripts = mcp.parse(result)
    if not scripts:
        return None
    return scripts[0].get('script_json')


def semantic_search(query: str, top_k: int = 5, book_id: Optional[int] = None) -> list:
    mcp = get_mcp()
    args = {'query': query, 'top_k': top_k}
    if book_id:
        args['book_id'] = book_id
    result = mcp.call('semantic_search', args)
    return mcp.parse(result)


def list_categories() -> list:
    mcp = get_mcp()
    result = mcp.call('list_categories', {})
    return mcp.parse(result)


def get_book_stats() -> dict:
    mcp = get_mcp()
    result = mcp.call('get_book_stats', {})
    return mcp.parse(result)


def search_books(query: str, limit: int = 20) -> list:
    mcp = get_mcp()
    result = mcp.call('search_books', {'query': query, 'limit': limit})
    return mcp.parse(result)


# ============== 评分（封装 deep_agent._evaluate_answer） ==============

def evaluate_answer(question: dict, answer: str, book_id: int) -> tuple[int, str]:
    """OE 题评分：先 semantic_search 原文，再用 LLM 5 维度评分"""
    try:
        from llm_client import LLMClient
        from config_loader import load_config
        config = load_config()
        llm = LLMClient(config['llm'])
    except Exception as e:
        return (50, f'（LLM 未配置，跳过评分: {e}）')

    # 1. semantic_search 查原文
    query = answer[:30] if len(answer) > 30 else answer
    context_chunks = semantic_search(query, top_k=2, book_id=book_id)
    context_text = '\n'.join(c.get('preview', '') for c in context_chunks[:2])

    # 2. LLM 评分（同步调用，因为 streamlit 在主线程）
    eval_prompt = question.get('evaluation_prompt', '5 维度评分 (深度/独特性/文本关联/真诚度/世界观)')
    user_msg = f'''{eval_prompt}

原文参考:
{context_text}

玩家回答: {answer}

请评分 (0-100), 然后给一句温暖鼓励。'''

    try:
        # llm_client.call 本身就是同步（用 requests）
        text, _ = llm.call(
            '你是温暖姐姐 + 语文老师, 5 维度评分, 先肯定再说建议, 最后一句鼓励。绝不用"你的回答很好"等套话。',
            user_msg,
            max_tokens=300,
        )
    except Exception as e:
        return (50, f'（LLM 调用失败: {e}）')

    import re
    m = re.search(r'(\d+)', text)
    score = int(m.group(1)) if m else 50
    return (min(100, max(0, score)), text)


# ============== Streamlit 页面 ==============

st.set_page_config(
    page_title='玖玖书塔 · JiujiuBookStack',
    page_icon='📚',
    layout='wide',
    initial_sidebar_state='expanded',
)

# 侧边栏导航
with st.sidebar:
    st.markdown('# 📚 玖玖书塔')
    st.caption('jiujiu-bookstack · v0.2.0')
    st.divider()
    page = st.radio('导航', ['🏠 首页', '🎮 剧本杀', '🔍 搜索', '📖 书详情'], index=0)
    st.divider()

    # 显示库统计
    try:
        stats = get_book_stats()
        st.metric('总书数', stats.get('total_books', '-'))
        st.metric('总 chunks', f"{stats.get('total_chunks', 0):,}")
        if 'vectorized_chunks' in stats:
            rate = 100 * stats['vectorized_chunks'] / max(1, stats['total_chunks'])
            st.metric('向量化率', f'{rate:.1f}%')
        if 'total_scripts' in stats:
            st.metric('剧本数', stats['total_scripts'])
    except Exception as e:
        st.error(f'统计加载失败: {e}')


# ====== 页面：首页 ======
if page == '🏠 首页':
    st.title('📚 玖玖书塔')
    st.markdown('''
> 一站式电子书知识库构建流水线：**丢进 epub，产出结构化知识图谱、SKILL 文档、可玩游戏化剧本、叙事化摘要**。
    ''')

    # 分类统计
    st.subheader('📊 分类分布')
    try:
        cats = list_categories()
        cols = st.columns(min(6, len(cats)))
        for i, cat in enumerate(cats[:12]):
            with cols[i % 6]:
                st.metric(cat.get('category', '?'), cat.get('count', 0))
    except Exception as e:
        st.error(str(e))

    st.divider()

    # 书单浏览
    st.subheader('📖 书架')
    try:
        cat_list = ['全部'] + [c.get('category', '?') for c in list_categories()]
        sel_cat = st.selectbox('筛选分类', cat_list)
        books = list_books(category=sel_cat if sel_cat != '全部' else None, limit=100)

        # 分两列展示
        for i in range(0, len(books), 2):
            cols = st.columns(2)
            for j, col in enumerate(cols):
                if i + j < len(books):
                    b = books[i + j]
                    with col:
                        with st.container(border=True):
                            st.markdown(f"**{b.get('name', '?')}**")
                            cat = b.get('category', '?')
                            st.caption(f"分类: {cat} · ID: {b.get('id', '?')}")
                            if b.get('summary'):
                                st.caption(b['summary'][:150] + ('...' if len(b.get('summary', '')) > 150 else ''))
                            if st.button('🎮 玩剧本', key=f"play_{b.get('id', i+j)}"):
                                st.session_state['selected_book_id'] = b.get('id')
                                st.session_state['selected_book_name'] = b.get('name')
                                st.rerun()
    except Exception as e:
        st.error(f'书单加载失败: {e}')


# ====== 页面：剧本杀 ======
elif page == '🎮 剧本杀':
    st.title('🎮 剧本杀 · 玩转一本书')

    # 选书
    try:
        books = list_books(limit=200)
    except Exception as e:
        st.error(f'加载书单失败: {e}')
        books = []

    # 用 session_state 记住选中的书
    if 'game_book_id' not in st.session_state:
        st.session_state.game_book_id = None
        st.session_state.game_book_name = None
        st.session_state.game_script = None
        st.session_state.game_scene_idx = 0
        st.session_state.game_history = []
        st.session_state.game_ended = False

    if st.session_state.game_book_id is None:
        st.markdown('### 👇 选一本书开始玩')
        book_options = {f"{b.get('name', '?')} (ID={b.get('id')})": b.get('id') for b in books if b.get('id')}
        sel = st.selectbox('选书', list(book_options.keys()))
        if st.button('🚀 开始玩'):
            st.session_state.game_book_id = book_options[sel]
            st.session_state.game_book_name = sel.split(' (ID=')[0]
            # 加载剧本
            with st.spinner('加载剧本...'):
                script = get_script(st.session_state.game_book_id)
                if script:
                    st.session_state.game_script = script
                    st.session_state.game_scene_idx = 0
                    st.session_state.game_history = []
                    st.session_state.game_ended = False
                else:
                    st.error('该书没有剧本（先生成再玩）')
                    st.session_state.game_book_id = None
            st.rerun()
    else:
        # 显示当前书
        book_id = st.session_state.game_book_id
        st.markdown(f"### 📖 《{st.session_state.game_book_name}》")
        if st.button('🔄 重玩 / 换书'):
            st.session_state.game_book_id = None
            st.session_state.game_script = None
            st.session_state.game_scene_idx = 0
            st.session_state.game_history = []
            st.session_state.game_ended = False
            st.rerun()

        script = st.session_state.game_script
        if not script or not script.get('scenes'):
            st.error('剧本为空或格式错误')
        else:
            scenes = script['scenes']
            idx = st.session_state.game_scene_idx

            if st.session_state.game_ended or idx >= len(scenes):
                # 结算
                st.success('🎬 剧本完成！')
                total = sum(h.get('score', 0) for h in st.session_state.game_history)
                n = max(1, len(st.session_state.game_history))
                avg = total / n
                c1, c2, c3 = st.columns(3)
                c1.metric('总分', f'{avg:.1f}/100')
                c2.metric('回答数', n)
                c3.metric('剧本', st.session_state.game_book_name)
                st.balloons()
            else:
                scene = scenes[idx]
                # 场景描述
                with st.container(border=True):
                    st.markdown(f"#### 🎬 场景 {idx+1}/{len(scenes)}: {scene.get('title', '?')}")
                    if scene.get('act'):
                        st.caption(f"幕: {scene['act']}")
                    if scene.get('narrator_intro'):
                        st.info(scene['narrator_intro'])
                    if scene.get('description'):
                        st.markdown(f"> {scene['description']}")

                # 处理该场景的所有问题
                questions = scene.get('questions', [])
                if not questions:
                    st.warning('该场景没有题目，自动跳到下一场景')
                    st.session_state.game_scene_idx += 1
                    st.rerun()

                # 一题一题来
                if 'scene_q_idx' not in st.session_state:
                    st.session_state.scene_q_idx = 0
                if 'scene_answers' not in st.session_state:
                    st.session_state.scene_answers = []

                q_idx = st.session_state.scene_q_idx

                if q_idx < len(questions):
                    q = questions[q_idx]
                    st.markdown(f"##### 问题 {q_idx+1}/{len(questions)} · `{q.get('type', '?')}`")
                    st.markdown(f"**{q.get('question', '')}**")

                    if q.get('type') == 'comprehension_mc':
                        # 单选题
                        opts = q.get('options', [])
                        if not opts:
                            st.warning('该题没有选项，跳过')
                            st.session_state.scene_q_idx += 1
                            st.rerun()
                        ans = st.radio('选择', [f"{chr(65+i)}. {o}" for i, o in enumerate(opts)], key=f'q_{idx}_{q_idx}')
                        # 取首字母
                        choice = ans.split('.')[0].strip()
                        if st.button('提交答案'):
                            correct = q.get('correct', '').upper()
                            is_correct = choice == correct
                            score = 100 if is_correct else 0
                            feedback = q.get('explanation', '')
                            st.session_state.scene_answers.append({
                                'question': q.get('question'),
                                'type': 'mc',
                                'answer': choice,
                                'correct': correct,
                                'score': score,
                                'feedback': feedback,
                            })
                            st.session_state.scene_q_idx += 1
                            st.rerun()
                    else:
                        # 开放题
                        ans = st.text_area('你的回答', key=f'q_{idx}_{q_idx}', height=100)
                        if st.button('提交答案'):
                            if not ans.strip():
                                st.warning('回答不能为空')
                            else:
                                with st.spinner('AI 老师评分中...'):
                                    score, feedback = evaluate_answer(q, ans, book_id)
                                st.session_state.scene_answers.append({
                                    'question': q.get('question'),
                                    'type': 'oe',
                                    'answer': ans,
                                    'score': score,
                                    'feedback': feedback,
                                })
                                st.session_state.scene_q_idx += 1
                                st.rerun()

                    # 显示上一题反馈
                    if st.session_state.scene_answers and q_idx > 0:
                        last = st.session_state.scene_answers[-1]
                        with st.container(border=True):
                            if last['type'] == 'mc':
                                if last.get('correct') == last.get('answer'):
                                    st.success(f"✅ 正确! {last.get('feedback', '')}")
                                else:
                                    st.error(f"❌ 正确答案是 {last.get('correct')}. {last.get('feedback', '')}")
                            else:
                                st.info(f"📊 评分: **{last.get('score', 0)}/100**\n\n{last.get('feedback', '')}")
                else:
                    # 当前场景所有题答完，进入下一场景
                    st.session_state.game_history.extend(st.session_state.scene_answers)
                    st.session_state.game_scene_idx += 1
                    st.session_state.scene_q_idx = 0
                    st.session_state.scene_answers = []
                    if st.session_state.game_scene_idx >= len(scenes):
                        st.session_state.game_ended = True
                    st.rerun()


# ====== 页面：搜索 ======
elif page == '🔍 搜索':
    st.title('🔍 搜索书库')

    tab1, tab2 = st.tabs(['搜书名', '语义搜原文'])

    with tab1:
        q = st.text_input('书名关键词')
        if q:
            try:
                results = search_books(q, limit=20)
                for r in results:
                    with st.container(border=True):
                        st.markdown(f"**{r.get('name', '?')}**")
                        st.caption(f"分类: {r.get('category', '?')} · ID: {r.get('id', '?')}")
                        if r.get('summary'):
                            st.caption(r['summary'][:200])
            except Exception as e:
                st.error(str(e))

    with tab2:
        q = st.text_input('语义查询（可以问开放式问题）')
        col1, col2 = st.columns(2)
        with col1:
            top_k = st.slider('返回数量', 3, 20, 5)
        with col2:
            try:
                books = list_books(limit=200)
                opts = {'全库': None}
                for b in books:
                    if b.get('id'):
                        opts[b.get('name', '?')[:40]] = b.get('id')
                sel = st.selectbox('限定书', list(opts.keys()))
                book_id = opts[sel]
            except Exception:
                book_id = None

        if q and st.button('🔍 搜索'):
            try:
                results = semantic_search(q, top_k=top_k, book_id=book_id)
                for i, r in enumerate(results, 1):
                    with st.container(border=True):
                        st.markdown(f"**结果 {i}** · 相关度 {r.get('score', 0):.2f}")
                        if r.get('book_name'):
                            st.caption(f"书: {r['book_name']}")
                        st.markdown(f"> {r.get('preview', '')[:400]}")
            except Exception as e:
                st.error(str(e))


# ====== 页面：书详情 ======
elif page == '📖 书详情':
    st.title('📖 书详情')

    try:
        books = list_books(limit=200)
        opts = {f"{b.get('name', '?')} (ID={b.get('id')})": b.get('id') for b in books if b.get('id')}
        sel = st.selectbox('选书', list(opts.keys()))
        if sel:
            book_id = opts[sel]
            book = get_book(book_id)
            st.markdown(f"### 《{book.get('name', '?')}》")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric('ID', book.get('id', '-'))
            c2.metric('分类', book.get('category', '-'))
            c3.metric('chunks', book.get('chunk_count', '-'))
            c4.metric('剧本', '有' if book.get('has_script') else '无')

            st.divider()
            st.subheader('📝 叙事摘要')
            st.markdown(book.get('summary', '（无摘要）'))

            # 思维导图（如有）
            mmd_path = ROOT / 'mindmaps' / f"{book.get('id')}.mmd"
            if mmd_path.exists():
                with st.expander('🗺️ 思维导图 (Mermaid)'):
                    st.code(mmd_path.read_text(encoding='utf-8'), language='mermaid')

            # SKILL.md（如有）
            skill_path = ROOT / 'skills' / f"book_{book.get('id')}_SKILL.md"
            if not skill_path.exists():
                skill_path = ROOT / 'data' / f"{book.get('id')}_SKILL.md"
            if skill_path.exists():
                with st.expander('📋 SKILL.md'):
                    st.markdown(skill_path.read_text(encoding='utf-8'))
    except Exception as e:
        st.error(str(e))


# ====== 页脚 ======
st.sidebar.divider()
st.sidebar.caption('''
🐾 jiujiu-bookstack v0.2.0
- GitHub: github.com/fuermos/jiujiu-bookstack
- MCP: 12 tools, stdio
- Stack: PG + bge-m3 + Streamlit
''')