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


def mermaid_to_png(mermaid: str, output: Path, theme: str = 'default') -> bool:
    """用 Playwright 渲染 mermaid 为 PNG (主人 2026-08-22 要求"思维导图需要是图的形式")

    方案: 内嵌 mermaid.ink 在线渲染 (最快免依赖) → Playwright Chromium 截图 → 保存 PNG
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('  ⚠️  playwright 未装, 跳过 PNG 渲染')
        return False

    # 写临时 HTML
    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body{{margin:0;padding:20px;background:white;}}
.mermaid{{font-family:"Microsoft YaHei",sans-serif;}}
</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
</head><body>
<div class="mermaid">
{mermaid}
</div>
<script>
mermaid.initialize({{startOnLoad:true, theme:'{theme}', themeVariables:{{fontSize:'16px'}}}});
</script>
</body></html>'''
    tmp_html = Path('/tmp/mermaid_render.html')
    tmp_html.write_text(html, encoding='utf-8')

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1400, 'height': 1000})
            page.goto(f'file://{tmp_html}', wait_until='networkidle', timeout=30000)
            # 等 mermaid 渲染完
            page.wait_for_timeout(3000)
            # 截图 mermaid 节点
            elem = page.query_selector('.mermaid svg')
            if elem:
                elem.screenshot(path=str(output))
                browser.close()
                print(f'  ✅ PNG: {output} ({output.stat().st_size//1024} KB)')
                return True
            else:
                # fallback: 截整个 body
                page.screenshot(path=str(output), full_page=True)
                browser.close()
                print(f'  ⚠️  fallback 到 full_page 截图')
                return True
    except Exception as e:
        print(f'  ❌ PNG 渲染失败: {e}')
        return False


def generate_mindmap(book_id: int, llm: LLMClient, force: bool = False, save_pg: bool = True,
                     script_id: Optional[int] = None) -> Optional[str]:
    """生成思维导图，返回 mermaid 源码 (同步生成 PNG)

    主人 2026-08-22 钓定: 每本书多个剧本, 每个剧本独立思维导图
    - script_id=None: 按书生成汇总 mindmap (兼容老路径)
    - script_id=N: 读 game_scripts[id=N] 的 scenes 生成剧本级 mindmap

    输出: mindmaps/{book_id}.mmd (书级) / mindmaps/{book_id}_{script_id}.mmd (剧本级)
    """
    # 选路路径 (剧本级优先)
    if script_id:
        key = f'{book_id}_{script_id}'
    else:
        key = f'{book_id}'

    mm_path = MINDMAP_DIR / f'{key}.mmd'
    png_path = MINDMAP_DIR / f'{key}.png'
    if mm_path.exists() and not force:
        print(f'  ⏭️  mindmap {mm_path} 已存在')
        if not png_path.exists():
            mermaid_to_png(mm_path.read_text(encoding='utf-8'), png_path)
        return mm_path.read_text(encoding='utf-8')

    # 加载 chunks
    full_text = load_chunks_text(book_id)
    if not full_text:
        print(f'  ❌ book={book_id} 无 chunks')
        return None

    book_name = get_book_name(book_id)
    user_prompt = f'书籍内容:\n{full_text[:60000]}\n\n请生成 mermaid 思维导图源码。'

    print(f'  🧠 book={book_id} 生成 mindmap ...')
    if script_id:
        script_prompt = generate_script_mindmap_prompt(book_id, script_id)
        if not script_prompt:
            return None
        user_prompt = script_prompt
        print(f'    ↳ 剧本级 mindmap (script_id={script_id})')
    text, provider = llm.call(SYSTEM, user_prompt, max_tokens=4000)

    # 提取 mermaid 代码块
    mermaid = extract_mermaid(text)

    MINDMAP_DIR.mkdir(parents=True, exist_ok=True)
    mm_path.write_text(mermaid, encoding='utf-8')

    # 同步写 .json（结构化版本）
    structure = mermaid_to_structure(mermaid)
    (MINDMAP_DIR / f'{key}.json').write_text(
        json.dumps(structure, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    if save_pg:
        save_to_pg(book_id, mermaid, structure, provider, script_id=script_id)

    # 同步生成 PNG (主人 2026-08-22 要求: mindmap 必须是图)
    mermaid_to_png(mermaid, png_path)

    print(f'  ✅ mindmap: {mm_path} ({len(mermaid)} chars)')
    return mermaid


def generate_script_mindmap_prompt(book_id: int, script_id: int) -> Optional[str]:
    """从 game_scripts 里读剧本结构, 生成提示词 (剧本级 mindmap)"""
    from db import get_cursor
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(
            'SELECT book_id, script_json, total_scenes FROM game_scripts WHERE id = %s AND book_id = %s',
            (script_id, book_id),
        )
        row = cur.fetchone()
    if not row:
        print(f'    ❌ script_id={script_id} not found')
        return None

    script = row['script_json']
    scenes = script.get('scenes', [])
    scene_summaries = []
    for i, s in enumerate(scenes, 1):
        title = s.get('title', f'场景{i}')
        desc = s.get('description', '')[:80]
        player = s.get('player_role', '?')
        scene_summaries.append(f'场景{i} {title} ({player}): {desc}...')

    book_name = get_book_name(book_id)
    return f'''剧本: {book_name}
本剧本共 {len(scenes)} 个场景, 玩家角色与剧情进展如下:

{chr(10).join(scene_summaries)}

请生成 mermaid mindmap, 结构:
- 根: 剧本名
- 二级: 场景列表 (起承转合 4 个阶段, 每阶段包含几个 scene)
- 二级: 玩家角色轨迹 (身份/状态/代入点)
- 二级: 核心诡计 / 反转
- 二级: 主题/价值观
- 二级: 金句/名场面

不要有错别字, 中文输出, mermaid 格式从 mindmap 开头.'''


def generate_all_script_mindmaps(book_id: int, llm: LLMClient, force: bool = False) -> list:
    """为某本书的所有剧本生成 mindmap (主人 2026-08-22 要求: 每剧本一图)"""
    from db import get_cursor
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(
            'SELECT id FROM game_scripts WHERE book_id = %s ORDER BY chapter_index, id',
            (book_id,),
        )
        script_ids = [r['id'] for r in cur.fetchall()]

    print(f'  📜 book={book_id} 有 {len(script_ids)} 个剧本')
    results = []
    for sid in script_ids:
        mm = generate_mindmap(book_id, llm, force=force, script_id=sid)
        results.append({'script_id': sid, 'mindmap': mm})
    return results


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


def save_to_pg(book_id: int, mermaid: str, structure: dict, provider: str,
              script_id: Optional[int] = None, png_path: Optional[str] = None):
    """写 PG book_mindmaps 表

    主键: (book_id, script_id) 联合
    script_id=None -> 0 (书级汇总 mindmap)
    script_id=N    -> N (第 N 个剧本的 mindmap)
    """
    sid = script_id if script_id is not None else 0
    with get_cursor() as cur:
        cur.execute(
            '''INSERT INTO book_mindmaps (book_id, script_id, mermaid, structure, llm_model, png_path)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (book_id, script_id) DO UPDATE SET
                   mermaid = EXCLUDED.mermaid,
                   structure = EXCLUDED.structure,
                   llm_model = EXCLUDED.llm_model,
                   png_path = EXCLUDED.png_path,
                   updated_at = NOW()''',
            (book_id, sid, mermaid, json.dumps(structure, ensure_ascii=False), provider, png_path),
        )
