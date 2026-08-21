#!/usr/bin/env python3
"""import_book.py - 步骤 1: epub → chunks → 入库

支持格式: .epub / .mobi / .txt
去重: UNIQUE (book_id, MD5(chunk_text))
"""
import hashlib
import re
from pathlib import Path
from typing import Optional

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

from db import get_cursor


CHUNK_SIZE = 1000  # 每 chunk 目标字符数
OVERLAP = 100      # chunk 之间重叠（防上下文断片）


def detect_new_books(books_dir: Path) -> list[int]:
    """检测 books_dir 里所有待处理 epub/mobi，返回 book_ids（已入库）"""
    if books_dir.is_file():
        files = [books_dir]
    else:
        files = list(books_dir.glob('*.epub')) + list(books_dir.glob('*.mobi'))

    book_ids = []
    for f in files:
        book_id = import_epub(f)
        if book_id:
            book_ids.append(book_id)
    return book_ids


def import_epub(file_path: Path, force: bool = False) -> Optional[int]:
    """epub → chunks 入库，返回 book_id

    幂等: 已存在的书名不会重复 import
    """
    book_name = file_path.stem.replace('_', ' ').strip()
    book_name = re.sub(r'\s*\(z-library.*?\)\s*', '', book_name, flags=re.IGNORECASE).strip()

    # 查重（按 md5）
    md5 = hashlib.md5(file_path.read_bytes()).hexdigest()
    with get_cursor() as cur:
        cur.execute('SELECT id FROM books WHERE md5 = %s', (md5,))
        row = cur.fetchone()
        if row and not force:
            print(f'  ⏭️ {book_name} 已存在 (id={row["id"]})')
            return row['id']

        # 入库
        cur.execute(
            'INSERT INTO books (name, path, md5) VALUES (%s, %s, %s) ON CONFLICT (md5) DO UPDATE SET name=EXCLUDED.name RETURNING id',
            (book_name, str(file_path), md5),
        )
        book_id = cur.fetchone()['id']
        print(f'  📖 新书入库: id={book_id} name={book_name[:50]}')

    # 解析 + chunks
    if file_path.suffix.lower() == '.epub':
        chapters = parse_epub(file_path)
    else:
        chapters = [(None, file_path.read_text(encoding='utf-8', errors='ignore'))]

    chunks = split_into_chunks(chapters)
    insert_chunks(book_id, chunks)
    print(f'  ✅ chunks 入库: {len(chunks)} 条')
    return book_id


def parse_epub(file_path: Path) -> list[tuple[str, str]]:
    """解析 epub 章节

    Returns: [(chapter_title, content), ...]
    """
    book = epub.read_epub(str(file_path))
    chapters = []

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        content = item.get_content().decode('utf-8', errors='ignore')
        soup = BeautifulSoup(content, 'lxml')
        text = soup.get_text(separator='\n', strip=True)
        if text.strip():
            title = soup.title.string if soup.title else ''
            chapters.append((title or 'Untitled', text))

    # 也加 toc 作为参考
    return chapters


def split_into_chunks(chapters: list[tuple[str, str]]) -> list[dict]:
    """把章节按 CHUNK_SIZE 切 chunks，带 OVERLAP 重叠"""
    chunks = []
    chapter_index = 0
    for title, content in chapters:
        # 按段落切（避免把句子切两半）
        paragraphs = re.split(r'\n{2,}', content)
        current = ''
        for p in paragraphs:
            if len(current) + len(p) > CHUNK_SIZE and current:
                chunks.append({
                    'chapter_index': chapter_index,
                    'chunk_text': current.strip(),
                })
                # 保留 overlap
                current = current[-OVERLAP:] + '\n' + p
            else:
                current += '\n' + p if current else p
        if current.strip():
            chunks.append({
                'chapter_index': chapter_index,
                'chunk_text': current.strip(),
            })
        chapter_index += 1
    return chunks


def insert_chunks(book_id: int, chunks: list[dict]):
    """批量插入 chunks（去重由 UNIQUE 约束保证）"""
    with get_cursor() as cur:
        for c in chunks:
            cur.execute(
                '''INSERT INTO chunks (book_id, chapter_index, chunk_text, char_count)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (book_id, md5(chunk_text)) DO NOTHING''',
                (book_id, c['chapter_index'], c['chunk_text'], len(c['chunk_text'])),
            )
