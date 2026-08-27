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
import os
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
    issue_token, resolve_token, revoke_token,  # 2026-08-24 主人反馈加: 登录持久化
)

# 初始化 DB 连接池 (user_manager 直连 DB)
import sys as _sys_dbg
_sys_dbg.stderr.write("[boot] before db imports\n"); _sys_dbg.stderr.flush()
from db import init_pool as _init_db_pool
from config_loader import load_config as _load_cfg
_sys_dbg.stderr.write("[boot] after db imports\n"); _sys_dbg.stderr.flush()
try:
    _cfg = _load_cfg()
    _init_db_pool(_cfg['database'])
    _sys_dbg.stderr.write("[boot] db init OK\n"); _sys_dbg.stderr.flush()
except Exception as _e:
    print(f'⚠️ DB pool init 失败: {_e}')

# 启动后台 pipeline worker —— 已移到文件末尾 (所有函数定义之后)，
# 否则 _worker_loop 在此处还未定义 → NameError (2026-08-25 修复)


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
        # 2026-08-24 主人反馈: 宿主只有 python3 (没 python) → Errno 2 [修复]
        server_params = StdioServerParameters(
            command=sys.executable,  # 用当前 Python 解释器 (兼容 docker / host)
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


@st.cache_data(ttl=300, show_spinner=False)
def get_all_chunks(book_id: int) -> list:
    """直接走 db 拿全部 chunks（不走 MCP, 避免 limit=100 上限）

    缓存 5 分钟, 阅读翻页不重复打 DB
    """
    import db
    with db.get_cursor() as cur:
        cur.execute(
            "SELECT id, chapter_index, chunk_text, char_count "
            "FROM chunks WHERE book_id=%s ORDER BY chapter_index, id",
            (book_id,)
        )
        return [dict(r) for r in cur.fetchall()]


# ============== 原书 epub 解析 (铲屎官 2026-08-26 钦定: 阅读器读原书, 不读 chunks) ==============

# 容器内可见路径映射: host path (/home/fuermos/books/...) → container (/mnt/books/...)
# book.path 在 books 表里是 host 路径, docker 容器内只能从 /mnt/books 或 /app/books 读
_PATH_MAP_HOST_TO_CONTAINER = (
    ('/home/fuermos/books/', '/mnt/books/'),
    ('/home/fuermos/jiujiu-bookstack/books/', '/app/books/'),
)


def _resolve_book_path(book_path: str) -> Optional[str]:
    """把 books.path (host 路径) 映射到容器内可读的路径

    例如 /home/fuermos/books/文学/...epub → /mnt/books/文学/...epub
    book.path 存的是 host 路径, web 容器内需要映射后 才能读
    """
    if not book_path:
        return None
    for src, dst in _PATH_MAP_HOST_TO_CONTAINER:
        if book_path.startswith(src):
            return dst + book_path[len(src):]
    return book_path  # 容器内路径 (如 /app/books/...)


def _parse_epub_chapters(epub_path: str) -> list[dict]:
    """用 ebooklib 解析 epub, 返回 [{'index': i, 'title': str, 'text': str, 'char_count': int}]

    拿不到 TOC 就退而求其次, 用 spine 顺序生成 [Chapter i] 标题。

    锋屎官 2026-08-26 加强:
    - 跳过 < 50 字的版权页 / 标题页
    - 跳过只含“目录” 的目录页 (第一项单独处理)
    - 标题优先从 epub.TOC 取, 其次 h1/h2/h3/strong/b/center, 最后第一行短文本
    """
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup
    import warnings
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)

    book = epub.read_epub(epub_path)
    items = [it for it in book.get_items() if it.get_type() == ebooklib.ITEM_DOCUMENT]
    # 按 spine 顺序排
    spine_ids = [s[0] for s in book.spine]
    items_by_id = {it.get_id(): it for it in items}
    ordered = [items_by_id[sid] for sid in spine_ids if sid in items_by_id]
    # 未在 spine 中的 额外补上 (少见)
    seen = {it.get_id() for it in ordered}
    for it in items:
        if it.get_id() not in seen:
            ordered.append(it)

    # 从 epub.TOC 拿标题映射 (href → title)
    toc_title_map: dict[str, str] = {}

    def _walk_toc(toc):
        for t in toc:
            if isinstance(t, tuple):
                for sub in t:
                    _walk_toc([sub])
            else:
                href = getattr(t, 'href', '') or ''
                title = (getattr(t, 'title', '') or '').strip()
                # href 可能含 #fragment, 只取文件路径部分
                fname = href.split('#')[0] if href else ''
                if fname and title:
                    toc_title_map[fname] = toc_title_map.get(fname, title)

    try:
        _walk_toc(book.toc)
    except Exception:
        pass

    def _is_toc_page(text: str) -> bool:
        """目录页检测: 开头是“目录” 且多为章节名列表 (多行 < 30字)"""
        lines = [l for l in text.splitlines() if l.strip()][:15]
        if not lines:
            return False
        if lines[0].strip() not in ('目录', '目 录', '目錄', 'Contents', '目  录'):
            return False
        # 短行多 → 像是目录
        short = sum(1 for l in lines if len(l) < 30)
        return short >= 5

    chapters: list[dict] = []
    for idx, item in enumerate(ordered):
        try:
            raw = item.get_content().decode('utf-8', errors='replace')
        except Exception:
            continue
        soup = BeautifulSoup(raw, 'lxml')
        text = soup.get_text(separator='\n', strip=True)
        text = '\n'.join(line.strip() for line in text.splitlines() if line.strip())
        if len(text) < 200:  # 过滤版权页 / 标题页
            continue
        if _is_toc_page(text):
            continue
        # 标题: 优先 TOC → h1/h2/h3 → strong/b/center → 前 3 行内第一行短文本
        title = ''
        item_name = item.get_name() or ''
        if item_name in toc_title_map:
            title = toc_title_map[item_name]
        if not title:
            for tag in ('h1', 'h2', 'h3'):
                h = soup.find(tag)
                if h and h.get_text(strip=True):
                    title = h.get_text(strip=True)[:80]
                    break
        if not title:
            for tag in ('strong', 'b'):
                h = soup.find(tag)
                if h and 1 < len(h.get_text(strip=True)) < 60:
                    title = h.get_text(strip=True)[:80]
                    break
        if not title:
            # 取正文前 5 行内的第一个短文本 (e.g. 前言, Chapter 1)
            for line in text.splitlines()[:5]:
                line = line.strip()
                # 过滤纯数字 / 纯标点 / 太长
                if 1 < len(line) < 40 and not line.isdigit():
                    title = line
                    break
        if not title:
            # 最后抱佛脚: 抽前 30 个汉字作为默认标题
            cn = ''.join(c for c in text[:80] if '\u4e00' <= c <= '\u9fff')
            title = cn[:20] + ('...' if len(cn) > 20 else '') if cn else (item.get_name() or f'Chapter {idx+1}')
        if not title:
            title = item.get_name() or f'Chapter {idx+1}'
        chapters.append({
            'index': idx,
            'title': title,
            'text': text,
            'char_count': len(text),
        })
    return chapters


@st.cache_data(ttl=3600, show_spinner=False)
def load_book_epub(book_id: int) -> Optional[dict]:
    """从 DB 拿 book.path, 在容器内找 epub 文件, 解析章节

    返回 {'book_id', 'name', 'path', 'chapters': [...], 'source': 'epub'|'chunks'}
    找不到 epub 时返回 None, 调用者 fallback 到 PG chunks
    """
    import db
    with db.get_cursor() as cur:
        cur.execute("SELECT id, name, path FROM books WHERE id=%s", (book_id,))
        row = cur.fetchone()
    if not row:
        return None
    host_path = row['path']
    container_path = _resolve_book_path(host_path)
    if not container_path or not os.path.exists(container_path):
        return None
    try:
        chapters = _parse_epub_chapters(container_path)
    except Exception as e:
        print(f'epub 解析失败 {container_path}: {e}')
        return None
    if not chapters:
        return None
    return {
        'book_id': book_id,
        'name': row['name'],
        'path': container_path,
        'chapters': chapters,
        'source': 'epub',
    }


@st.cache_data(ttl=600, show_spinner=False)
def get_book_chapters(book_id: int) -> dict[int, int]:
    """返回 {chapter_index: n_chunks}，方便选章节时显示页数

    章节标题用 chapter_index 代替（chunks 表无 chapter_title 字段）
    """
    import db
    with db.get_cursor() as cur:
        cur.execute(
            "SELECT chapter_index, COUNT(*) AS n FROM chunks "
            "WHERE book_id=%s GROUP BY chapter_index ORDER BY chapter_index",
            (book_id,)
        )
        return {r['chapter_index']: r['n'] for r in cur.fetchall()}


# ============== 任务队列 (铲屎官 2026-08-25 钓定) ==============

def enqueue_pipeline_job(file_path: str, book_name: str, book_id: Optional[int] = None) -> int:
    """创建任务，写 PG + 创建日志文件路径。返回 job_id。"""
    import db
    # 预先生成 log_path（需要 id）→ 先插入拿 id 再 UPDATE log_path
    with db.get_cursor() as cur:
        cur.execute(
            "INSERT INTO pipeline_jobs (book_id, book_name, file_path, status, current_step, log_path) "
            "VALUES (%s, %s, %s, 'queued', 'queued', '') RETURNING id",
            (book_id, book_name, str(file_path)),
        )
        job_id = cur.fetchone()['id']
        log_path = f'logs/jobs/job_{job_id}.log'
        cur.execute(
            "UPDATE pipeline_jobs SET log_path=%s WHERE id=%s",
            (log_path, job_id),
        )
    # 创建日志目录
    (ROOT / 'logs' / 'jobs').mkdir(parents=True, exist_ok=True)
    return job_id


def list_jobs(limit: int = 30) -> list:
    """列所有 jobs，按 id 倒序"""
    import db
    with db.get_cursor() as cur:
        cur.execute(
            "SELECT id, book_id, book_name, file_path, status, current_step, "
            "step_progress, step_total, log_path, error, created_at, started_at, finished_at "
            "FROM pipeline_jobs ORDER BY id DESC LIMIT %s",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_job_log(log_path: str, tail_bytes: int = 16384) -> str:
    """读日志 tail。tail_bytes 默认 16KB, 可传 None 读全部"""
    if not log_path:
        return ''
    full = ROOT / log_path
    if not full.exists():
        return ''
    try:
        size = full.stat().st_size
        with open(full, 'rb') as f:
            if tail_bytes and size > tail_bytes:
                f.seek(-tail_bytes, 2)
            data = f.read()
        return data.decode('utf-8', errors='replace')
    except Exception as e:
        return f'[读日志失败: {e}]'


def cancel_job(job_id: int) -> None:
    """把 job 标记为 cancelled（worker 会跳过）"""
    import db
    with db.get_cursor() as cur:
        cur.execute(
            "UPDATE pipeline_jobs SET status='cancelled', finished_at=now() "
            "WHERE id=%s AND status IN ('queued', 'running')",
            (job_id,),
        )


def retry_job(job_id: int) -> bool:
    """重试失败/取消的任务：重置为 queued，清 error 和进度（铲屎官 2026-08-25）"""
    import db
    with db.get_cursor() as cur:
        cur.execute(
            "UPDATE pipeline_jobs SET status='queued', current_step='queued', "
            "step_progress=0, step_total=0, error=NULL, finished_at=NULL, started_at=NULL "
            "WHERE id=%s AND status IN ('failed', 'cancelled') RETURNING id",
            (job_id,),
        )
        if not cur.fetchall():
            return False
    # 日志文件截断重开，避免新旧日志混淆
    with db.get_cursor() as cur:
        cur.execute("SELECT log_path FROM pipeline_jobs WHERE id=%s", (job_id,))
        r = cur.fetchone()
    if r and r.get('log_path'):
        try:
            log_full = ROOT / r['log_path']
            log_full.parent.mkdir(parents=True, exist_ok=True)
            log_full.write_text('\n===== 🔁 重试 =====\n', encoding='utf-8')
        except Exception:
            pass
    return True


def clear_completed_jobs() -> int:
    """删除 completed/failed/cancelled 的 job (清理)"""
    import db
    with db.get_cursor() as cur:
        cur.execute(
            "DELETE FROM pipeline_jobs WHERE status IN ('completed', 'failed', 'cancelled') RETURNING id",
        )
        return len(cur.fetchall() or [])


def get_reading_progress(book_id: int, user_key: str = 'default') -> Optional[dict]:
    """拿阅读进度（书+用户维度）。表不存在时自动建。"""
    import db
    try:
        with db.get_cursor() as cur:
            cur.execute(
                "SELECT chapter_index, page_idx, scroll_pct, font_size, updated_at FROM reading_progress "
                "WHERE book_id=%s AND user_key=%s",
                (book_id, user_key),
            )
            r = cur.fetchone()
            return dict(r) if r else None
    except Exception:
        with db.get_cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS reading_progress (
                book_id integer NOT NULL,
                user_key text NOT NULL DEFAULT 'default',
                chapter_index integer NOT NULL DEFAULT 0,
                page_idx integer NOT NULL DEFAULT 0,
                scroll_pct real NOT NULL DEFAULT 0,
                font_size integer NOT NULL DEFAULT 18,
                updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (book_id, user_key)
            )""")
        return None


def save_reading_progress(book_id: int, chapter_index: int, page_idx: int,
                          scroll_pct: float = 0, font_size: int = 18,
                          user_key: str = 'default') -> None:
    """存阅读进度（UPSERT，每次翻页调用）

    page_idx 含义 (铲屎官 2026-08-26 重构): 本章内字符偏移 (char_offset)。
    这样 page_size 改了之后还能精确定位到字。
    """
    import db
    try:
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO reading_progress (book_id, user_key, chapter_index, page_idx, scroll_pct, font_size)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (book_id, user_key) DO UPDATE SET
                     chapter_index=EXCLUDED.chapter_index, page_idx=EXCLUDED.page_idx,
                     scroll_pct=EXCLUDED.scroll_pct, font_size=EXCLUDED.font_size,
                     updated_at=now()""",
                (book_id, user_key, chapter_index, page_idx, scroll_pct, font_size),
            )
    except Exception as e:
        print(f'进度保存失败: {e}')


def parse_pipeline_progress(log_text: str) -> tuple[str, int, int]:
    """从日志文本解析 (current_step, progress_done, progress_total)

    返回默认 ('running', 0, 0) 如果解析不到
    """
    import re
    # Step 标记 (pipeline.py 输出)
    step_match = re.search(r'Step\s*(\d+(?:\.\d+)?)\s*:?\s*([^\n]+)', log_text)
    if step_match:
        step_label = f'Step {step_match.group(1)}: {step_match.group(2)[:30]}'
    else:
        step_label = 'running'
    # 进度标记: "进度 X/Y" 或 "✅ 进度 X/Y"
    prog_match = re.search(r'(?:进度\s*|progress\s*)(\d+)\s*/\s*(\d+)', log_text)
    if prog_match:
        return step_label, int(prog_match.group(1)), int(prog_match.group(2))
    # 默认
    return step_label, 0, 0


# ============== 后台 Worker Daemon (铲屎官 2026-08-25 钓定) ==============
# 一个常驻守护线程，轮询 PG 队列，启动 subprocess 跑 pipeline
# 用 @st.cache_resource 保证只启一次（跨 rerun 保留）

import subprocess as _sp
import threading as _th
import time as _time


def _update_job_progress(job_id: int, step: str, prog: int, total: int) -> None:
    import db
    try:
        with db.get_cursor() as cur:
            cur.execute(
                "UPDATE pipeline_jobs SET current_step=%s, step_progress=%s, step_total=%s "
                "WHERE id=%s",
                (step, prog, total, job_id),
            )
    except Exception as e:
        print(f'⚠️ update_job_progress failed: {e}')


def _run_one_job(job: dict) -> None:
    """同步跑一个 job (被 worker_loop 在子线程里调用)"""
    import db
    job_id = job['id']
    log_path = ROOT / job['log_path']
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 标记 running
    with db.get_cursor() as cur:
        cur.execute(
            "UPDATE pipeline_jobs SET status='running', started_at=now() WHERE id=%s",
            (job_id,),
        )

    # 2. 启动 subprocess
    with open(log_path, 'wb', 0) as logf:
        proc = _sp.Popen(
            ['python3', '/app/scripts/pipeline.py', job['file_path'], '--force'],
            stdout=logf, stderr=_sp.STDOUT,
            cwd='/app',
        )

    # 3. 周期性 tail 日志，更新 progress
    last_size = 0
    last_update = 0
    while proc.poll() is None:
        _time.sleep(2)
        try:
            cur_size = log_path.stat().st_size
            if cur_size > last_size:
                with open(log_path, 'rb') as f:
                    f.seek(last_size)
                    new_text = f.read().decode('utf-8', errors='replace')
                last_size = cur_size
                step, prog, total = parse_pipeline_progress(new_text)
                # 每 5 秒限流一次避免 PG 压力
                now = _time.time()
                if now - last_update > 5:
                    _update_job_progress(job_id, step, prog, total)
                    last_update = now
        except Exception:
            pass

    # 4. 进程退出，写 status
    rc = proc.returncode
    status = 'completed' if rc == 0 else 'failed'
    error_msg = None
    if rc != 0:
        try:
            tail = log_path.read_text(encoding='utf-8', errors='replace')[-2000:]
            error_msg = tail[-500:] if tail else f'exit code {rc}'
        except Exception:
            error_msg = f'exit code {rc}'

    # 最后一次进度解析（拿全部日志）
    try:
        full_text = log_path.read_text(encoding='utf-8', errors='replace')
        step, prog, total = parse_pipeline_progress(full_text)
    except Exception:
        step, prog, total = ('done', 0, 0)

    with db.get_cursor() as cur:
        cur.execute(
            "UPDATE pipeline_jobs SET status=%s, finished_at=now(), error=%s, "
            "current_step=%s, step_progress=%s, step_total=%s WHERE id=%s",
            (status, error_msg, step, prog, total, job_id),
        )


def _worker_loop() -> None:
    """守护线程：轮询 PG，找 queued 任务 → 跑"""
    print('🚀 pipeline worker started')
    while True:
        try:
            import db
            # 检查是否有 running 超过 30 分钟 (stale 检测)
            with db.get_cursor() as cur:
                cur.execute(
                    "SELECT id FROM pipeline_jobs WHERE status='running' "
                    "AND started_at < now() - interval '30 minutes'"
                )
                stale = cur.fetchall()
                for s in stale:
                    print(f'⚠️ stale job #{s["id"]} 超过 30 分钟, 重置为 failed')
                    cur.execute(
                        "UPDATE pipeline_jobs SET status='failed', error='timeout 30min', "
                        "finished_at=now() WHERE id=%s",
                        (s['id'],),
                    )

            # 拿一个 queued (FIFO)
            with db.get_cursor() as cur:
                cur.execute(
                    "SELECT id, book_id, book_name, file_path, log_path FROM pipeline_jobs "
                    "WHERE status='queued' ORDER BY id LIMIT 1"
                )
                job = cur.fetchone()
            if not job:
                _time.sleep(2)
                continue

            # 同步跑 (单线程 worker)
            print(f'🚀 worker: 开始 job #{job["id"]} - {job["book_name"]}')
            _run_one_job(dict(job))
            print(f'✅ worker: 完成 job #{job["id"]}')

        except Exception as e:
            print(f'❌ worker_loop error: {e}')
            import traceback; traceback.print_exc()
            _time.sleep(5)


@st.cache_resource
def start_pipeline_worker() -> _th.Thread:
    """兼容旧调用。实际启动在 module-level 完成, 这个函数仅占位。"""
    return None


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

    # 2026-08-24 主人反馈: 硬编码 /app/ 路径 + python 指令 在宿主都会报错 [修复]
    from pathlib import Path as P
    mcp_script = P(__file__).parent.parent / 'scripts' / 'mcp_server.py'
    mcp_params = StdioServerParameters(
        command=sys.executable,
        args=[str(mcp_script)],
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


def render_tts_button(text: str, key: str, voice_label: str = '🎙️ 听旁白',
                      voice: Optional[str] = None, rate: str = '+0%'):
    """渲染 TTS 按钮 + 音频播放 (用 cache 避免重复生成)

    voice: 不传则从 session_state['global_voice'] 读, 默认 Xiaoxiao
    """
    if not text or not text.strip():
        return
    if voice is None:
        voice = st.session_state.get('global_voice', 'zh-CN-XiaoxiaoNeural')
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

# 全局 session_state 默认值 (铲屎官 2026-08-26)
if 'global_voice' not in st.session_state:
    st.session_state['global_voice'] = 'zh-CN-XiaoxiaoNeural'
if 'reader_page_size' not in st.session_state:
    st.session_state['reader_page_size'] = 600

# 侧边栏导航
with st.sidebar:
    st.markdown('# 📚 玖玖书塔')
    st.caption('jiujiu-bookstack · v0.2.0')
    st.divider()
    # 处理强制跳转 (主页"玩剧本"按钮触发)
    pages = ['🏠 首页', '🎮 剧本杀', '🔍 搜索', '📖 书详情', '📖 阅读', '🚀 任务队列']
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

    # 📤 上传书本 (铲屎官 2026-08-25 钓定)
    with st.expander('📤 上传书本', expanded=False):
        st.caption('上传 epub / mobi 到 books/_uploads/')
        uploaded = st.file_uploader(
            '选 epub/mobi',
            type=['epub', 'mobi'],
            accept_multiple_files=False,
            key='upload_epub',
        )
        if uploaded is not None:
            # 保存到 books/_uploads/
            uploads_dir = ROOT / 'books' / '_uploads'
            uploads_dir.mkdir(parents=True, exist_ok=True)
            target = uploads_dir / uploaded.name
            target.write_bytes(uploaded.read())
            st.success(f'✅ 已保存: {uploaded.name} ({uploaded.size // 1024} KB)')
            # 上传完直接跑全流程 (铲屎官 2026-08-25 钓定)
            if st.button('🚀 上传并跑全流程 (8 步)', key='btn_pipeline', type='primary'):
                try:
                    # 1. 先 import (快速拿到 book_id)
                    import import_book
                    book_id = import_book.import_epub(target)
                    if not book_id:
                        st.error('入库失败，无法启动 pipeline')
                        st.stop()
                    # 2. 写 pipeline_jobs 队列
                    job_id = enqueue_pipeline_job(str(target), uploaded.name, book_id=book_id)
                    st.success(f'📚 入库 (book_id={book_id}) + 任务已入队 (job_id={job_id})')
                    st.info('→ 跳转到 🚀 任务队列 查看进度和实时日志')
                    get_all_chunks.clear()
                    st.session_state['reader_book_id'] = book_id
                    st.session_state['force_page'] = '🚀 任务队列'
                    st.session_state.pop('upload_epub', None)
                    st.rerun()
                except Exception as e:
                    st.error(f'出错: {e}')
            st.caption(f'文件路径: `{target.relative_to(ROOT)}`')

    st.divider()

    # 用户登录状态
    if 'current_user' not in st.session_state:
        st.session_state.current_user = None
    # 2026-08-24 加: 启动时尝试从 query_params 里的 token 还原登录态 (防止刷新掉登)
    if st.session_state.current_user is None:
        url_token = st.query_params.get('token', '')
        if url_token:
            user_obj = resolve_token(url_token)
            if user_obj:
                st.session_state.current_user = user_obj
    user = st.session_state.get('current_user')
    if user:
        st.markdown(f"### 👤 {user['nickname']}")
        st.caption(f"📧 {user['email']}")
        if st.button('🚪 退出登录', use_container_width=True):
            # 2026-08-24 加: 退出时清除 token + query_params,防下个访客自动登入
            old_token = st.query_params.get('token', '')
            if old_token:
                revoke_token(old_token)
            st.query_params.clear()
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
                        # 2026-08-24 主人反馈: 刷页面需要重登 -> token + query_params 持久化
                        token = issue_token(res['id'])
                        st.query_params['token'] = token
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
                        # 2026-08-24 主人反馈: 注册成功同样发 token,避免跳出后丢登录
                        token = issue_token(res['id'])
                        st.query_params['token'] = token
                        st.session_state.current_user = res
                        st.success(f'✅ 注册成功, 欢迎 {res["nickname"]}!')
                        st.rerun()


# ============== Modal: 选剧本 & 选角色 (Streamlit 1.31+ @st.dialog) ==============

@st.dialog('🎭 三步：选剧本 → 选角色 → 进故事', width='large')
def _script_selector_modal(book_id: int):
    """分步流（v3 2026-08-24 主人反馈）：

    ① 选剧本 → ② 选角色 → ③ 确认开玩
    每步未完成时，后面的内容不出现。

    历史教训:
    - 旧版 SUSPICIOUS_CHARS 硬编码误判（福尔摩斯本尊的书也被标"跨书串场"）
    - 旧版剧本+角色一锅端，主人反馈应严格三段式
    - 旧版直接显示英文 role_id（reader_xiao 等）→ 改成优先中文 role_name
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

    # ====== 步骤 ① 选剧本 ======
    # 2026-08-24 主人反馈: 如果一本书只有 1 个剧本, 跳过这一步直接选角色
    if len(scripts_list) == 1:
        only_script = scripts_list[0]
        # 自动选 (未选过时设上)
        if not st.session_state.selected_script_id:
            st.session_state.selected_script_id = only_script['id']
            st.session_state.player_role_radio = None
            st.rerun()
        st.caption(f"🎬 这本书只有 1 个剧本: 《第 {only_script['chapter_index']+1} 册 · {only_script['n_scenes']} 场景》- 直接选角色")
    else:
        st.markdown('#### ① 选个剧本')
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
                    # 切剧本时重置角色选择（避免跨剧本串场）
                    st.session_state.player_role_radio = None
                    st.rerun()

    # 未选剧本 → 不显示步骤 ②③
    if not st.session_state.selected_script_id:
        st.markdown('---')
        if st.button('✖ 退出去', key='dlg_close_step1', use_container_width=True):
            st.session_state.show_script_modal = False
            st.session_state.selected_book_id = None
            st.session_state.selected_script_id = None
            st.session_state.player_role_radio = None
            st.rerun()
        return

    # ====== 步骤 ② 选角色（仅当选完剧本才显示）======
    target_script = next((s for s in scripts_list if s['id'] == st.session_state.selected_script_id), scripts_list[0])
    script_json = target_script['script_json']

    # 构造 _role_id → 中文 _role_name 映射（v3.0 字段）
    role_name_map: dict[str, str] = {}
    for c in script_json.get('characters', {}).get('available_roles', []):
        rid = c.get('role_id') or ''
        rname = c.get('role_name') or ''
        if rid and rname:
            role_name_map[rid] = rname

    # 收集 scenes 实际出现过的角色（scene.player_role + question.role_perspective）
    scenes_roles: set = set()
    for sc in script_json.get('scenes', []):
        pr = (sc.get('player_role') or '').strip()
        if pr:
            scenes_roles.add(pr)
        for q in sc.get('questions', []):
            rp = (q.get('role_perspective') or '').strip()
            if rp:
                scenes_roles.add(rp)

    # 从 _player_role_options 出发，最终展示 (label, value) 列表
    raw_options = script_json.get('_player_role_options', [])
    valid_labels: list[tuple[str, str]] = []  # (display_name, role_id)
    for rid in raw_options:
        rid = (rid or '').strip()
        if not rid:
            continue
        # scenes 没出现 → 跳过（v2 scenes-based 校验）
        if rid not in scenes_roles:
            continue
        # 中文名优先，缺失回退到 role_id
        display = role_name_map.get(rid) or rid
        valid_labels.append((display, rid))

    # 兜底：scenes 里有但 options 缺 → 用 scenes 里的 role_id 直接显示
    if not valid_labels and scenes_roles:
        for rid in sorted(scenes_roles):
            display = role_name_map.get(rid) or rid
            valid_labels.append((display, rid))

    # 终极兜底
    if not valid_labels:
        valid_labels = [('主角', '主角'), ('书童', '书童'), ('旁白', '旁白')]

    # 警告条：检查哪些 role_id 缺失中文名（友好提示）
    missing_cn = [(rid, role_name_map.get(rid, '?')) for rid in scenes_roles if rid not in role_name_map and not any(c for c in script_json.get('characters', {}).get('available_roles', []) if c.get('role_id') == rid)]
    if missing_cn:
        st.warning(f'⚠️ 以下角色缺中文名（fallback 到英文 ID）：{[m[0] for m in missing_cn]}')

    st.markdown('#### ② 在这个故事里，你是谁？')
    # 切换剧本时 radio 已被重置；如果当前 radio 不在新选项里也重置
    current_ids = [v for _, v in valid_labels]
    if st.session_state.player_role_radio not in current_ids:
        st.session_state.player_role_radio = current_ids[0]
    chosen_role_id = st.session_state.player_role_radio
    cols = st.columns(min(4, len(valid_labels)))
    for k, (display, rid) in enumerate(valid_labels):
        with cols[k % len(cols)]:
            btn_type = 'primary' if rid == chosen_role_id else 'secondary'
            if st.button(display, key=f'dlg_role_{rid}', use_container_width=True, type=btn_type):
                st.session_state.player_role_radio = rid
                st.rerun()

    # ====== 步骤 ③ 确认开玩（仅当选完角色才显示）======
    p = progress_map.get(target_script['id'])
    resume_text = ''
    if p and p['status'] == 'playing':
        resume_text = f' (从 Lv.{p["current_scene_idx"]+1} 接着玩，之前得分 {p["total_score"]:.0f})'
    elif p and p['status'] == 'completed':
        resume_text = ' (上次通关了，这次从头来)'

    st.markdown('#### ③ 确认开玩')
    # 找中文名用于提示
    chosen_display = next((d for d, v in valid_labels if v == chosen_role_id), chosen_role_id)
    st.info(f'你将以 **《{chosen_display}》** 身份进入《{sel_book["name"]}》{resume_text}')

    st.markdown('---')
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button('✖ 退出去', key='dlg_close_final', use_container_width=True):
            st.session_state.show_script_modal = False
            st.session_state.selected_book_id = None
            st.session_state.selected_script_id = None
            st.session_state.player_role_radio = None
            st.rerun()
    with col2:
        btn_label = f'🚀 推开这扇门{resume_text}'
        if st.button(btn_label, key='dlg_start_play', type='primary', use_container_width=True):
            st.session_state.game_book_id = book_id
            st.session_state.game_book_name = sel_book['name']
            st.session_state.game_script_id = target_script['id']
            st.session_state.player_role = chosen_role_id  # 存 role_id (逻辑层用)
            st.session_state.player_role_display = chosen_display  # 中文名 (UI 显示用)
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
                    # fix 2026-08-25: MCP 返回字段是 n 不是 count (旧代码永远显示 0)
                    st.metric(cat.get('category', '?'), cat.get('n', cat.get('count', 0)))
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
                                    # 修复 (2026-08-24 主人反馈): 首页「玩剧本」以前只跳页不弹 modal,
                                    # 导致直接进入游戏。现在统一走三步 modal: 选剧本 → 选角色 → 进游戏。
                                    st.session_state['selected_book_id'] = b.get('id')
                                    st.session_state['selected_book_name'] = b.get('name')
                                    # 切书/重选时强制重置剧本和角色,防上个书状态残留
                                    st.session_state['selected_script_id'] = None
                                    st.session_state['player_role_radio'] = None
                                    st.session_state['show_script_modal'] = True
                                    # 跳到剧本杀页 (剧本杀页负责弹 modal)
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
                                # 铲屎官 2026-08-25 反馈: use_container_width 让竖版封面太高, 限定 height
                                st.image(str(cover_path), width=160)
                            else:
                                st.markdown(f'<div style="text-align:center;font-size:4em;line-height:1.2;padding:20px 0">{cat_emoji.get(cat, "📘")}</div>', unsafe_allow_html=True)
                        else:
                            st.markdown(f'<div style="text-align:center;font-size:4em;line-height:1.2;padding:20px 0">{cat_emoji.get(cat, "📘")}</div>', unsafe_allow_html=True)
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
                    # 🎙️ TTS 旁白按钮 (在线 edge-tts, 不要 key; 走全局音色, 铲屎官 2026-08-26)
                    narrator_text = scene.get('narrator_intro', '').strip()
                    if narrator_text:
                        render_tts_button(
                            narrator_text,
                            key=f'narrator_{idx}',
                            voice_label='🎙️ 听旁白',
                            rate='-10%',  # 慢一点更有沉浸感
                        )
                    # 描述也提供语音版
                    desc_text = scene.get('description', '').strip().lstrip('>').strip()
                    if desc_text and len(desc_text) < 500:
                        render_tts_button(
                            desc_text,
                            key=f'desc_{idx}',
                            voice_label='🎙️ 听场景描述',
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
                            # 反馈语音 (走全局音色, 铲屎官 2026-08-26)
                            feedback_text = last.get('feedback', '').strip()
                            if feedback_text and len(feedback_text) < 500:
                                render_tts_button(
                                    feedback_text,
                                    key=f'fb_{idx}_{q_idx}',
                                    voice_label='🎙️ 听反馈',
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

            # 📖 在线阅读入口 (铲屎官 2026-08-25 钓定)
            st.markdown('---')
            col_read, col_play = st.columns([1, 1])
            with col_read:
                if st.button('📖 在线阅读这本书', key=f'reader_entry_{book_id}', type='primary', use_container_width=True):
                    st.session_state['reader_book_id'] = book_id
                    st.session_state['force_page'] = '📖 阅读'
                    st.rerun()
            with col_play:
                if book.get('has_script'):
                    if st.button('🎮 直接玩剧本', key=f'play_entry_{book_id}', use_container_width=True):
                        st.session_state['selected_book_id'] = book_id
                        st.session_state['force_page'] = '🎮 剧本杀'
                        st.rerun()

            st.divider()
            st.subheader('📝 叙事摘要')
            st.markdown(book.get('summary', '（无摘要）'))

            # 思维导图（如有）—— 优先显示 PNG 图, 源码折叠备用
            png_path = ROOT / 'mindmaps' / f"{book.get('id')}.png"
            mmd_path = ROOT / 'mindmaps' / f"{book.get('id')}.mmd"
            if png_path.exists():
                st.subheader('🗺️ 思维导图')
                # 铲屎官 2026-08-25 反馈: mindmap PNG 在详情页太大, 限宽 600
                st.image(str(png_path), width=600)
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


# ====== 📖 在线阅读 (Koodo 风格阅读器, 铲屎官 2026-08-26 翻页重写) ======
elif page == '📖 阅读':
    st.title('📖 在线阅读')

    # ---------- 1. 选书 ----------
    books = list_books(limit=200)
    book_opts = {f"{b.get('name', '?')} (#{b.get('id')})": b.get('id') for b in books if b.get('id')}
    if not book_opts:
        st.warning('库里还没书。请先生成 pipeline。')
        st.stop()

    default_idx = 0
    pre_book_id = st.session_state.get('reader_book_id')
    if pre_book_id:
        for i, bid in enumerate(book_opts.values()):
            if bid == pre_book_id:
                default_idx = i
                break

    sel_name = st.selectbox('选书', list(book_opts.keys()), index=default_idx, key='reader_book_select')
    book_id = book_opts[sel_name]
    st.session_state['reader_book_id'] = book_id

    book = get_book(book_id)

    # ---------- 2. 加载原书 epub (铲屎官 2026-08-26 钩定: 读原书不读 chunks) ----------
    saved = get_reading_progress(book_id)
    epub_data = load_book_epub(book_id)
    source_label = ''
    if epub_data:
        # epub 解析成功
        chapter_texts = {ch['index']: ch['text'] for ch in epub_data['chapters']}
        chapter_titles = {ch['index']: ch['title'] for ch in epub_data['chapters']}
        chapters = sorted(chapter_texts.keys())
        source_label = '📖 原书 epub'
    else:
        # fallback: PG chunks (旧方案, 用于个别补不全原书的情况)
        st.info('💡 未找到原书 epub, 使用 PG chunks fallback')
        all_chunks = get_all_chunks(book_id)
        if not all_chunks:
            st.warning('⚠️ 这本书还没有 chunks, 原书也读不到。请先跑 pipeline import。')
            st.stop()
        chapter_titles = {}
        chapter_texts = {}
        chapters = sorted({c['chapter_index'] for c in all_chunks})
        for ch in chapters:
            ch_chunks = [c for c in all_chunks if c['chapter_index'] == ch]
            text = '\n\n'.join(c['chunk_text'].strip() for c in ch_chunks if c.get('chunk_text'))
            chapter_texts[ch] = text
        source_label = '💾 PG chunks (fallback)'

    # 全书总字符数 + 章节起点偏移
    chapter_chars_before: dict[int, int] = {chapters[0]: 0}
    chapter_char_lens = {ch: len(chapter_texts[ch]) for ch in chapters}
    total_chars = sum(chapter_char_lens.values())
    for i in range(1, len(chapters)):
        chapter_chars_before[chapters[i]] = chapter_chars_before[chapters[i-1]] + chapter_char_lens[chapters[i-1]]

    # 恢复进度 (新版: saved.page_idx 存的是本章内 char_offset)
    # 反算 page_idx: 找 page_breaks 中 <= char_offset 的最后一个位置
    init_ch = chapters[0]
    init_char = 0  # 本章内字符偏移 (后续算 page_idx 用)
    if saved:
        sc, sp = saved.get('chapter_index', 0), saved.get('page_idx', 0)
        if sc in chapter_texts:
            init_ch = sc
            init_char = max(0, int(sp))

    # ---------- 3. 章节选择 ----------
    # 章节下拉标签: epub 源用真标题, chunks 源用 "第 N 章"
    def _chapter_label(idx):
        ch_len = chapter_char_lens.get(idx, 0)
        if chapter_titles.get(idx):
            return f"{chapter_titles[idx]} · {ch_len:,} 字"
        return f'第 {idx+1} 章 · {ch_len:,} 字'
    chapter_labels = {idx: _chapter_label(idx) for idx in chapters}
    sel_chapter = st.selectbox(
        '章节',
        list(chapter_labels.keys()),
        index=chapters.index(init_ch),
        format_func=lambda x: chapter_labels[x],
        key=f'reader_ch_{book_id}',
    )
    chapter_text = chapter_texts[sel_chapter]
    chapter_len = len(chapter_text)

    # ---------- 4. 按字数分页 (page_size 字/页) ----------
    # 同步 page_size: widget state 可能变了, 但 session_state['reader_page_size'] 可能未更新
    page_size = st.session_state.get('reader_page_size_input', st.session_state.get('reader_page_size', 600))
    if 'reader_page_size' not in st.session_state or st.session_state['reader_page_size'] != page_size:
        st.session_state['reader_page_size'] = page_size
    # 在 chunk_text 边界附近找最近换行 (避免把一行中间切开)
    page_breaks: list[int] = [0]  # 每页起点字符偏移 (在 chapter_text 内)
    pos = 0
    while pos + page_size < chapter_len:
        # 试图在 [pos+page_size-100, pos+page_size+50] 范围找 \n
        end = pos + page_size
        nl = chapter_text.rfind('\n', max(pos + page_size - 150, pos), min(end + 50, chapter_len))
        if nl <= pos:
            nl = end  # 没找到换行, 硬切
        page_breaks.append(nl)
        pos = nl
        # 跳过连续换行/空格
        while pos < chapter_len and chapter_text[pos] in ' \n\r\t':
            pos += 1
        if pos > nl:
            page_breaks[-1] = pos  # 调整上一页终点
    page_breaks.append(chapter_len)  # 尾页终点
    total_pages = len(page_breaks) - 1

    # ---------- 5. 页面状态 ----------
    state_key = f'_rd_{book_id}_{sel_chapter}_{page_size}'
    if st.session_state.get('_reader_state_key') != state_key:
        # 用 bisect 把 saved 的 char_offset 转成页码 (铲屎官 2026-08-26)
        import bisect
        if sel_chapter == init_ch and chapter_text:
            init_page_idx = bisect.bisect_right(page_breaks, init_char) - 1
            init_page_idx = max(0, min(init_page_idx, max(0, total_pages - 1)))
        else:
            init_page_idx = 0
        st.session_state['reader_page_idx'] = init_page_idx
        st.session_state['_reader_state_key'] = state_key

    page_idx = max(0, min(st.session_state.get('reader_page_idx', 0), max(0, total_pages - 1)))

    # ---------- 6. 工具栏: 字号 / 进度 / 音色 / 页大小 ----------
    tb1, tb2, tb3, tb4 = st.columns([1.2, 2, 1.2, 1.2])
    with tb1:
        font_size = st.slider('字号', 14, 28, saved.get('font_size', 18) if saved else 18, key='rd_font')
    with tb2:
        # 全书进度 (基于字符偏移)
        ch_offset = chapter_chars_before[sel_chapter] + page_breaks[page_idx]
        global_pct = (ch_offset / max(total_chars, 1))
        st.progress(min(1.0, global_pct), text=f'全书 {ch_offset:,}/{total_chars:,} 字 ({global_pct*100:.1f}%)')
    with tb3:
        VOICE_OPTIONS = {
            '🎀 晓晓 (女·温柔)': 'zh-CN-XiaoxiaoNeural',
            '🎩 云希 (男·青年)': 'zh-CN-YunxiNeural',
            '🎩 云扬 (男·新闻)': 'zh-CN-YunyangNeural',
            '🎀 晓伊 (女·甜)': 'zh-CN-XiaoyiNeural',
            '🎀 晓涵 (女·主播)': 'zh-CN-XiaohanNeural',
        }
        # 默认上次选的
        cur_voice = st.session_state.get('global_voice', 'zh-CN-XiaoxiaoNeural')
        cur_label = next((k for k, v in VOICE_OPTIONS.items() if v == cur_voice), list(VOICE_OPTIONS.keys())[0])
        chosen_label = st.selectbox('🎙️ 音色', list(VOICE_OPTIONS.keys()),
                                    index=list(VOICE_OPTIONS.keys()).index(cur_label),
                                    key='reader_voice')
        st.session_state['global_voice'] = VOICE_OPTIONS[chosen_label]
    with tb4:
        page_size_new = st.number_input('每页字数', min_value=200, max_value=2000,
                                        value=page_size, step=100, key='reader_page_size_input')
        # 不再需要 st.rerun (上面的同步已处理)

    # 断点续读提示 (显示)
    if saved and saved.get('updated_at'):
        last_ch = saved.get('chapter_index', 0)
        last_offset = saved.get('page_idx', 0)
        # 计算上次读到的页码: 在 last_ch 章内, char offset -> page idx
        last_ch_text = chapter_texts.get(last_ch, '')
        if last_ch_text:
            # 复用 page_breaks 逻辑: 每页 page_size 字, 行切
            last_page_breaks = [0]
            pos = 0
            while pos < len(last_ch_text):
                # 找最近的换行符
                end = min(pos + page_size, len(last_ch_text))
                r = last_ch_text.rfind('\n', pos, end)
                if r != -1 and r > pos:
                    end = r
                last_page_breaks.append(end)
                pos = end
            # bisect_right 找到 <= last_offset 的最大位置
            import bisect
            last_page_idx = bisect.bisect_right(last_page_breaks, last_offset) - 1
            last_page_idx = max(0, min(last_page_idx, len(last_page_breaks)-2))
        else:
            last_page_idx = 0
        if chapter_titles.get(last_ch):
            st.caption(f'🔖 上次读到 → {chapter_titles[last_ch]} · 第 {last_page_idx+1} / {total_pages} 页 · {saved["updated_at"].strftime("%m-%d %H:%M")}')
        else:
            st.caption(f'🔖 上次读到 第 {last_ch+1} 章 · 位置 {last_offset} · {saved["updated_at"].strftime("%m-%d %H:%M")}')

    # ---------- 7. 正文渲染 (书页风格) ----------
    page_text = chapter_text[page_breaks[page_idx]:page_breaks[page_idx + 1]].strip()
    # 章节标题 (epub 源才有, chunks 源为空)
    ch_title_html = ''
    if chapter_titles.get(sel_chapter):
        ch_title_html = (
            f'<div style="max-width:800px;margin:0 auto 8px auto;font-size:14px;'
            f'color:#a8826a;text-align:center;font-family:\'Noto Serif SC\',serif;">'
            f'— {chapter_titles[sel_chapter]} —</div>'
        )
    body_html = (
        f'{ch_title_html}'
        f'<div style="font-size:{font_size}px; line-height:2.0; letter-spacing:0.5px; '
        f'max-width:800px; margin:0 auto; padding:32px 40px; background:#fbf6ec; '
        f'border-radius:12px; box-shadow:0 2px 12px rgba(0,0,0,0.08); '
        f'color:#2b2b2b; font-family:\'Noto Serif SC\', \'Source Han Serif SC\', serif; '
        f'white-space:pre-wrap;">'
        f'{page_text}</div>'
    )
    st.markdown(body_html, unsafe_allow_html=True)

    # 存进度: chapter_index + 本章内字符偏移
    char_offset_in_chapter = page_breaks[page_idx]
    save_reading_progress(book_id, sel_chapter, char_offset_in_chapter,
                          scroll_pct=ch_offset / max(total_chars, 1),
                          font_size=font_size)

    # ---------- 8. 控制栏 ----------
    col_prev, col_info, col_next, col_tts = st.columns([1, 1.4, 1, 1])
    with col_prev:
        if st.button('◀ 上一页', disabled=(page_idx == 0), use_container_width=True, key='rd_prev'):
            st.session_state['reader_page_idx'] = page_idx - 1
            st.rerun()
    with col_info:
        st.caption(f'第 {page_idx+1} / {total_pages} 页 · {len(page_text):,} 字')
        if chapter_titles.get(sel_chapter):
            st.caption(f'→ {chapter_titles[sel_chapter]} · 第 {page_idx+1} / {total_pages} 页')
        # 用 form 包起来, 避免 number_input 的 widget_state 与 page_idx 不同步时
        # 触发误判重置 page_idx (铲屎官 2026-08-26 实测 bug)
        with st.form('reader_jump_form', clear_on_submit=False):
            jump = st.number_input('跳到页', min_value=1, max_value=total_pages,
                                   value=page_idx + 1, step=1, key='reader_jump',
                                   label_visibility='collapsed')
            submitted = st.form_submit_button('跳', use_container_width=True)
            if submitted and jump != page_idx + 1:
                st.session_state['reader_page_idx'] = jump - 1
                st.rerun()
    with col_next:
        if st.button('下一页 ▶', disabled=(page_idx >= total_pages - 1),
                     use_container_width=True, key='rd_next'):
            st.session_state['reader_page_idx'] = page_idx + 1
            st.rerun()
    with col_tts:
        render_tts_button(page_text, key=f'rd_tts_{book_id}_{sel_chapter}_{page_idx}',
                          voice_label=f'🎙️ 听这页 ({chosen_label.split()[0]})')

# ====== 🚀 任务队列 (铲屎官 2026-08-25 钓定) ======
elif page == '🚀 任务队列':
    st.title('🚀 任务队列')

    # 顶部状态栏 + 控制
    jobs = list_jobs(limit=30)
    running = [j for j in jobs if j['status'] == 'running']
    queued = [j for j in jobs if j['status'] == 'queued']
    completed = [j for j in jobs if j['status'] == 'completed']
    failed = [j for j in jobs if j['status'] == 'failed']

    c1, c2, c3, c4 = st.columns(4)
    c1.metric('🚀 运行中', len(running))
    c2.metric('⏳ 等待中', len(queued))
    c3.metric('✅ 已完成', len(completed))
    c4.metric('❌ 失败', len(failed))

    ctrl_l, ctrl_r = st.columns([3, 1])
    with ctrl_l:
        st.caption(f'共 {len(jobs)} 个任务 · 后台 worker 已启动 (守护线程)')
    with ctrl_r:
        col_refresh, col_clear = st.columns(2)
        if col_refresh.button('🔄 刷新', use_container_width=True):
            st.rerun()
        if col_clear.button('🗑️ 清已完成', use_container_width=True, help='删除 completed/failed/cancelled'):
            n = clear_completed_jobs()
            st.success(f'清 {n} 个')
            st.rerun()

    st.divider()

    if not jobs:
        st.info('📭 还没有任务。点击侧栏“📤 上传书本”上传 epub 即可创建任务。')
        st.stop()

    # 按状态分组展示: running 在前, 然后 queued, 然后 completed/failed
    for job in jobs:
        status = job['status']
        step = job.get('current_step') or '—'
        prog = job.get('step_progress') or 0
        total = job.get('step_total') or 0
        book_name = job['book_name']
        jid = job['id']

        # 状态 emoji
        status_icon = {
            'queued': '⏳',
            'running': '🚀',
            'completed': '✅',
            'failed': '❌',
            'cancelled': '🚫',
        }.get(status, '❓')

        # 容器
        with st.container(border=True):
            # 头: 状态 + 书名 + ID
            hdr_l, hdr_r = st.columns([3, 1])
            with hdr_l:
                st.markdown(f"{status_icon} **#{jid} · {book_name}**")
                st.caption(f"步骤: {step}")
            with hdr_r:
                # 取消按钮 (仅 queued/running 可取消)
                if status in ('queued', 'running'):
                    if st.button('🚫 取消', key=f'cancel_{jid}', use_container_width=True):
                        cancel_job(jid)
                        st.rerun()
                # 重试按钮 (failed/cancelled 可重试, 铲屎官 2026-08-25)
                if status in ('failed', 'cancelled'):
                    if st.button('🔁 重试', key=f'retry_{jid}', use_container_width=True, type='primary'):
                        if retry_job(jid):
                            st.success(f'任务 #{jid} 已重新入队')
                        else:
                            st.error('重试失败')
                        st.rerun()

            # 进度条
            if total > 0:
                pct = min(prog / total, 1.0)
                st.progress(pct, text=f'{prog} / {total}')
            elif status == 'running':
                st.progress(0.0, text='运行中...')
            elif status == 'completed':
                st.progress(1.0, text='完成')
            elif status == 'failed':
                st.error(f'失败: {job.get("error", "未知错误")[:200]}')

            # 时间信息
            time_info = []
            if job.get('created_at'):
                time_info.append(f'入队: {job["created_at"].strftime("%H:%M:%S")}')
            if job.get('started_at'):
                time_info.append(f'启动: {job["started_at"].strftime("%H:%M:%S")}')
            if job.get('finished_at'):
                time_info.append(f'结束: {job["finished_at"].strftime("%H:%M:%S")}')
            st.caption(' · '.join(time_info))

            # 实时日志
            log_expanded = (status == 'running')
            with st.expander('📜 实时日志', expanded=log_expanded):
                log_text = get_job_log(job['log_path'], tail_bytes=32768 if status in ('running', 'failed') else None)
                if log_text:
                    st.code(log_text, language='log')
                else:
                    st.caption('(日志为空)')


# ====== 页脚 ======
st.sidebar.divider()
st.sidebar.caption('''
🐾 jiujiu-bookstack v0.2.0
- GitHub: github.com/fuermos/jiujiu-bookstack
- MCP: 12 tools, stdio
- Stack: PG + bge-m3 + Streamlit
''')


# ====== 启动后台 pipeline worker (必须在所有函数定义之后, 2026-08-25 修复 NameError) ======
import threading as _th_init
_sys_dbg.stderr.write("[boot] before worker setup\n"); _sys_dbg.stderr.flush()
try:
    _existing_threads = [t.name for t in _th_init.enumerate()]
    if 'pipeline-worker' not in _existing_threads:
        _t = _th_init.Thread(target=_worker_loop, daemon=True, name='pipeline-worker')
        _t.start()
        _sys_dbg.stderr.write("[boot] pipeline worker started (module-end)\n"); _sys_dbg.stderr.flush()
    else:
        _sys_dbg.stderr.write("[boot] pipeline worker already running\n"); _sys_dbg.stderr.flush()
except Exception as _e:
    _sys_dbg.stderr.write(f"[boot] FAIL: {_e}\n"); _sys_dbg.stderr.flush()
    print(f'⚠️ pipeline worker 启动失败: {_e}')