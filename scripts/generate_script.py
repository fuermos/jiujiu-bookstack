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


SYSTEM = '''你是顶级沉浸式剧本杀设计师。生成多题型 v2.3 第一人称剧情驱动剧本。

【硬约束 - 必须严格遵守】
1. description 第一人称，必须以"你"开头，含感官细节（看/听/嗅/触/味至少 3 种）
2. 每个场景含 player_role 字段（默认"华生医生"）
3. 每个场景含 world_state 字段（4 键值对：华生状态/案件进度/危险等级/道德记录）
4. choice 题必含 consequences 字段（4 字符串数组，每个 30-50 字剧情后果）
5. options 字段不加"A. "前缀（UI 自动加）

【剧情设计原则】
- 玩家是亲历者不是旁观者，描述要像电影开场
- choice 选项是剧情动作（勇敢/谨慎/求助/后退），每个有不同剧情后果
- 起承转合完整分布，13-15 个场景

【题型配比】32 题左右
- choice 40% / comprehension_mc 25% / open_ended 25% / inference_mc 10%

【输出】严格 JSON，开头 { 结尾 }，不要 markdown 包装。'''


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

    # 后处理：补齐第一人称/player_role/world_state/consequences
    script_json = enrich_script_for_immersive(script_json)

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


def enrich_script_for_immersive(script_json: dict) -> dict:
    """后处理：把 LLM 没听话生成的字段补齐

    LLM 经常偷懒不写第一人称 / player_role / world_state / consequences，
    我们兜底填上，保证 web 端有剧情化体验。
    """
    scenes = script_json.get('scenes', [])
    for i, s in enumerate(scenes):
        # 1. description 转第一人称：如果不以"你"开头，加"你"
        desc = s.get('description', '').strip()
        if desc and not desc.startswith(('你', '你')):
            # 加第一句"你..."做代入
            s['description'] = '你' + desc[0] + '。' + desc[1:] if desc[0] not in '。，！？、' else '你' + desc
        elif desc and desc.startswith('你'):
            pass  # 已经是第一人称
        # 2. player_role
        if not s.get('player_role'):
            s['player_role'] = '华生医生' if i < len(scenes) * 0.7 else '福尔摩斯'
        # 3. world_state
        if not s.get('world_state') or s.get('world_state') == {}:
            act = s.get('act', '')
            danger = {'起': '🟢', '承': '🟡', '转': '🔴', '合': '⚪'}.get(act, '🟢')
            s['world_state'] = {
                '华生状态': '警觉',
                '案件进度': f'线索 {min(i+1, 12)}/12',
                '危险等级': danger,
                '道德记录': [],
            }
        # 2.5 按 act 自动分配角色视角 (替代 LLM 只写华生)
        # 起: 华生; 承: 莫斯坦小姐; 转: 福尔摩斯/斯莫尔; 合: 任意
        act_role_map = {
            '起': ['华生', '莫斯坦小姐'],
            '承': ['莫斯坦小姐', '华生', '福尔摩斯'],
            '转': ['福尔摩斯', '斯莫尔', '华生', '巴塞洛缪'],
            '合': ['华生', '福尔摩斯', '莫斯坦小姐'],
        }
        for s in scenes:
            act = s.get('act', '起')
            roles_for_act = act_role_map.get(act, ['华生'])
            for qi, q in enumerate(s.get('questions', [])):
                if q.get('type') in ('comprehension_mc', 'open_ended', 'inference_mc'):
                    # 每题分配不同角色 (循环使用)
                    q['role_perspective'] = roles_for_act[qi % len(roles_for_act)]
        # 抽出所有可用 NPC 给 web UI (玩家可自选)
        npcs = set()
        for s in scenes:
            act = s.get('act', '起')
            npcs.update(act_role_map.get(act, ['华生']))
        script_json['_available_npcs'] = sorted(npcs)
        script_json['_player_role_options'] = sorted(npcs) + ['读者 (上帝视角)']
        # 4. choice 题 consequences 兜底
        for q in s.get('questions', []):
            if q.get('type') == 'choice':
                cons = q.get('consequences')
                if not cons or (isinstance(cons, list) and len(cons) != 4):
                    opts = q.get('options', [])
                    cons_list = []
                    for o in opts[:4]:
                        o_clean = str(o).strip()
                        # 去掉前缀 A. B. C. D.
                        if o_clean[:2] in ('A.', 'B.', 'C.', 'D.') and o_clean[1:3].strip() == '.':
                            o_clean = o_clean[3:].strip()
                        cons_list.append(f"你选择了【{o_clean}】，剧情继续推进...")
                    q['consequences'] = cons_list
                elif isinstance(cons, str):
                    q['consequences'] = [cons] + [f"你选择了另一个选项，剧情继续推进..."] * 3
                # 5. options 去掉 "A. " 前缀（防重复）
                opts = q.get('options', [])
                q['options'] = [
                    (o[3:].strip() if o[:2] in ('A.', 'B.', 'C.', 'D.', 'E.') and o[1:3].strip() == '.' else o)
                    for o in opts
                ]
            elif q.get('type') in ('comprehension_mc', 'inference_mc'):
                # 同样去掉 options 前缀
                opts = q.get('options', [])
                q['options'] = [
                    (o[3:].strip() if o[:2] in ('A.', 'B.', 'C.', 'D.', 'E.') and o[1:3].strip() == '.' else o)
                    for o in opts
                ]
    return script_json


def _try_repair_json(text: str) -> Optional[dict]:
    """尝试多种修复策略解析 JSON"""
    import re
    # 策略1：提取 {...}
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # 策略2：尝试 json_repair 库
    try:
        import json_repair
        repaired = json_repair.loads(text)
        if repaired:
            return repaired if isinstance(repaired, dict) else None
    except ImportError:
        pass
    except Exception:
        pass
    # 策略3：手动修复常见错误（缺逗号）
    if m:
        s = m.group(0)
        # 在 "} { 之间、" " { 之间、} " 之间插入逗号
        s = re.sub(r'\}\s*\{', '}, {', s)
        s = re.sub(r'"\s*\{', '", {', s)
        s = re.sub(r'\}\s*"', '}, "', s)
        s = re.sub(r'"\s*"', '", "', s)
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    return None


def parse_json_with_retry(text: str, llm: LLMClient, user_prompt: str, max_retries: int = 1) -> Optional[dict]:
    """尝试解析 JSON，失败时让 LLM 重写（最多 1 次），最后兜底生成骨架"""
    import re
    for attempt in range(max_retries + 1):
        result = _try_repair_json(text)
        if result:
            return result
        print(f'  ⚠️  JSON 解析失败, 重试 {attempt+1}/{max_retries}')
        text, _ = llm.call(SYSTEM, user_prompt + '\n\n⚠️ JSON 有语法错误, 务必只输出合法 JSON. 每个字段后必须有逗号. 字段值用双引号.', max_tokens=16000, temperature=0.2)
    # 最后兜底：从 chunks 骨架生成
    print('  ⚠️  多次解析失败, 使用本地骨架 fallback')
    return _fallback_skeleton(text, llm, user_prompt)


def _fallback_skeleton(text: str, llm: LLMClient, user_prompt: str) -> dict:
    """LLM 多次失败时，从原文 8 个 chunks 生成简化骨架剧本"""
    # 从 user_prompt 提取 book_id
    import re
    m = re.search(r'book_id[\":\s]+(\d+)', user_prompt)
    book_id = int(m.group(1)) if m else 0
    if not book_id:
        return {"scenes": []}
    from db import get_cursor
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT name FROM books WHERE id = %s', (book_id,))
        book_name = cur.fetchone()['name']
        cur.execute('SELECT chunk_text FROM chunks WHERE book_id = %s ORDER BY id LIMIT 8', (book_id,))
        chunks = [r['chunk_text'][:300] for r in cur.fetchall()]
    # 8 个场景的骨架
    scenes = []
    for i, c in enumerate(chunks):
        scenes.append({
            'id': f's{i+1}',
            'act': ['起', '起', '起', '承', '承', '承', '转', '合'][i],
            'title': f'场景 {i+1}',
            'description': f'你翻开《{book_name}》的第{i+1}页，{c[:80]}...',
            'narrator_intro': f'你正在阅读《{book_name}》。',
            'player_role': '华生医生',
            'world_state': {
                '华生状态': '好奇',
                '案件进度': f'线索 {i+1}/8',
                '危险等级': '🟢',
                '道德记录': [],
            },
            'questions': [
                {
                    'type': 'choice',
                    'question': '面对这段内容，你会怎么做？',
                    'options': ['仔细阅读', '跳到下一段', '做笔记', '与同伴讨论'],
                    'consequences': [f'你选择了【仔细阅读】，{c[20:60]}...'] * 4,
                    'branch_to': f's{i+2}' if i < 7 else None,
                    'source_chunk_id': 0,
                    'worldview_theme': '阅读陪伴',
                },
            ],
        })
    return {'version': '2.3-fallback', 'book_id': book_id, 'scenes': scenes}


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
