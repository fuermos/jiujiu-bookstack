#!/usr/bin/env python3
"""generate_summary.py - 步骤 7: 叙事化摘要

输入: chunks + SKILL.md + mindmap
输出: books.summary
"""
from pathlib import Path
from typing import Optional

from db import get_cursor
from llm_client import LLMClient


SYSTEM = '''你是一本图书摘要生成专家。根据输入的完整图书内容（及可选的上游 SKILL.md / 思维导图），生成一段简洁但信息丰富、叙事化的摘要。

要求:
- 包含书名、主要人物（不超过10个）、核心情节（3-5个关键事件）、故事背景、主题思想
- 若提供了 SKILL.md / mindmap, 优先引用其中已提炼的角色名、主题词、关键句
- 总字数控制在500-1000字
- 直接输出摘要正文，不要解释，不要 markdown 代码块'''


def generate_summary(book_id: int, llm: LLMClient, force: bool = False,
                     skill_path: Optional[str] = None,
                     mindmap_path: Optional[str] = None) -> Optional[str]:
    """生成/重生成摘要"""
    if not force:
        existing = get_existing_summary(book_id)
        if existing:
            print(f'  ⏭️  summary 已存在 ({len(existing)} chars)')
            return existing

    if force:
        # --force 模式: 先清空（绕过 get_or_generate 的缓存短路）
        with get_cursor() as cur:
            cur.execute('UPDATE books SET summary = NULL, summary_generated_at = NULL WHERE id = %s', (book_id,))

    book_name = get_book_name(book_id)
    full_text = load_chunks_text(book_id)

    # 注入 skill + mindmap
    skill_ref = ''
    sp = Path(skill_path) if skill_path else Path.home() / '.openclaw' / 'skill-archive' / 'books' / book_name.replace(' ', '-') / 'SKILL.md'
    if sp.exists():
        sk_text = sp.read_text(encoding='utf-8')[:2500]
        skill_ref = f'\n\n# 📋 上游 SKILL.md 提炼\n```\n{sk_text}\n```\n'

    mindmap_ref = ''
    mp = Path(mindmap_path) if mindmap_path else Path(f'mindmaps/{book_id}.mmd')
    if mp.exists():
        mm_text = mp.read_text(encoding='utf-8')[:1800]
        mindmap_ref = f'\n\n# 🗺️ 思维导图\n```mermaid\n{mm_text}\n```\n'

    user_msg = f'请为以下图书生成摘要：{skill_ref}{mindmap_ref}\n\n{full_text[:500000]}'

    print(f'  🤖 book={book_id} 生成 summary ...')
    text, provider = llm.call(SYSTEM, user_msg, max_tokens=2000, temperature=0.5)

    # 写 PG
    with get_cursor() as cur:
        cur.execute(
            'UPDATE books SET summary = %s, summary_generated_at = NOW() WHERE id = %s',
            (text, book_id),
        )

    print(f'  ✅ summary: {len(text)} chars (provider={provider})')
    return text


def load_chunks_text(book_id: int) -> str:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT chunk_text FROM chunks WHERE book_id = %s ORDER BY id', (book_id,))
        return ''.join(r['chunk_text'] for r in cur.fetchall())


def get_book_name(book_id: int) -> str:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT name FROM books WHERE id = %s', (book_id,))
        return cur.fetchone()['name']


def get_existing_summary(book_id: int) -> Optional[str]:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT summary FROM books WHERE id = %s', (book_id,))
        row = cur.fetchone()
        return row['summary'] if row and row['summary'] else None
