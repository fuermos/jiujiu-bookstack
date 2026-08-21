#!/usr/bin/env python3
"""generate_script.py - 步骤 5-6: 游戏化剧本生成 + TTS 预生成

输入: chunks + SKILL.md + mindmap
输出: game_scripts 表 + tts/*.mp3 音频
"""
import json
from pathlib import Path
from typing import Optional

from db import get_cursor
from llm_client import LLMClient

TTS_DIR = Path('tts')


SYSTEM = '''你是读书陪伴剧本杀设计师。任务：根据书的全文 + SKILL.md + 思维导图，生成多题型 v2.1 游戏化剧本。

核心要求:
- 这不是简单问答游戏，而是有故事弧度的读书陪伴
- 多题型（MC + OE + 角色扮演）
- 10-15 个场景（含 1-2 个分支点）
- 24-35 个问题
- 3 个 NPC 角色
- 起承转合叙事弧（起/承/转/合 完整分布）
- worldview_theme 标签
- 每个 question 必填 source_chunk_id

输出: 严格 JSON（不要 markdown 包装），开头 { 结尾 }'''


def generate_script_and_tts(book_id: int, llm: LLMClient, force: bool = False,
                            skill_path: Optional[str] = None,
                            mindmap_path: Optional[str] = None) -> Optional[int]:
    """生成剧本 + TTS，返回 script_id"""
    # 检测已有
    existing = get_existing_script(book_id)
    if existing and not force:
        print(f'  ⏭️  script {existing} 已存在')
        return existing

    book_name = get_book_name(book_id)
    full_text = load_chunks_text(book_id)

    # 注入 skill + mindmap
    skill_ref = ''
    sp = Path(skill_path) if skill_path else Path.home() / '.openclaw' / 'skill-archive' / 'books' / book_name.replace(' ', '-') / 'SKILL.md'
    if sp.exists():
        skill_text = sp.read_text(encoding='utf-8')[:3000]
        skill_ref = f'\n# 📋 SKILL.md（参考）\n```\n{skill_text}\n```\n'

    mindmap_ref = ''
    mp = Path(mindmap_path) if mindmap_path else Path(f'mindmaps/{book_id}.mmd')
    if mp.exists():
        mm_text = mp.read_text(encoding='utf-8')[:2000]
        mindmap_ref = f'\n# 🗺️ 思维导图（参考）\n```mermaid\n{mm_text}\n```\n'

    # 选 5-8 段关键原文（开头/高潮/结尾/转折）
    chunks_sample = load_chunks_sample(book_id, n=8)

    user_prompt = f'''# 任务
为《{book_name}》生成 v2.1 深度沉浸式剧本杀。

# 书本信息
- ID: {book_id}
{skill_ref}{mindmap_ref}

# 关键原文（5-8 段，按内容顺序）
{json.dumps(chunks_sample, ensure_ascii=False, indent=2)}

# 玩家画像
{{"age": 13, "特点": ["自我认同", "情绪波动", "拖延"], "阅读": "中上"}}

# 🚨 输出红线
1. 只输出 JSON, 不要 ``` 包装
2. 开头 {{ 结尾 }} 中间无文本
3. 场景 10-15 个 (含 1-2 分支点)
4. 总问题 24-35 个
5. MC 题 4 个选项 A B C D
6. OE 题必填 worldview_theme 标签
7. 每个问题必填真实 source_chunk_id

# v2.1 严格 schema
{{
  "version": "2.1",
  "book_id": {book_id},
  "narrative_arc": {{
    "起": "s1-s3 (铺垫: 身份/世界/初遇)",
    "承": "s4-s6 (深入: 细节/情感/连接)",
    "转": "s7-s10 (含 1-2 分支点, 价值观碰撞)",
    "合": "s11-s13 (升华 + 行动指引)"
  }},
  "scenes": [
    {{
      "id": "s1",
      "act": "起/承/转/合",
      "title": "中文标题",
      "description": "150-250 字场景描述",
      "narrator_intro": "30-50 字旁白",
      "questions": [
        {{
          "type": "comprehension_mc",
          "question": "中文",
          "options": ["A", "B", "C", "D"],
          "correct": "A",
          "explanation": "含原文引用",
          "source_chunk_id": 0,
          "difficulty": 2,
          "xp_reward": 10,
          "role_perspective": "主角"
        }},
        {{
          "type": "open_ended",
          "question": "中文",
          "evaluation_mode": "llm_grade",
          "evaluation_prompt": "5 维度评分 (深度/独特性/文本关联/真诚度/世界观), 先肯定再说建议",
          "source_chunk_id": 0,
          "worldview_theme": "主题标签"
        }}
      ]
    }}
  ]
}}

只输出 JSON。'''

    print(f'  🤖 book={book_id} 生成剧本 ...')
    raw_text, provider = llm.call(SYSTEM, user_prompt, max_tokens=16000, temperature=0.7)

    # 解析 + 重试
    script_json = parse_json_with_retry(raw_text, llm, user_prompt)
    if not script_json:
        return None

    # 写 PG
    script_id = save_to_pg(book_id, script_json, provider)
    print(f'  ✅ 剧本: {len(script_json["scenes"])} 场景')

    # TTS 预生成（每个场景的 narrator_intro）
    tts_count = generate_tts(script_json, TTS_DIR)
    print(f'  ✅ TTS: {tts_count} 个音频已生成')

    return script_id


def load_chunks_text(book_id: int) -> str:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT chunk_text FROM chunks WHERE book_id = %s ORDER BY id', (book_id,))
        return ''.join(r['chunk_text'] for r in cur.fetchall())


def load_chunks_sample(book_id: int, n: int = 8) -> list[str]:
    """均匀采样 n 个 chunk"""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT chunk_text FROM chunks WHERE book_id = %s ORDER BY id', (book_id,))
        chunks = [r['chunk_text'] for r in cur.fetchall()]
    if len(chunks) <= n:
        return chunks
    step = len(chunks) // n
    return [chunks[i * step][:500] for i in range(n)]


def get_book_name(book_id: int) -> str:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT name FROM books WHERE id = %s', (book_id,))
        return cur.fetchone()['name']


def get_existing_script(book_id: int) -> Optional[int]:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(
            'SELECT id FROM game_scripts WHERE book_id = %s AND game_type LIKE %s ORDER BY id DESC LIMIT 1',
            (book_id, 'v2\\_%'),
        )
        row = cur.fetchone()
        return row['id'] if row else None


def parse_json_with_retry(text: str, llm: LLMClient, user_prompt: str, max_retries: int = 2) -> Optional[dict]:
    """尝试解析 JSON，失败时让 LLM 重写"""
    import re
    for attempt in range(max_retries + 1):
        # 提取 {...} 范围
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if not m:
            print(f'  ⚠️  未找到 JSON, 重试 {attempt+1}/{max_retries}')
            text, _ = llm.call(SYSTEM, user_prompt + '\n\n⚠️ 上次输出非 JSON, 请严格只输出 JSON.', max_tokens=16000, temperature=0.3)
            continue
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            print(f'  ⚠️  JSON 解析失败: {e}, 重试 {attempt+1}/{max_retries}')
            text, _ = llm.call(SYSTEM, user_prompt + '\n\n⚠️ JSON 有语法错误, 请修复后重新输出.', max_tokens=16000, temperature=0.3)
    return None


def save_to_pg(book_id: int, script_json: dict, provider: str) -> int:
    script_hash = str(hash(json.dumps(script_json, sort_keys=True)))
    total_scenes = len(script_json.get('scenes', []))
    with get_cursor() as cur:
        cur.execute(
            '''INSERT INTO game_scripts (book_id, game_type, script_json, script_hash, total_scenes, status, provider)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id''',
            (book_id, 'v2_mixed', json.dumps(script_json, ensure_ascii=False), script_hash, total_scenes, 'ready', provider),
        )
        return cur.fetchone()['id']


def generate_tts(script_json: dict, tts_dir: Path) -> int:
    """为每个场景的 narrator_intro 生成 TTS 音频

    需要安装 edge-tts: pip install edge-tts
    """
    try:
        import edge_tts
    except ImportError:
        print('  ⚠️  edge-tts 未安装, 跳过 TTS 生成 (pip install edge-tts)')
        return 0

    import asyncio
    tts_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    async def gen_all():
        nonlocal count
        for scene in script_json.get('scenes', []):
            sid = scene.get('id', 'unknown')
            text = scene.get('narrator_intro', '').strip()
            if not text:
                continue
            audio_path = tts_dir / f'{sid}.mp3'
            if audio_path.exists():
                continue
            try:
                communicate = edge_tts.Communicate(text, voice='zh-CN-YunxiNeural')
                await communicate.save(str(audio_path))
                count += 1
            except Exception as e:
                print(f'  ⚠️  TTS {sid} 失败: {e}')

    asyncio.run(gen_all())
    return count
