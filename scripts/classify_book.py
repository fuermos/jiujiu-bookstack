#!/usr/bin/env python3
"""classify_book.py - 根据书名给 books.category 赋值

主人 2026-08-22 发现: pipeline 跑完后 books.category 是空的,
所以 web UI 分类分布显示 0

方案: 用 category_rules.yaml 的正则匹配 + LLM fallback
- 先用规则匹配 (快, 0 API cost)
- 规则失败才调 LLM (兜底)
"""
import re
from pathlib import Path

import yaml
from db import get_cursor

CATEGORY_RULES_PATH = Path('config/category_rules.yaml')

DEFAULT_CATEGORY = '其他'


def load_rules() -> dict:
    if not CATEGORY_RULES_PATH.exists():
        return {}
    with CATEGORY_RULES_PATH.open(encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def classify_by_rules(book_name: str, rules: dict) -> str | None:
    """按规则匹配书名 (优先级: 第一个匹配)"""
    for category, cfg in rules.items():
        pattern = cfg.get('pattern', '')
        if not pattern:
            continue
        try:
            if re.search(pattern, book_name):
                return category
        except re.error:
            continue
    return None


def classify_book(book_id: int, force: bool = False) -> str:
    """给一本书分类, 返回分类名. 已分类跳过(除非 force)."""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT name, category FROM books WHERE id = %s', (book_id,))
        row = cur.fetchone()
    if not row:
        return ''
    name = row['name']
    existing = row.get('category')

    if existing and not force:
        return existing

    rules = load_rules()
    category = classify_by_rules(name, rules)

    if not category:
        # 兜底: 用关键词启发式
        if any(kw in name for kw in ['写作', '随笔', '散文', '日记', '故事']):
            category = '写作'
        elif any(kw in name for kw in ['心理', '情绪', '疗愈', '情商']):
            category = '心理'
        elif any(kw in name for kw in ['历史', '传记', '战争', '考古']):
            category = '历史'
        elif any(kw in name for kw in ['哲学', '思想', '论语', '道德经']):
            category = '哲学'
        elif any(kw in name for kw in ['物理', '数学', '化学', '生物', '天文']):
            category = '科学'
        elif any(kw in name for kw in ['福尔摩斯', '侦探', '推理', '金庸', '古龙', '武侠']):
            category = '文学'
        else:
            category = DEFAULT_CATEGORY

    with get_cursor() as cur:
        cur.execute(
            'UPDATE books SET category = %s WHERE id = %s',
            (category, book_id),
        )

    print(f'  🏷️  book={book_id} ({name}) → {category}')
    return category


def classify_all_books(force: bool = False) -> int:
    """给所有未分类的书分类, 返回处理数"""
    with get_cursor() as cur:
        cur.execute(
            'SELECT id, name FROM books WHERE category IS NULL OR category = %s',
            ('',) if force else ('' if False else None),
        )
        # 简单版: 拿所有书
        cur.execute('SELECT id, name FROM books')
        books = cur.fetchall()

    print(f'📚 共 {len(books)} 本书, 开始分类...')
    n = 0
    for b in books:
        classify_book(b['id'], force=force)
        n += 1
    print(f'✅ 分类完成, 共处理 {n} 本')
    return n


if __name__ == '__main__':
    import sys
    from db import init_pool
    from config_loader import load_config
    config = load_config('/app/config/config.yaml' if Path('/app/config/config.yaml').exists() else 'config/config.yaml')
    init_pool(config['database'])

    force = '--force' in sys.argv
    classify_all_books(force=force)