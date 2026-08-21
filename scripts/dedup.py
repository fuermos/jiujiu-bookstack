#!/usr/bin/env python3
"""dedup.py - 步骤 8: 同名变体查重

检测:
1. 同名变体（如《中国最美诗词典评（全6册）_z-lib.org.epub》vs 干净版）
2. chunks 数差异 >20% 视为疑似重复
3. 给出合并建议
"""
from collections import defaultdict

from db import get_cursor


def dedup_pass(book_id: int = None) -> dict:
    """查重，返回疑似重复组

    Args:
        book_id: 限定一本书，或 None 查全库
    """
    with get_cursor(dict_cursor=True) as cur:
        if book_id:
            cur.execute(
                '''SELECT b.id, b.name, b.category,
                          (SELECT COUNT(*) FROM chunks WHERE book_id=b.id) AS chunks
                   FROM books b WHERE b.id = %s''',
                (book_id,),
            )
        else:
            cur.execute(
                '''SELECT b.id, b.name, b.category,
                          (SELECT COUNT(*) FROM chunks WHERE book_id=b.id) AS chunks
                   FROM books b ORDER BY b.id'''
            )
        books = cur.fetchall()

    # 按规范化名字分组
    groups = defaultdict(list)
    for b in books:
        norm_name = normalize_name(b['name'])
        groups[norm_name].append(b)

    # 找出 chunks 数差异 >20% 的疑似重复
    duplicates = []
    for norm, bs in groups.items():
        if len(bs) < 2:
            continue
        chunks_counts = [b['chunks'] for b in bs]
        max_c = max(chunks_counts)
        min_c = min(chunks_counts)
        if max_c > 0 and (max_c - min_c) / max_c > 0.2:
            duplicates.append({
                'normalized_name': norm,
                'variants': [{'id': b['id'], 'name': b['name'], 'chunks': b['chunks']} for b in bs],
                'recommendation': recommend(bs),
            })

    print(f'  📊 待查重: {len(books)} 本')
    print(f'  📋 疑似重复组: {len(duplicates)} 组')
    for dup in duplicates:
        print(f'    ⚠️  {dup["normalized_name"]}: {len(dup["variants"])} 个变体')
        for v in dup['variants']:
            print(f'      - id={v["id"]} chunks={v["chunks"]} {v["name"][:40]}')
        print(f'    💡 建议: {dup["recommendation"]}')

    return {'total_books': len(books), 'duplicate_groups': duplicates}


def normalize_name(name: str) -> str:
    """规范化书名（去后缀、空格、副标题等）"""
    import re
    name = re.sub(r'\s*\(z-library.*?\)\s*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*_z-lib\.org\s*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*【[^】]*】\s*', '', name)
    name = re.sub(r'\s*（[^）]*）\s*', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def recommend(books: list) -> str:
    """给合并建议

    保留 chunks 数最多的，标记其他为 alias
    """
    keep = max(books, key=lambda b: b['chunks'])
    others = [b for b in books if b['id'] != keep['id']]
    return f'保留 id={keep["id"]} ({keep["chunks"]} chunks), ' + \
           ', '.join(f'id={o["id"]} (chunks={o["chunks"]}) 标为 alias 或删除' for o in others)
