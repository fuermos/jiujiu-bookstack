#!/usr/bin/env python3
"""generate_mindmap.py - 步骤 3.5: 思维导图生成

chunks → LLM 提炼结构骨架 → mermaid 源码
输出: workspace/mindmaps/{book_id}.mmd + .json
PG: book_mindmaps 表（除非 --no-pg）
"""
import json
from pathlib import Path
from typing import Optional

from db import get_cursor
from llm_client import LLMClient

MINDMAP_DIR = Path('mindmaps')


SYSTEM = '''你是文学分析专家。任务：基于用户提供的全书内容，生成 mermaid 思维导图源码。

要求:
1. 结构清晰: 根节点 = 书名，二级节点 = 主要维度
   - 主要人物（每行: 角色名 + 1 个简短特征标签）
   - 故事主线（开端/发展/高潮/结局）
   - 主题（4-8 个核心主题，每个 4-8 字概括）
   - 关键场景（3-5 个标志性场景，每个 6-12 字）
   - 金句（1-3 句原文金句，加引号）
2. 格式严格: 只输出 mermaid 源码，从 `mindmap` 开头，`{{(...)}}` 包裹根节点
3. 中文输出

参考格式:
```mermaid
mindmap
  root((《渔夫和他的灵魂》))
    主要人物
      渔夫 - 年轻执着
      小人鱼 - 美丽神秘
    故事主线
      开端 - ...
      发展 - ...
      高潮 - ...
      结局 - ...
    主题
      爱与牺牲
      灵魂的重量
    关键场景
      海边初遇
      巫师小屋
    金句
      "心灵是世界上最锋利的刀"
```'''


def generate_mindmap(book_id: int, llm: LLMClient, force: bool = False, save_pg: bool = True) -> Optional[str]:
    """生成思维导图，返回 mermaid 源码"""
    # 检测已有
    mm_path = MINDMAP_DIR / f'{book_id}.mmd'
    if mm_path.exists() and not force:
        print(f'  ⏭️  mindmap {mm_path} 已存在')
        return mm_path.read_text(encoding='utf-8')

    # 加载 chunks
    full_text = load_chunks_text(book_id)
    if not full_text:
        print(f'  ❌ book={book_id} 无 chunks')
        return None

    book_name = get_book_name(book_id)
    user_prompt = f'书籍内容:\n{full_text[:60000]}\n\n请生成 mermaid 思维导图源码。'

    print(f'  🧠 book={book_id} 生成 mindmap ...')
    text, provider = llm.call(SYSTEM, user_prompt, max_tokens=4000)

    # 提取 mermaid 代码块
    mermaid = extract_mermaid(text)

    MINDMAP_DIR.mkdir(parents=True, exist_ok=True)
    mm_path.write_text(mermaid, encoding='utf-8')

    # 同步写 .json（结构化版本）
    structure = mermaid_to_structure(mermaid)
    (MINDMAP_DIR / f'{book_id}.json').write_text(
        json.dumps(structure, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    if save_pg:
        save_to_pg(book_id, mermaid, structure, provider)

    print(f'  ✅ mindmap: {mm_path} ({len(mermaid)} chars)')
    return mermaid


def load_chunks_text(book_id: int, max_chars: int = 200000) -> str:
    """加载书的有代表性 chunks 拼接成文本

    设计：超长书（>200K 字符）只取代表性 chunks（开头/中间/结尾均匀采样），
    避免 LLM prompt 超 1M token 慢/OOM。

    采样策略：
    - 总字符数 <= max_chars: 全部加载
    - 总字符数 > max_chars: 按字符预算均匀采样 N 块
    """
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(
            'SELECT id, chunk_text FROM chunks WHERE book_id = %s ORDER BY id',
            (book_id,),
        )
        chunks = cur.fetchall()

    total_chars = sum(len(c['chunk_text']) for c in chunks)

    if total_chars <= max_chars:
        # 小书：全部加载
        return ''.join(c['chunk_text'] for c in chunks)

    # 大书：均匀采样
    # 估算需要多少块: ceil(total_chars / max_chars)
    n_needed = max(10, (total_chars + max_chars - 1) // max_chars)
    n_needed = min(n_needed, len(chunks))  # 不超过实际块数

    # 均匀采样（保留开头、结尾、中间均匀）
    if n_needed >= len(chunks):
        sampled = chunks
    else:
        step = len(chunks) / n_needed
        indices = [int(i * step) for i in range(n_needed)]
        # 去重 + 排序
        indices = sorted(set(min(idx, len(chunks) - 1) for idx in indices))
        sampled = [chunks[i] for i in indices]

    print(f'  📊 大书采样: {len(chunks)} 块 → {len(sampled)} 块 ({total_chars:,} → {sum(len(c["chunk_text"]) for c in sampled):,} 字符)')

    return ''.join(c['chunk_text'] for c in sampled)


def get_book_name(book_id: int) -> str:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT name FROM books WHERE id = %s', (book_id,))
        return cur.fetchone()['name']


def extract_mermaid(llm_output: str) -> str:
    """从 LLM 输出里提取 mermaid 代码"""
    # 尝试匹配 ```mermaid ... ``` 包裹
    import re
    m = re.search(r'```mermaid\s*(.*?)```', llm_output, re.DOTALL)
    if m:
        return m.group(1).strip()
    # fallback: 假设整段就是 mermaid
    return llm_output.strip()


def mermaid_to_structure(mermaid: str) -> dict:
    """简化版: 把 mermaid 转 dict 结构"""
    lines = [l for l in mermaid.split('\n') if l.strip()]
    structure = {'root': '', 'sections': {}}
    current_section = None
    for line in lines:
        if 'root' in line and '{{' in line:
            structure['root'] = line.split('root')[1].split('(')[0].strip('({ ')
        elif line.strip().startswith('root'):
            continue
        else:
            indent = len(line) - len(line.lstrip())
            text = line.strip()
            if indent <= 4:
                current_section = text
                structure['sections'][current_section] = []
            else:
                if current_section:
                    structure['sections'][current_section].append(text)
    return structure


def save_to_pg(book_id: int, mermaid: str, structure: dict, provider: str):
    """写 PG book_mindmaps 表"""
    with get_cursor() as cur:
        cur.execute(
            '''INSERT INTO book_mindmaps (book_id, mermaid, structure, llm_model)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (book_id) DO UPDATE SET
                   mermaid = EXCLUDED.mermaid,
                   structure = EXCLUDED.structure,
                   llm_model = EXCLUDED.llm_model,
                   updated_at = NOW()''',
            (book_id, mermaid, json.dumps(structure, ensure_ascii=False), provider),
        )
