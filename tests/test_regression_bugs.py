#!/usr/bin/env python3
"""test_regression_bugs.py - 回归测试套件

主人 2026-08-24 钦定 (TDD 流程):
- 先写测试 (RED) → 修复 (GREEN) → 重构 (REFACTOR)
- 每次修复完必跑 pytest tests/test_regression_bugs.py -v
- 每个测试对应一个具体的回归 bug, 带 @pytest.mark.bug_YYYY_MM_DD tag

覆盖 5 大场景:
1. test_no_cross_book_contamination     - 跨书串场 (Holmes 角色出现在数学书)
2. test_holmes_specific_characters       - 福尔摩斯剧本必须用福尔摩斯角色
3. test_single_script_book_routing       - 单剧本智能跳过 modal 步骤
4. test_token_persistence                - 登录 token 跨刷新持久化
5. test_mcp_list_books_no_duplicates     - MCP list_books 去重

运行:
    pytest tests/test_regression_bugs.py -v
    pytest tests/test_regression_bugs.py -m regression -v
"""
import json
import sys
from pathlib import Path

import pytest

# 让测试 import jiujiu-bookstack 模块
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "web"))

# ---- Fixtures ----

@pytest.fixture(scope="session")
def db_pool():
    """共享 DB 连接池"""
    from config_loader import load_config
    import db as db_mod
    cfg = load_config(str(ROOT / "config" / "config.yaml"))
    db_mod.init_pool(cfg["database"])
    return db_mod


@pytest.fixture(scope="session")
def db_cur(db_pool):
    """默认游标 (autouse=False, 用 with 拿)"""
    from db import get_cursor
    with get_cursor() as cur:
        yield cur


@pytest.fixture(scope="session")
def all_books(db_cur):
    """所有书的列表 [{id, name}, ...]"""
    db_cur.execute("SELECT id, name FROM books ORDER BY id")
    return [dict(r) for r in db_cur.fetchall()]


@pytest.fixture(scope="session")
def all_scripts(db_cur):
    """所有剧本的列表 + 完整 script_json"""
    db_cur.execute("""
        SELECT id, book_id, chapter_index, game_type, total_scenes, script_json
        FROM game_scripts ORDER BY book_id, chapter_index
    """)
    return [dict(r) for r in db_cur.fetchall()]


# ============================================================
# 1. 跨书串场检测 (2026-08-24 主人反馈: 数学书出现 Holmes 角色)
# ============================================================

# 跨书黑名单 (主人 2026-08-24 钦定 + 历史污染数据)
CROSS_BOOK_TERMS = {
    "福尔摩斯": ["华生", "福尔摩斯", "莫斯坦", "巴塞洛缪", "斯莫尔", "歇洛克", "Baker Street", "221B"],
    "神奇的数学": ["数学", "李永乐", "思考者", "探索者", "解谜人", "算珠子", "street_vendor_grandpa", "math_explorer"],
    "心流": ["心流", "米哈里", "契克森米哈赖", "莉莉", "工人", "researcher_chick", "worker_lili", "reader_xiao_xiao", "modern_reader_xiao_xiao"],
    "讲解的艺术": ["罗斯", "阿特金斯", "讲解", "presentation"],
}

# 每本书自己的"合法"角色 (白名单, 不在这个集合里就是污染)
BOOK_ALLOWED_ROLES = {
    "福尔摩斯": {"华生", "福尔摩斯", "莫斯坦", "巴塞洛缪", "斯莫尔", "歇洛克", "华生医生", "watson", "holmes", "莫斯坦小姐",
                  "华生医生 (主角)", "福尔摩斯 (观察者)", "旁白"},
    "神奇的数学": {"思考者", "探索者", "解谜人", "现代读者·笑笑", "modern_reader_xiao_xiao", "思考者 (数学启蒙者)",
                  "探索者 (好奇的提问者)", "解谜人 (难题挑战者)", "街头大爷", "math_explorer_li_yongle_perspective",
                  "street_vendor_grandpa", "读者 (上帝视角)"},
    "心流": {"现代读者·笑笑", "观察者·米哈里", "体验者·莉莉", "reader_xiao_xiao", "researcher_chick", "worker_lili",
            "读者 (上帝视角)"},
    "讲解的艺术": {"讲解者", "聆听者", "设计者", "改造者", "现代读者·笑笑", "读者 (上帝视角)"},
}


@pytest.mark.regression
@pytest.mark.bug_2026_08_24
def test_no_cross_book_contamination(all_scripts, all_books):
    """回归: book A 的剧本不能含 book B 的角色/术语

    Bug 历史: 数学书 (book 27) 出现 '华生/福尔摩斯' 等 Holmes 角色
    """
    book_map = {b["id"]: b["name"] for b in all_books}

    for script in all_scripts:
        book_id = script["book_id"]
        book_name = book_map.get(book_id, "?")
        if book_name not in BOOK_ALLOWED_ROLES:
            continue  # 测试书不验证

        sj = script["script_json"]
        if isinstance(sj, str):
            sj = json.loads(sj)
        all_text = json.dumps(sj, ensure_ascii=False)
        allowed_roles = BOOK_ALLOWED_ROLES.get(book_name, set())

        # 1. 检查"角色"层 (_player_role_options + scenes.player_role)
        role_options = set(sj.get("_player_role_options", []))
        scene_roles = set()
        for s in sj.get("scenes", []):
            pr = s.get("player_role", "")
            if pr:
                scene_roles.add(pr)
            for q in s.get("questions", []):
                rp = q.get("role_perspective", "")
                if rp:
                    scene_roles.add(rp)

        all_roles = role_options | scene_roles
        foreign_roles = all_roles - allowed_roles
        # 排除通用占位符
        foreign_roles = {r for r in foreign_roles if r and r not in {"读者 (上帝视角)", "主角", "NPC", "旁白"}}

        assert not foreign_roles, (
            f"❌ [book {book_id} {book_name} script {script['id']}] 跨书污染: "
            f"roles={foreign_roles} 不在白名单 {allowed_roles}"
        )

        # 2. 检查"内容关键词"层 (问题/答案/描述里不能有别书的核心词)
        for other_book, bad_terms in CROSS_BOOK_TERMS.items():
            if other_book == book_name:
                continue
            for term in bad_terms:
                if term in all_text and len(term) > 2:
                    # 例外: 如果是引用角色英文别名, 看白名单
                    pytest.fail(
                        f"❌ [book {book_id} {book_name} script {script['id']}] "
                        f"含 '{other_book}' 的术语: '{term}'"
                    )


# ============================================================
# 2. Holmes 剧本必须用福尔摩斯角色 (反向测试)
# ============================================================

@pytest.mark.regression
@pytest.mark.bug_2026_08_24
def test_holmes_specific_characters(all_scripts, all_books):
    """回归: 福尔摩斯 (book 24) 的剧本角色必须是 Holmes 系

    Bug 历史: 福尔摩斯剧本里出现数学书角色 (script 32)
    """
    book_map = {b["id"]: b["name"] for b in all_books}

    for script in all_scripts:
        if book_map.get(script["book_id"]) != "福尔摩斯":
            continue

        sj = script["script_json"]
        if isinstance(sj, str):
            sj = json.loads(sj)

        # 福尔摩斯剧本里必须出现至少 1 个 Holmes 系关键词
        all_text = json.dumps(sj, ensure_ascii=False)
        holmes_terms = ["福尔摩斯", "华生", "莫斯坦", "歇洛克", "Baker Street", "221B"]
        found = [t for t in holmes_terms if t in all_text]

        assert found, (
            f"❌ [book 24 script {script['id']}] 福尔摩斯剧本里找不到 Holmes 角色关键词! "
            f"检查是否被错位配置了别的书的角色"
        )


# ============================================================
# 3. 单剧本书的智能跳过 (modal 三步流必须工作)
# ============================================================

@pytest.mark.regression
@pytest.mark.bug_2026_08_24
def test_single_script_book_routing(db_cur, all_books):
    """回归: 单剧本的书 (e.g., 数学/心流) 应该自动跳过步骤①选剧本

    Bug 历史: 旧版 modal 一锅端 (剧本 + 角色 + 开始 全堆)
    """
    from db import get_cursor
    with get_cursor() as cur:
        cur.execute("""
            SELECT b.id, b.name, COUNT(gs.id) AS scripts
            FROM books b
            LEFT JOIN game_scripts gs ON gs.book_id = b.id
            WHERE b.id IN (SELECT book_id FROM game_scripts GROUP BY book_id HAVING COUNT(*) = 1)
            GROUP BY b.id, b.name
            ORDER BY b.id
        """)
        single_script_books = [dict(r) for r in cur.fetchall()]

    assert single_script_books, "测试前提: 至少要有一本单剧本的书 (e.g., 27 数学/28 心流)"

    # 验证 web/app.py 的 modal 函数有"单剧本智能跳过"逻辑
    web_app = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
    assert "len(scripts_list) == 1" in web_app, (
        "❌ web/app.py 没有单剧本智能跳过逻辑"
    )
    assert "selected_script_id = only_script['id']" in web_app, (
        "❌ web/app.py 单剧本跳过时没自动设 selected_script_id"
    )


# ============================================================
# 4. 登录 token 持久化
# ============================================================

@pytest.mark.regression
@pytest.mark.bug_2026_08_24
def test_token_persistence():
    """回归: 登录后刷新页面不掉登录 (token 写到 query_params + 文件)

    Bug 历史: 刷页面就需要重登 (主人 2026-08-24)
    """
    # 1. user_manager 必须有 token 函数
    from user_manager import issue_token, resolve_token, revoke_token

    # 2. web/app.py 必须 issue + resolve token
    web_app = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
    assert "issue_token" in web_app, "web/app.py 没调 issue_token"
    assert "resolve_token" in web_app, "web/app.py 没调 resolve_token"
    assert "st.query_params['token']" in web_app, "web/app.py 没把 token 写到 URL"

    # 3. 验证 token 文件路径存在
    from user_manager import TOKEN_FILE
    assert TOKEN_FILE.parent.exists(), f"token 目录不存在: {TOKEN_FILE.parent}"


# ============================================================
# 5. MCP list_books 必须去重
# ============================================================

@pytest.mark.regression
@pytest.mark.bug_2026_08_24
def test_mcp_list_books_no_duplicates():
    """回归: MCP list_books 不能因 JOIN game_scripts 产生重复行

    Bug 历史: 福尔摩斯 11 剧本 → list_books 返回 18 行 (有 11 个福尔摩斯副本)
    → streamlit 重复 key='select_book_24' 报错
    """
    # 直接调 MCP (subprocess 方式)
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        p = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "scripts" / "mcp_server.py")],
        )
        async with stdio_client(p) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.call_tool("list_books", {"limit": 200})
                data = json.loads(result.content[0].text)
                return data

    books = asyncio.run(go())
    ids = [b["id"] for b in books]

    from collections import Counter
    dups = [(k, v) for k, v in Counter(ids).items() if v > 1]
    assert not dups, (
        f"❌ MCP list_books 重复 ids: {dups}\n"
        f"   (修法: tool_list_books SQL 改用 EXISTS 而不是 JOIN)"
    )
    assert len(books) == len(set(ids)), "❌ 重复 book_id 出现"


# ============================================================
# Bonus: 跨书数据统计 (辅助调试用)
# ============================================================

def test_stats_summary(db_cur):
    """辅助测试: 打印当前数据库状态"""
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) c FROM books")
        books_n = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM game_scripts")
        scripts_n = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(DISTINCT book_id) c FROM game_scripts")
        books_with_scripts = cur.fetchone()["c"]
        print(f"\n📊 库状态: {books_n} 书, {scripts_n} 剧本, "
              f"{books_with_scripts} 本有剧本")


if __name__ == "__main__":
    # 不用 pytest 跑也行 (单文件调试)
    print("⚠️  请用 pytest 跑: pytest tests/test_regression_bugs.py -v")
    sys.exit(1)