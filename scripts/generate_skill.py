#!/usr/bin/env python3
"""generate_skill.py - 步骤 4: SKILL.md 生成

输入: chunks + mindmap
输出: skill-archive/books/{slug}/SKILL.md (+ cheatsheet/glossary/patterns/chapters)
"""
import json
import re
from pathlib import Path
from typing import Optional

from db import get_cursor
from llm_client import LLMClient

SKILL_DIR = Path.home() / '.openclaw' / 'skill-archive' / 'books'  # 默认


SYSTEM = '''你是一本图书的 skill 提炼师。任务：根据书的全文 + 思维导图，生成 SKILL.md。

要求:
1. frontmatter (YAML):
   - name: 书名
   - author: 作者
   - category: 分类
   - description: 100-200 字，介绍本书核心内容
   - tags: [关键词1, 关键词2, ...]

2. 主体结构:
   - # 书名
   - ## How to Use（什么时候用这个 skill）
   - ## Core Frameworks（5-10 个核心框架，每个 100-300 字）
   - ## Chapter Index（章节索引表）
   - ## Anti-Patterns（容易误解的反模式）

3. 必读:
   - 引用 mindmap 里的角色名（不要编造）
   - 引用 mindmap 里的主题词
   - 引用 mindmap 里的金句（加引号）

4. 输出: 完整 Markdown，从 frontmatter `---` 开始。

参考示例: 见 SKILL.md 范例（如《敢于脆弱》skill 含 7 个核心框架）。'''


def generate_skill(book_id: int, llm: LLMClient, force: bool = False,
                   mindmap_path: Optional[str] = None) -> Optional[Path]:
    """生成 SKILL.md，返回 skill 目录路径"""
    book_name = get_book_name(book_id)
    slug = book_name.replace(' ', '-').replace('_', '-')[:60]
    skill_path = SKILL_DIR / slug / 'SKILL.md'

    if skill_path.exists() and not force:
        print(f'  ⏭️  SKILL.md {skill_path} 已存在')
        return skill_path.parent

    # 加载 chunks
    full_text = load_chunks_text(book_id)
    chapters = detect_chapters(book_id)

    # 注入 mindmap
    mindmap_ref = ''
    mp = Path(mindmap_path) if mindmap_path else Path(f'mindmaps/{book_id}.mmd')
    if mp.exists():
        mm_text = mp.read_text(encoding='utf-8')[:2500]
        mindmap_ref = f'\n# 🗺️ 思维导图（结构骨架）\n```mermaid\n{mm_text}\n```\n'

    chapters_str = '\n'.join(f'- {i+1}. {title}' for i, (_, title) in enumerate(chapters[:8]))

    user_prompt = f'''# 书籍信息
- 标题：{book_name}
- 字符数：{len(full_text):,}
{mindmap_ref}
# 章节列表
{chapters_str if chapters_str else '（未提供，请自动划分章节）'}

# 正文（节选前 3000 字符）
{full_text[:3000]}

---

请按 system prompt 要求输出完整 SKILL.md。'''

    print(f'  🤖 book={book_id} 生成 SKILL.md ...')
    text, provider = llm.call(SYSTEM, user_prompt, max_tokens=16000)

    # 写入
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(text, encoding='utf-8')

    print(f'  ✅ SKILL.md: {skill_path} ({len(text)} chars, provider={provider})')
    return skill_path.parent


def load_chunks_text(book_id: int) -> str:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT chunk_text FROM chunks WHERE book_id = %s ORDER BY id', (book_id,))
        return ''.join(r['chunk_text'] for r in cur.fetchall())


def get_book_name(book_id: int) -> str:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT name FROM books WHERE id = %s', (book_id,))
        return cur.fetchone()['name']


def detect_chapters(book_id: int) -> list[tuple[int, str]]:
    """从 chunks 里检测章节标题（heuristic）"""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(
            '''SELECT chapter_index, MIN(chunk_text) AS first_text
               FROM chunks WHERE book_id = %s GROUP BY chapter_index ORDER BY chapter_index''',
            (book_id,),
        )
        rows = cur.fetchall()
    chapters = []
    for r in rows:
        text = r['first_text'][:100]
        m = re.match(r'(第[一二三四五六七八九十百千零\d]+章[^\n]*|Chapter\s+\d+[^\n]*|【[^\]]+】)', text)
        if m:
            chapters.append((r['chapter_index'], m.group(1)))
        else:
            chapters.append((r['chapter_index'], text[:50]))
    return chapters
