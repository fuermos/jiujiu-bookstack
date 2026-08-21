#!/usr/bin/env python3
"""embed_chunks.py - 步骤 2: 批量向量化

向量化 chunks → chunk_vectors (pgvector)
"""
import requests
from tqdm import tqdm

from db import get_cursor


def embed_pending_chunks(book_id: int, config: dict, batch_size: int = 50) -> int:
    """向量化指定书的 pending chunks，返回向量化条数"""
    pending_count = get_pending_count(book_id)
    if pending_count == 0:
        print(f'  ✅ book={book_id} 无 pending chunks')
        return 0

    print(f'  🔢 待向量化: {pending_count} 条')
    embedded = 0
    pbar = tqdm(total=pending_count, desc='embedding')

    while True:
        batch = get_pending_batch(book_id, batch_size)
        if not batch:
            break

        # 批量调 embedding API
        texts = [r['chunk_text'] for r in batch]
        vectors = call_embedding_api(texts, config)

        # 写 PG
        with get_cursor() as cur:
            for r, vec in zip(batch, vectors):
                cur.execute(
                    'INSERT INTO chunk_vectors (chunk_id, embedding) VALUES (%s, %s) ON CONFLICT (chunk_id) DO UPDATE SET embedding = EXCLUDED.embedding',
                    (r['id'], vec),
                )
        embedded += len(batch)
        pbar.update(len(batch))

    pbar.close()
    print(f'  ✅ 完成: {embedded} 条已向量化')
    return embedded


def get_pending_count(book_id: int) -> int:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(
            '''SELECT COUNT(*) AS n FROM chunks c
               WHERE c.book_id = %s
                 AND NOT EXISTS (SELECT 1 FROM chunk_vectors v WHERE v.chunk_id = c.id)''',
            (book_id,),
        )
        return cur.fetchone()['n']


def get_pending_batch(book_id: int, limit: int) -> list[dict]:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(
            '''SELECT c.id, c.chunk_text FROM chunks c
               WHERE c.book_id = %s
                 AND NOT EXISTS (SELECT 1 FROM chunk_vectors v WHERE v.chunk_id = c.id)
               ORDER BY c.id LIMIT %s''',
            (book_id, limit),
        )
        return cur.fetchall()


def call_embedding_api(texts: list[str], config: dict) -> list[list[float]]:
    """调 OpenAI 兼容 embedding API"""
    resp = requests.post(
        f'{config["base_url"]}/embeddings',
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {config["api_key"]}',
            'User-Agent': 'curl/8.5.0',
        },
        json={
            'model': config['model'],
            'input': texts,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()['data']
    # 按 index 排序（OpenAI 不保证顺序）
    data.sort(key=lambda x: x['index'])
    return [d['embedding'] for d in data]
