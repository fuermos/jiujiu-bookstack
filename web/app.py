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
from user_manager import (
    register_user, login_user, get_user,
    save_progress, load_progress, list_user_history, delete_progress,
)

# 初始化 DB 连接池 (user_manager 直连 DB)
from db import init_pool as _init_db_pool
from config_loader import load_config as _load_cfg
try:
    _cfg = _load_cfg()
    _init_db_pool(_cfg['database'])
except Exception as _e:
    print(f'⚠️ DB pool init 失败: {_e}')


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

    def call(self, tool: str, args: dict):
        result = self.loop.run_until_complete(self.session.call_tool(tool, args))
        return result

    def parse(self, result) -> dict:
        """把 MCP 返回的 CallToolResult 解析成 dict

        MCP 2.0 SDK 返回的是 CallToolResult 对象，包含 .content (list[TextContent]) 和 .isError
        """
        return json.loads(result.content[0].text)


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


def list_scripts(book_id: int) -> list:
    """列某本书所有剧本 (id + chapter_index + game_type + n_scenes)"""
    mcp = get_mcp()
    result = mcp.call('get_script', {'book_id': book_id, 'game_type': 'v2_mixed'})
    scripts = mcp.parse(result)
    out = []
    for s in (scripts or []):
        sj = s.get('script_json', {})
        out.append({
            'id': s.get('id'),  # 透传 game_scripts.id 给上层 (进度恢复用)
            'chapter_index': s.get('chapter_index', 0),
            'game_type': s.get('game_type', ''),
            'n_scenes': len(sj.get('scenes', [])),
            'script_json': sj,
        })
    return out


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
    """OE 题评分：调 deep_agent._debate_evaluate 多 Agent 协同评分

    3 个评分员: 温柔姐姐 + 严格导师 + 调解人
    - 温柔姐姐看深度/独特性 (宽容)
    - 严格导师看文本关联/真诚度 (严格)
    - 调解人综合两者加权 (40:60)

    Fallback: 若 deep_agent 初始化失败，退回单 LLM 直评
    """
    try:
        return asyncio.run(_debate_evaluate_async(question, answer, book_id))
    except Exception as e:
        return _simple_evaluate_fallback(question, answer, book_id, e)


async def _debate_evaluate_async(question: dict, answer: str, book_id: int) -> tuple[int, str]:
    """调 deep_agent._debate_evaluate 多 Agent 评分"""
    import sys
    from pathlib import Path as P
    sys.path.insert(0, str(P(__file__).parent.parent / 'agent'))

    from llm_client import LLMClient
    from config_loader import load_config
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    config = load_config()
    llm = LLMClient(config['llm'])

    # MCP stdio 起 server (web 容器里脚本路径是 /app/scripts)
    mcp_params = StdioServerParameters(
        command='python',
        args=['/app/scripts/mcp_server.py'],
    )

    async with stdio_client(mcp_params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            from deep_agent import ScriptKillerAgent
            agent = ScriptKillerAgent(book_id=book_id, llm_client=llm, mcp_session=session)
            return await agent._debate_evaluate(question, answer)


def _simple_evaluate_fallback(question: dict, answer: str, book_id: int, prev_err) -> tuple[int, str]:
    """DeepAgent 失败时的单 LLM 评分 fallback"""
    try:
        from llm_client import LLMClient
        from config_loader import load_config
        config = load_config()
        llm = LLMClient(config['llm'])
    except Exception as e:
        return (50, f'(LLM 未配置, 跳过评分: {e})')

    query = answer[:30] if len(answer) > 30 else answer
    context_chunks = semantic_search(query, top_k=2, book_id=book_id)
    context_text = '\n'.join(c.get('preview', '') for c in context_chunks[:2])
    eval_prompt = question.get('evaluation_prompt', '5 维度评分')

    user_msg = (
        eval_prompt + '\n\n原文参考:\n' + context_text + '\n\n玩家回答: ' + answer
        + '\n\n请评分 (0-100), 然后给一句温暖鼓励.'
    )

    try:
        text, _ = llm.call(
            "你是温暖姐姐 + 语文老师, 5 维度评分, 先肯定再说建议, 最后一句鼓励. 绝不用 你的回答很好 等套话.",
            user_msg, max_tokens=300,
        )
    except Exception as e:
        return (50, f'(LLM 调用失败: {e}; deep_agent 错误: {prev_err})')

    import re
    m = re.search(r'(\d+)', text)
    score = int(m.group(1)) if m else 50
    return (min(100, max(0, score)), text)


# ============== 在线 TTS (edge-tts) ==============

def tts_generate(text: str, voice: str = 'zh-CN-XiaoxiaoNeural', rate: str = '+0%') -> Optional[bytes]:
    """在线生成 TTS 音频 (edge-tts 免费, 不要 key)
    
    Returns: mp3 bytes or None (失败时)
    """
    try:
        import edge_tts
        import asyncio
        
        async def _gen():
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            buf = bytearray()
            async for chunk in communicate.stream():
                if chunk['type'] == 'audio':
                    buf.extend(chunk['data'])
            return bytes(buf)
        
        return asyncio.run(_gen())
    except Exception as e:
        print(f'TTS 失败: {e}')
        return None


def render_tts_button(text: str, key: str, voice_label: str = '🎙️ 听旁白', voice: str = 'zh-CN-XiaoxiaoNeural', rate: str = '+0%'):
    """渲染 TTS 按钮 + 音频播放 (用 cache 避免重复生成)
"""
    if not text or not text.strip():
        return
    cache_key = f'tts_{key}_{voice}_{rate}_{hash(text)}'
    if cache_key in st.session_state:
        audio_bytes = st.session_state[cache_key]
        if audio_bytes:
            st.audio(audio_bytes, format='audio/mp3', autoplay=False)
            return
    if st.button(voice_label, key=f'btn_{key}'):
        with st.spinner('生成语音...'):
            audio_bytes = tts_generate(text, voice, rate)
        if audio_bytes:
            st.session_state[cache_key] = audio_bytes
            st.audio(audio_bytes, format='audio/mp3', autoplay=True)
        else:
            st.warning('TTS 生成失败')


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
    # 处理强制跳转 (主页"玩剧本"按钮触发)
    pages = ['🏠 首页', '🎮 剧本杀', '🔍 搜索', '📖 书详情']
    default_idx = 0
    if st.session_state.get('force_page'):
        try:
            default_idx = pages.index(st.session_state['force_page'])
        except ValueError:
            default_idx = 0
    page = st.radio('导航', pages, index=default_idx)
    # 消费掉 force_page
    if st.session_state.get('force_page'):
        st.session_state.pop('force_page', None)
    st.divider()

    # 用户登录状态
    if 'current_user' not in st.session_state:
        st.session_state.current_user = None
    user = st.session_state.get('current_user')
    if user:
        st.markdown(f"### 👤 {user['nickname']}")
        st.caption(f"📧 {user['email']}")
        if st.button('🚪 退出登录', use_container_width=True):
            st.session_state.current_user = None
            st.session_state.game_book_id = None
            st.rerun()
    else:
        st.caption('🔓 未登录')
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


def render_auth_form():
    """邮箱注册/登录 (主人 2026-08-22 钦定: 邮箱注册 + 简单验证)"""
    tab_login, tab_register = st.tabs(['登录', '注册'])

    with tab_login:
        with st.form('login_form'):
            email = st.text_input('邮箱', key='login_email')
            pwd = st.text_input('密码', type='password', key='login_pwd')
            if st.form_submit_button('🔓 登录', type='primary'):
                if not email or not pwd:
                    st.error('请填邮箱和密码')
                else:
                    res = login_user(email, pwd)
                    if 'error' in res:
                        st.error(res['error'])
                    else:
                        st.session_state.current_user = res
                        st.success(f'✅ 欢迎回来 {res["nickname"]}!')
                        st.rerun()

    with tab_register:
        with st.form('register_form'):
            email = st.text_input('邮箱', key='reg_email', help='例如：me@163.com')
            nickname = st.text_input('昵称 (可选)', key='reg_nick', help='默认用邮箱前缀')
            pwd = st.text_input('密码', type='password', key='reg_pwd', help='至少 6 位')
            pwd2 = st.text_input('确认密码', type='password', key='reg_pwd2')
            if st.form_submit_button('🎉 注册', type='primary'):
                if not email or not pwd:
                    st.error('请填邮箱和密码')
                elif pwd != pwd2:
                    st.error('两次密码不一致')
                else:
                    res = register_user(email, pwd, nickname)
                    if 'error' in res:
                        st.error(res['error'])
                    else:
                        st.session_state.current_user = res
                        st.success(f'✅ 注册成功, 欢迎 {res["nickname"]}!')
                        st.rerun()


# ============== Modal: 选剧本 & 选角色 (Streamlit 1.31+ @st.dialog) ==============

@st.dialog('🎭 选剧本 & 选角色', width='large')
def _script_selector_modal(book_id: int):
    """选剧本 → 选角色 → 开始游戏

    历史教训 (2026-08-23 主人反馈截图): 之前用自定义 HTML/CSS modal (行 553-697),
    但 Streamlit 的 st.markdown 不会嵌套 HTML div, 导致 .script-modal-shell 容器是空的,
    只有深色背景显示出来 = 黑色横条挡住内容。改用 @st.dialog 后, Streamlit 自动管理
    backdrop + 居中 + ESC 关闭, 不依赖任何 HTML/CSS hack。
    """
    sel_book = get_book(book_id)
    if not sel_book:
        st.error('书不存在')
        if st.button('关闭', key='dlg_no_book'):
            st.session_state.show_script_modal = False
            st.session_state.selected_book_id = None
            st.rerun()
        return

    st.markdown(f"### 🎭 《{sel_book['name']}》")
    st.caption('选个剧本，挑个角色，然后穿进故事里。')

    scripts_list = list_scripts(book_id)
    if not scripts_list:
        st.warning('这本书还没有剧本。')
        if st.button('✖ 关闭', key='dlg_empty', use_container_width=True):
            st.session_state.show_script_modal = False
            st.session_state.selected_book_id = None
            st.session_state.selected_script_id = None
            st.session_state.player_role_radio = None
            st.rerun()
        return

    user_id = st.session_state.current_user['id']
    progress_map = {}
    for s in scripts_list:
        p = load_progress(user_id, book_id, s['id'])
        if p:
            progress_map[s['id']] = p

    st.markdown('#### 📜 选个剧本')
    n_cols = min(3, len(scripts_list))
    cols = st.columns(n_cols)
    for i, s in enumerate(scripts_list):
        with cols[i % n_cols]:
            label = f"第 {s['chapter_index']+1} 册 · {s['n_scenes']} 场景"
            p = progress_map.get(s['id'])
            if p:
                if p['status'] == 'completed':
                    label = f"✅ 第 {s['chapter_index']+1} 册 · 已通关"
                else:
                    label = f"▶️ 第 {s['chapter_index']+1} 册 · Lv.{p['current_scene_idx']+1}/{s['n_scenes']}"
            btn_type = 'primary' if st.session_state.selected_script_id == s['id'] else 'secondary'
            if st.button(label, key=f'dlg_ch_{s["id"]}', use_container_width=True, type=btn_type):
                st.session_state.selected_script_id = s['id']
                st.rerun()

    # 选完剧本 → 选角色
    if st.session_state.selected_script_id:
        target_script = next((s for s in scripts_list if s['id'] == st.session_state.selected_script_id), scripts_list[0])
        script_json = target_script['script_json']
        role_options = script_json.get('_player_role_options', [])

        # ★ B 防御 (2026-08-23 主人反馈 bug fix):
        # 1. 如果 _player_role_options 为空, 从 scenes 扫 role_perspective fallback
        # 2. 如果 _player_role_options 含"跨书串场"角色 (如汪曾祺剧本里出现华生/福尔摩斯),
        #    强制以 scenes 为准, 避免给玩家看到错位角色
        SUSPICIOUS_CHARS = {'华生', '福尔摩斯', '莫斯坦', '巴塞洛缪', '斯莫尔', '华生医生', '哈利', '赫敏', '罗恩', '霍格沃茨', '江户川', '柯南', '毛利'}
        has_foreign = any(any(sc_word in r for sc_word in SUSPICIOUS_CHARS) for r in role_options)
        if not role_options or has_foreign:
            seen = set()
            for sc in script_json.get('scenes', []):
                for q in sc.get('questions', []):
                    rp = (q.get('role_perspective') or '').strip()
                    if rp:
                        seen.add(rp)
            computed = sorted(seen)
            if has_foreign:
                st.warning(f'⚠️ 检测到剧本角色数据错位（_player_role_options 含{"、".join(repr(r) for r in role_options if any(sc in r for sc in SUSPICIOUS_CHARS))}），已自动以场景实际角色为准。')
            role_options = computed or ['主角', '书童', '旁白']

        st.markdown('#### 🎭 在这个故事里，你是谁？')
        # 如果 radio 与当前书的可选角色不匹配（切书场景），重置
        if st.session_state.player_role_radio not in role_options:
            st.session_state.player_role_radio = role_options[0]
        chosen_role = st.session_state.player_role_radio
        cols = st.columns(min(4, len(role_options)))
        for k, role in enumerate(role_options):
            with cols[k % len(cols)]:
                btn_type = 'primary' if role == chosen_role else 'secondary'
                if st.button(role, key=f'dlg_role_{role}', use_container_width=True, type=btn_type):
                    st.session_state.player_role_radio = role
                    st.rerun()

        # 确认开始
        p = progress_map.get(target_script['id'])
        resume_text = ''
        if p and p['status'] == 'playing':
            resume_text = f' (从 Lv.{p["current_scene_idx"]+1} 接着玩，之前得分 {p["total_score"]:.0f})'
        elif p and p['status'] == 'completed':
            resume_text = ' (上次通关了，这次从头来)'

        st.markdown('---')
        btn_label = f'🚀 推开这扇门{resume_text}'
        if st.button(btn_label, key='dlg_start_play', type='primary', use_container_width=True):
            st.session_state.game_book_id = book_id
            st.session_state.game_book_name = sel_book['name']
            st.session_state.game_script_id = target_script['id']
            st.session_state.player_role = chosen_role
            st.session_state.game_script = script_json
            if p and p['status'] == 'playing':
                st.session_state.game_scene_idx = p['current_scene_idx']
                st.session_state.game_history = p['game_history'] or []
                st.info(f'✅ 接着上次的进度: Lv.{p["current_scene_idx"]+1}')
            else:
                st.session_state.game_scene_idx = 0
                st.session_state.game_history = []
            st.session_state.game_ended = False
            st.session_state.show_script_modal = False
            st.session_state.selected_book_id = None
            st.session_state.selected_script_id = None
            st.session_state.player_role_radio = None
            st.rerun()

    st.markdown('---')
    if st.button('✖ 退出去', key='dlg_close', use_container_width=True):
        st.session_state.show_script_modal = False
        st.session_state.selected_book_id = None
        st.session_state.selected_script_id = None
        st.session_state.player_role_radio = None
        st.rerun()


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
        if not cats:
            st.info('尚无分类数据 — 跑一次 pipeline 后会显示')
        else:
            cols = st.columns(min(6, max(1, len(cats))))
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

        # 过滤: 只显示有剧本的书 (用户的书架 = 可玩剧本的列表)
        books = [b for b in books if b.get('has_script')]
        if not books:
            st.info('书架上还没有可玩的剧本。请先生成 pipeline (在分类页 / 处理新书)。')
            st.stop()
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
                            st.caption(f"分类: {cat}")
                            if b.get('summary'):
                                st.caption(b['summary'][:150] + ('...' if len(b.get('summary', '')) > 150 else ''))
                            # 没剧本的书: 不能玩, 提示先生成
                            if not b.get('has_script'):
                                st.button('🚧 暂无剧本', key=f"no_play_{b.get('id', i+j)}", disabled=True, help='先生成 pipeline 再玩')
                            else:
                                if st.button('🎮 玩剧本', key=f"play_{b.get('id', i+j)}"):
                                    st.session_state['selected_book_id'] = b.get('id')
                                    st.session_state['selected_book_name'] = b.get('name')
                                    # 跳到剧本杀页
                                    st.session_state['force_page'] = '🎮 剧本杀'
                                    st.rerun()
    except Exception as e:
        st.error(f'书单加载失败: {e}')


# ====== 页面：剧本杀 ======
elif page == '🎮 剧本杀':
    st.title('🎮 剧本杀 · 玩转一本书')

    # ========= 1. 未登录拦截 =========
    if not st.session_state.get('current_user'):
        st.warning('🔒 请先登录/注册才能玩剧本杀')
        render_auth_form()
        st.stop()

    # ========= 2. 加载所有书 =========
    try:
        books = list_books(limit=200)
    except Exception as e:
        st.error(f'加载书单失败: {e}')
        books = []

    # ========= 3. session_state 初始化 =========
    if 'game_book_id' not in st.session_state:
        st.session_state.game_book_id = None
        st.session_state.game_book_name = None
        st.session_state.game_script = None
        st.session_state.game_scene_idx = 0
        st.session_state.game_history = []
        st.session_state.game_ended = False
    if 'selected_book_id' not in st.session_state:
        st.session_state.selected_book_id = None
    if 'selected_script_id' not in st.session_state:
        st.session_state.selected_script_id = None
    if 'show_script_modal' not in st.session_state:
        st.session_state.show_script_modal = False
    if 'player_role_radio' not in st.session_state:
        st.session_state.player_role_radio = None

    # ========= 4. 主页未开始: 平铺书 + 封面 =========
    if st.session_state.game_book_id is None:
        st.markdown('### 📚 挑一本书，进一个故事')
        playable_books = [b for b in books if b.get('has_script') and b.get('id')]
        if not playable_books:
            st.warning('📭 库里还没什么书。先生成几个剧本再来玩。')
            st.stop()

        # 顶部过滤栏
        col_filter, col_history = st.columns([3, 1])
        with col_filter:
            cats = sorted({b.get('category', '其他') or '其他' for b in playable_books})
            sel_cat = st.selectbox('📂 按分类筛选', ['全部'] + cats, key='filter_cat')
            if sel_cat != '全部':
                playable_books = [b for b in playable_books if (b.get('category') or '其他') == sel_cat]
        with col_history:
            st.markdown('##### 🕐 玩过的')
            try:
                history = list_user_history(st.session_state.current_user['id'], limit=5)
                if not history:
                    st.caption('还没开过张')
                else:
                    for h in history:
                        status_icon = {'playing': '▶️', 'completed': '✅', 'paused': '⏸️'}.get(h['status'], '•')
                        st.caption(f"{status_icon} 《{h['book_name']}》 第 {h['current_scene_idx']+1} 关 · {h['total_score']:.0f} 分")
            except Exception as e:
                st.caption(f'加载历史失败: {e}')

        # 封面分类 emoji 映射
        cat_emoji = {
            '文学': '📖', '历史': '🏛️', '哲学': '🤔', '科学': '🔬',
            '心理': '🧠', '写作': '✍️', '学习技巧': '💡', '艺术': '🎨',
            '经济': '💰', '教育': '🎓', '其他': '📘', '综合套装': '📚',
        }

        # 平铺网格: 3 列 (封面 + 书名 + 分类)
        n_cols = 3
        for i in range(0, len(playable_books), n_cols):
            cols = st.columns(n_cols)
            for j, b in enumerate(playable_books[i:i+n_cols]):
                with cols[j]:
                    with st.container(border=True):
                        # 封面 (优先本地文件, 缺失用 emoji 占位)
                        cover_url = b.get('cover_url')
                        cat = b.get('category') or '其他'
                        if cover_url:
                            cover_path = ROOT / 'data' / cover_url
                            if cover_path.is_file():
                                st.image(str(cover_path), use_container_width=True)
                            else:
                                st.markdown(f'<div style="text-align:center;font-size:5em">{cat_emoji.get(cat, "📘")}</div>', unsafe_allow_html=True)
                        else:
                            st.markdown(f'<div style="text-align:center;font-size:5em">{cat_emoji.get(cat, "📘")}</div>', unsafe_allow_html=True)
                        st.markdown(f"**{b.get('name', '?')}**")
                        st.caption(f"分类: {cat}")
                        if b.get('summary'):
                            st.caption(b['summary'][:80] + ('...' if len(b.get('summary', '')) > 80 else ''))
                        if st.button('🎮 进入剧本杀', key=f'select_book_{b["id"]}', use_container_width=True, type='primary'):
                            # 切书/重选时强制重置脚本和角色选择，防止上个书的状态残留
                            st.session_state.selected_book_id = b['id']
                            st.session_state.selected_script_id = None
                            st.session_state.player_role_radio = None
                            st.session_state.show_script_modal = True
                            st.rerun()

        # ========= 5. 弹层: 选剧本 + 选角色 + 开始 =========
        # 修复 (2026-08-23 主人截图反馈): 用 Streamlit 1.31+ 原生 @st.dialog 替换失效的 HTML/CSS modal。
        # 之前 .script-modal-shell div 是空的 (st.markdown 不会嵌套 HTML),
        # 深色背景 (#1f1f2e → #2a2a3d) 显示为黑色横条挡住内容。
        if st.session_state.show_script_modal and st.session_state.selected_book_id:
            _script_selector_modal(st.session_state.selected_book_id)
    else:
        # 显示当前书
        book_id = st.session_state.game_book_id
        player_role = st.session_state.get('player_role', '读者')
        st.markdown(f"### 📖 《{st.session_state.game_book_name}》  ·  🎭 你扮演: **{player_role}**")
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

                # 保存完成记录 (主人 2026-08-22 钦定: 剧本使用记录模块)
                try:
                    save_progress(
                        user_id=st.session_state.current_user['id'],
                        book_id=st.session_state.game_book_id,
                        script_id=st.session_state.game_script_id,
                        player_role=st.session_state.player_role,
                        scene_idx=idx,
                        game_history=st.session_state.game_history,
                        world_state={},
                        total_score=avg,
                        status='completed',
                    )
                except Exception as e:
                    st.warning(f'保存进度失败: {e}')
                st.balloons()
            else:
                scene = scenes[idx]
                # 场景描述
                with st.container(border=True):
                    st.markdown(f"#### 🎬 场景 {idx+1}/{len(scenes)}: {scene.get('title', '?')}")
                    scene_role = scene.get('player_role', '')
                    player_role_now = st.session_state.get('player_role', '')
                    if scene_role:
                        st.caption(f"📖 场景视角: {scene_role}")
                    if player_role_now and player_role_now != scene_role:
                        st.caption(f"🎭 你扮演: **{player_role_now}** (替换场景中的 你)")
                    if scene.get('act'):
                        st.caption(f"幕: {scene['act']}")
                    if scene.get('narrator_intro'):
                        st.info(scene['narrator_intro'])
                    if scene.get('description'):
                        st.markdown(f"> {scene['description']}")
                    # 世界状态条（剧情推进感）
                    ws = scene.get('world_state', {})
                    if ws:
                        cols = st.columns(min(4, len(ws)))
                        for i, (k, v) in enumerate(ws.items()):
                            cols[i % len(cols)].metric(k, str(v))
                    # 🎙️ TTS 旁白按钮 (在线 edge-tts, 不要 key)
                    narrator_text = scene.get('narrator_intro', '').strip()
                    if narrator_text:
                        render_tts_button(
                            narrator_text,
                            key=f'narrator_{idx}',
                            voice_label='🎙️ 听旁白 (晓晓)',
                            voice='zh-CN-XiaoxiaoNeural',
                            rate='-10%',  # 慢一点更有沉浸感
                        )
                    # 描述也提供语音版
                    desc_text = scene.get('description', '').strip().lstrip('>').strip()
                    if desc_text and len(desc_text) < 500:
                        render_tts_button(
                            desc_text,
                            key=f'desc_{idx}',
                            voice_label='🎙️ 听场景描述 (云希 男声)',
                            voice='zh-CN-YunxiNeural',
                            rate='-5%',
                        )

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
                    q_type = q.get('type', '?')
                    type_label = {
                        'choice': '🎭 剧情分支',
                        'comprehension_mc': '📖 理解题',
                        'open_ended': '💬 开放题',
                        'inference_mc': '🔍 推理题',
                    }.get(q_type, q_type)
                    st.markdown(f"##### 问题 {q_idx+1}/{len(questions)} · {type_label}")
                    # choice 题加一句提示
                    if q_type == 'choice':
                        st.caption("💡 你的选择会决定剧情走向")
                    st.markdown(f"**{q.get('question', '')}**")

                    if q.get('type') in ('comprehension_mc', 'choice', 'inference_mc'):
                        # 单选题 / 剧情分支
                        opts = q.get('options', [])
                        if not opts:
                            st.warning('该题没有选项，跳过')
                            st.session_state.scene_q_idx += 1
                            st.rerun()
                        # 兼容两种存储方式: ["A. xxx", "B. xxx"] 或 ["xxx", "yyy"]
                        display_opts = []
                        for i, o in enumerate(opts):
                            o_str = str(o).strip()
                            # 如果 LLM 已加"A. "前缀，去重避免"A. A. xxx"
                            if o_str[:2] in ('A.', 'B.', 'C.', 'D.', 'E.') and o_str[1:3].strip() == '.':
                                display_opts.append(o_str)
                            else:
                                display_opts.append(f"{chr(65+i)}. {o_str}")
                        ans = st.radio('选择', display_opts, key=f'q_{idx}_{q_idx}')
                        # 取首字母
                        choice = ans.split('.')[0].strip()
                        if st.button('提交答案'):
                            if q.get('type') == 'comprehension_mc':
                                correct = q.get('correct', '').upper()
                                is_correct = choice == correct
                                score = 100 if is_correct else 0
                                feedback = q.get('explanation', '')
                                ans_type = 'mc'
                            elif q.get('type') == 'inference_mc':
                                correct = q.get('correct', '').upper()
                                is_correct = choice == correct
                                score = 100 if is_correct else 0
                                feedback = q.get('explanation', '')
                                ans_type = 'mc'
                            else:
                                # choice 剧情分支：不判对错，只推动剧情
                                score = 100  # 参与就有分
                                idx_opt = ord(choice) - ord('A')
                                consequences = q.get('consequences') or q.get('consequence')
                                if isinstance(consequences, list) and idx_opt < len(consequences):
                                    feedback = consequences[idx_opt]
                                elif isinstance(consequences, str) and consequences:
                                    feedback = consequences
                                else:
                                    # 兑底：依选项生成不同的剧情后果
                                    feedback = f"你选择了【{choice}】，剧情继续推进..."
                                ans_type = 'choice'
                                correct = None
                            st.session_state.scene_answers.append({
                                'question': q.get('question'),
                                'type': ans_type,
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

                    # 显示上一题反馈（仅提交后）
                    if st.session_state.scene_answers and q_idx > 0:
                        last = st.session_state.scene_answers[-1]
                        with st.container(border=True):
                            # 反馈语音
                            feedback_text = last.get('feedback', '').strip()
                            if feedback_text and len(feedback_text) < 500:
                                render_tts_button(
                                    feedback_text,
                                    key=f'fb_{idx}_{q_idx}',
                                    voice_label='🎙️ 听反馈',
                                    voice='zh-CN-XiaoxiaoNeural',
                                    rate='+0%',
                                )
                            if last['type'] == 'mc':
                                if last.get('correct') == last.get('answer'):
                                    st.success(f"✅ 正确! {last.get('feedback', '')}")
                                else:
                                    st.error(f"❌ 正确答案是 {last.get('correct')}. {last.get('feedback', '')}")
                            elif last['type'] == 'oe':
                                st.info(f"📊 评分: **{last.get('score', 0)}/100**\n\n{last.get('feedback', '')}")
                            elif last['type'] == 'choice':
                                # 剧情分支：只叙述后果 + 不打分了
                                st.info(f"🎬 剧情分支结果\n\n{last.get('feedback', '')}")
                else:
                    # 当前场景所有题答完，进入下一场景
                    st.session_state.game_history.extend(st.session_state.scene_answers)
                    st.session_state.game_scene_idx += 1
                    st.session_state.scene_q_idx = 0
                    st.session_state.scene_answers = []
                    if st.session_state.game_scene_idx >= len(scenes):
                        st.session_state.game_ended = True
                    # 保存进度 (主人 2026-08-22 钦定: 剧本使用记录模块, 可中断恢复)
                    try:
                        cur_total = sum(h.get('score', 0) for h in st.session_state.game_history)
                        cur_n = max(1, len(st.session_state.game_history))
                        cur_avg = cur_total / cur_n
                        save_progress(
                            user_id=st.session_state.current_user['id'],
                            book_id=st.session_state.game_book_id,
                            script_id=st.session_state.game_script_id,
                            player_role=st.session_state.player_role,
                            scene_idx=st.session_state.game_scene_idx,
                            game_history=st.session_state.game_history,
                            world_state={},
                            total_score=cur_avg,
                            status='completed' if st.session_state.game_ended else 'playing',
                        )
                    except Exception:
                        pass
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
                        st.caption(f"分类: {r.get('category', '?')}")
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
        opts = {b.get('name', '?'): b.get('id') for b in books if b.get('id')}
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

            # 思维导图（如有）—— 优先显示 PNG 图, 源码折叠备用
            png_path = ROOT / 'mindmaps' / f"{book.get('id')}.png"
            mmd_path = ROOT / 'mindmaps' / f"{book.get('id')}.mmd"
            if png_path.exists():
                st.subheader('🗺️ 思维导图')
                st.image(str(png_path), use_container_width=True)
                with st.expander('🔧 Mermaid 源码'):
                    st.code(mmd_path.read_text(encoding='utf-8'), language='mermaid')
            elif mmd_path.exists():
                with st.expander('🗺️ 思维导图 (Mermaid 源码)'):
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