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
    封面提取 (主人 2026-08-22 钓定: 剧本杀页平铺需展示封面)
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

    # 提取封面图 (epub 第一张 img)
    if file_path.suffix.lower() == '.epub':
        try:
            cover_rel = extract_cover(file_path, book_id)
            if cover_rel:
                with get_cursor() as cur:
                    cur.execute('UPDATE books SET cover_url = %s WHERE id = %s', (cover_rel, book_id))
                print(f'  🖼️  封面已提取: {cover_rel}')
        except Exception as e:
            print(f'  ⚠️  封面提取失败: {e}')

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

    # 铲屎官 2026-08-25 钩定: 过滤版权页/封面/目录页等非正文
    # (book 27 ch=0 "版权信息 书名 ISBN" / book 27 ch=1 "目录" 都是该过滤的)
    SKIP_PATTERNS = (
        'copyright', 'cover', 'titlepage', 'colophon', 'imprint',
        'nav', 'toc',  # 目录文件
        'index', 'glossary', 'colophon',  # 索引
    )
    SKIP_CONTENT_PREFIXES = (
        '版 权', '版权', 'Copyright', 'COPYRIGHT',
        '图书在版编目', 'CIP', 'ISBN',  # 版权信息标记
        'All rights reserved', '©',
        '目  录', '目 录', '目录', '总目录',  # 目录页
    )
    MIN_CONTENT_LEN = 50  # 太短的 (e.g. "上册") 跳过

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        name = item.get_name().lower() if hasattr(item, 'get_name') else ''
        # 文件名过滤
        if any(p in name for p in SKIP_PATTERNS):
            continue

        content = item.get_content().decode('utf-8', errors='ignore')
        soup = BeautifulSoup(content, 'lxml')
        text = soup.get_text(separator='\n', strip=True)
        if not text.strip():
            continue

        # 内容前缀过滤
        text_start = text.strip()[:30].replace('\n', ' ').replace(' ', ' ')
        if any(text_start.startswith(prefix.replace(' ', ' ')) for prefix in SKIP_CONTENT_PREFIXES):
            print(f'  ⏭️  跳过非正文: {item.get_name()} (前 30 字符: {text_start[:30]!r})')
            continue

        # 太短跳过
        if len(text.strip()) < MIN_CONTENT_LEN:
            print(f'  ⏭️  跳过过短章节: {item.get_name()} ({len(text.strip())} chars)')
            continue

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


def extract_cover(file_path: Path, book_id: int) -> Optional[str]:
    """从 epub 提取封面图（第一张 img, 或 metadata 指定的 cover）

    返回封面相对路径 (如 'covers/24.jpg'), 失败返回 None
    """
    # 用 __file__ 反推项目根，避免 CWD 错乱（主人口令 2026-08-24）
    # 之前 Path('data/covers') 相对 CWD，从 scripts/ 跑会歪到 scripts/data/covers/
    COVERS_DIR = Path(__file__).parent.parent / 'data' / 'covers'
    COVERS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        book = epub.read_epub(str(file_path))

        # 1. 优先找 metadata 指定的 cover
        cover_data = None
        cover_ext = 'jpg'

        # 遍历所有 items, 找带 cover 属性或 'cover' in id 的图
        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
            item_id = (item.get_id() or '').lower()
            if 'cover' in item_id or '封面' in item_id:
                cover_data = item.get_content()
                # 推断扩展名
                mt = (item.media_type or 'image/jpeg').lower()
                cover_ext = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif'}.get(mt, 'jpg')
                break

        # 2. 没找到: 取第一张图 (按文件顺序)
        if not cover_data:
            for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                cover_data = item.get_content()
                mt = (item.media_type or 'image/jpeg').lower()
                cover_ext = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif'}.get(mt, 'jpg')
                break

        if not cover_data:
            return None

        # 存盘
        out_path = COVERS_DIR / f'{book_id}.{cover_ext}'
        out_path.write_bytes(cover_data)
        return f'covers/{book_id}.{cover_ext}'
    except Exception as e:
        print(f'  ⚠️  封面解析失败: {e}')
        return None
