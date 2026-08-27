#!/usr/bin/env python3
"""generate_script.py - 步骤 5-6: 游戏化剧本生成 + TTS 预生成

输入: chunks + SKILL.md + mindmap
输出: game_scripts 表 + tts/*.mp3 音频
"""
import sys
import json
from pathlib import Path
from typing import Optional
from datetime import datetime

ROOT = Path(__file__).parent.parent  # 铲屎官 2026-08-25 补: 之前改路径引入 ROOT 未定义 bug
sys.path.insert(0, str(ROOT / 'scripts'))

from db import get_cursor
from llm_client import LLMClient


def log(msg, level='INFO'):
    """简单日志（2026-08-24 补）— 补全 enrich 脚本里需要的 log() 调用"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] [{level}] {msg}', flush=True)

TTS_DIR = Path('tts')


SYSTEM = '''你是顶级沉浸式剧本杀设计师。生成多题型 v2.3 第一人称剧情驱动剧本。

【硬约束 - 必须严格遵守】
1. description 第一人称，必须以"你"开头，含感官细节（看/听/嗅/触/味至少 3 种）
2. 每个场景含 player_role 字段（**必须严格使用 characters.available_roles 里的 role_id，禁止自创人物名**）
3. 每个场景含 world_state 字段（4 键值对，用本书语境的术语，不要套用任何特定书的固定名）
4. choice 题必含 consequences 字段（4 字符串数组，每个 30-50 字剧情后果）
5. options 字段不加"A. "前缀（UI 自动加）

【v3.0 角色系统 — 严禁违反】（2026-08-24 主人反馈 bug fix）
- 🚨 **严禁** 在任何字段使用其他书的 IP 角色（华生/福尔摩斯/莫斯坦/斯莫尔/巴塞洛缪/哈利/赫敏/柯南/毛利/江户川 等）
- 🚨 **严禁** 硬编码"默认主角=华生医生/福尔摩斯" 等 — 主角名必须是 characters.available_roles 里的 role_id
- characters.available_roles 必须填满 3 个角色：角色1 是"现代读者·笑笑"固定 + 角色2/3 从本书人物/视角推导
- 每个 question 的 role_perspective 必须是 characters.available_roles 里的某个 role_id
- 每个 scene 的 player_role 必须是 characters.available_roles 里的某个 role_id
- scene.description 里"你是 XXX" 的 XXX 必须是 characters.available_roles 里的 role_name

【剧情设计原则】
- 玩家是亲历者不是旁观者，描述要像电影开场
- choice 选项是剧情动作（勇敢/谨慎/求助/后退），每个有不同剧情后果
- 起承转合完整分布，13-15 个场景
- 角色命名必须严格基于本书的人物和语境，不能套任何外部 IP

【题型配比】32 题左右
- choice 40% / comprehension_mc 25% / open_ended 25% / inference_mc 10%

【输出】严格 JSON，开头 { 结尾 }，不要 markdown 包装。'''


def generate_script_and_tts(book_id: int, llm: LLMClient, force: bool = False,
                            skill_path: Optional[str] = None,
                            mindmap_path: Optional[str] = None,
                            chapter_range: Optional[tuple[int, int]] = None) -> Optional[int]:
    """生成剧本 + TTS，返回 script_id

    Args:
        chapter_range: (start, end) 仅生成该 chapter_index 范围的剧本
                       例: (0, 9) 生成第 0-9 章 (一个 story).
                       用途: 一本书多个剧本 (2026-08-24 主人反馈)
    """
    # 检测已有 - chapter_range 指定时跳过重复检测 (因为要生成多个)
    if not chapter_range:
        existing = get_existing_script(book_id)
        if existing and not force:
            print(f'  ⏭️  script {existing} 已存在')
            return existing

    book_name = get_book_name(book_id)
    full_text = load_chunks_text(book_id, chapter_range=chapter_range)

    # ★ v3.0 (2026-08-24): 加载 book_meta (name + category) 给 enrich 用，避免跨书 IP 污染
    from db import get_cursor
    book_meta = {'name': book_name, 'category':''}
    try:
        with get_cursor() as cur:
            cur.execute('SELECT category FROM books WHERE id = %s', (book_id,))
            row = cur.fetchone()
            if row:
                book_meta['category'] = row['category'] or ''
    except Exception as e:
        print(f'  ⚠️ book_meta 加载失败: {e}')

    # 注入 skill + mindmap
    # 铲屎官 2026-08-25 钩定: 优先项目内路径, 最后兜底 ~/.openclaw/skill-archive/
    skill_ref = ''
    if skill_path:
        sp = Path(skill_path)
    else:
        # 项目内路径优先 (skills/ → data/), 最后兜底全局
        sp = (ROOT / 'skills' / f'book_{book_id}_SKILL.md')
        if not sp.exists():
            sp = (ROOT / 'data' / f'{book_id}_SKILL.md')
        if not sp.exists():
            sp = Path.home() / '.openclaw' / 'skill-archive' / 'books' / book_name.replace(' ', '-') / 'SKILL.md'
    if sp.exists():
        skill_text = sp.read_text(encoding='utf-8')[:3000]
        skill_ref = f'\n# 📋 SKILL.md（参考）\n```\n{skill_text}\n```\n'

    mindmap_ref = ''
    if mindmap_path:
        mp = Path(mindmap_path)
    else:
        mp = (ROOT / 'mindmaps' / f'{book_id}.mmd')
    if mp.exists():
        mm_text = mp.read_text(encoding='utf-8')[:2000]
        mindmap_ref = f'\n# 🗺️ 思维导图（参考）\n```mermaid\n{mm_text}\n```\n'

    # 选 5-8 段关键原文（开头/高潮/结尾/转折）
    chunks_sample = load_chunks_sample(book_id, n=8, chapter_range=chapter_range)

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
    script_json = enrich_script_for_immersive(script_json, book_meta=book_meta)

    # 写 PG - chapter_range 指定时设对应的 chapter_index, 让多个剧本可区分
    ci = chapter_range[0] if chapter_range else 0
    # chapter_range 指定时检查是否已存在
    if chapter_range and not force:
        existing = get_existing_script(book_id, chapter_index=ci)
        if existing:
            print(f'  ⏭️  chapter_index={ci} 已存在 script_id={existing}, 跳过')
            return existing
    script_id = save_to_pg(book_id, script_json, provider, chapter_index=ci)
    print(f'  ✅ 剧本 (chapter_index={ci}): {len(script_json["scenes"])} 场景')

    # TTS 预生成（每个场景的 narrator_intro）
    tts_count = generate_tts(script_json, TTS_DIR)
    print(f'  ✅ TTS: {tts_count} 个音频已生成')

    return script_id


def load_chunks_text(book_id: int, chapter_range: Optional[tuple[int, int]] = None) -> str:
    """读 book 的 chunks 文本拼接

    Args:
        chapter_range: (start, end) 仅取 chapter_index 在范围内的 chunk
    """
    with get_cursor(dict_cursor=True) as cur:
        if chapter_range:
            cur.execute(
                'SELECT chunk_text FROM chunks WHERE book_id = %s AND chapter_index >= %s AND chapter_index <= %s ORDER BY chapter_index, id',
                (book_id, chapter_range[0], chapter_range[1]),
            )
        else:
            cur.execute('SELECT chunk_text FROM chunks WHERE book_id = %s ORDER BY id', (book_id,))
        return ''.join(r['chunk_text'] for r in cur.fetchall())


def load_chunks_sample(book_id: int, n: int = 8, chapter_range: Optional[tuple[int, int]] = None) -> list[str]:
    """均匀采样 n 个 chunk (2026-08-24 加 chapter_range 支持)"""
    with get_cursor(dict_cursor=True) as cur:
        if chapter_range:
            cur.execute(
                'SELECT chunk_text FROM chunks WHERE book_id = %s AND chapter_index >= %s AND chapter_index <= %s ORDER BY chapter_index, id',
                (book_id, chapter_range[0], chapter_range[1]),
            )
        else:
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


def get_existing_script(book_id: int, chapter_index: Optional[int] = None) -> Optional[int]:
    """拿这本书已存在的脚本 id

    Args:
        chapter_index: 指定时只查该 chapter_index 的 (福尔摩斯多剧本场景)
    """
    with get_cursor(dict_cursor=True) as cur:
        if chapter_index is not None:
            cur.execute(
                'SELECT id FROM game_scripts WHERE book_id = %s AND chapter_index = %s ORDER BY id DESC LIMIT 1',
                (book_id, chapter_index),
            )
        else:
            cur.execute(
                'SELECT id FROM game_scripts WHERE book_id = %s AND game_type LIKE %s ORDER BY id DESC LIMIT 1',
                (book_id, 'v2\\_%'),
            )
        row = cur.fetchone()
        return row['id'] if row else None


# ★ v3.0 (2026-08-24 主人反馈 bug fix):
# jiujiu-bookstack 之前硬编码福尔摩斯人物 (华生/福尔摩斯/斯莫尔/莫斯坦/巴塞洛缪)，
# 导致所有书的 player_role 都被污染。改为从 book_meta + characters.available_roles 推导。
FOREIGN_CHARS = {'华生', '福尔摩斯', '莫斯坦', '巴塞洛缪', '斯莫尔', '华生医生',
                 '哈利', '赫敏', '罗恩', '霍格沃茨', '邓布利多', '江户川', '柯南', '毛利',
                 '工藤新一'}


def _derive_roles_from_book(book_meta: dict) -> list[str]:
    """根据书名/分类推导专属 3 角色 ID（v3.0 fallback）"""
    if not book_meta:
        return ['主角', 'NPC', '读者']
    name = book_meta.get('name') or ''
    category = book_meta.get('category') or ''

    # 福尔摩斯 → Holmes characters 是合法的
    if '福尔摩斯' in name or 'holmes' in name.lower():
        return ['华生', '福尔摩斯', '莫斯坦']
    # 哈利波特
    if '哈利' in name or 'harry' in name.lower():
        return ['哈利', '赫敏', '罗恩']
    # 柯南
    if '柯南' in name or 'detective conan' in name.lower():
        return ['柯南', '毛利', '灰原']
    # 数学 / 概率 / 统计
    if any(kw in name for kw in ['数学', '概率', '统计', '算法']):
        return ['思考者', '探索者', '解谜人']
    # 心理学 / 成长
    if '心理' in category or '成长' in category:
        return ['探索者', '倾听者', '思考者']
    # 历史
    if '历史' in category:
        return ['小史官', '见证者', '记录者']
    # 文学 / 散文
    if '文学' in category or '散文' in category:
        return ['读书人', '小书童', '汪老']
    # 默认安全标签
    return ['主角', 'NPC', '读者']


def enrich_script_for_immersive(script_json: dict, book_meta: dict = None) -> dict:
    """后处理 + v3.0 角色适配（2026-08-24 主人反馈 bug fix）

    原版硬编码华生/福尔摩斯等跨书角色，导致非福尔摩斯书也被污染。
    现在改为：① LLM 写的 characters.available_roles 为权威
             ② 缺失时从 book_meta 推导
             ③ 兜底用安全标签

    铲屎官 2026-08-25 补: 防御 LLM 返回异常 JSON (不是 dict 或缺 scenes 字段)
    """
    # 防御: LLM 可能返回 list/str/缺字段
    if not isinstance(script_json, dict):
        log(f'⚠️ enrich 收到非 dict 输入 ({type(script_json).__name__}), 转为空剧本')
        script_json = {'scenes': []}
    scenes = script_json.get('scenes') or []
    if not isinstance(scenes, list):
        log(f'⚠️ scenes 不是 list ({type(scenes).__name__}), 转为空列表')
        scenes = []
    script_json['scenes'] = scenes

    # ★ v3.0: 优先读 LLM 写的 characters.available_roles
    characters = script_json.get('characters', {})
    available_roles = characters.get('available_roles', []) if isinstance(characters, dict) else []
    available_role_ids = [r.get('role_id', '').strip() for r in available_roles if r.get('role_id', '').strip()]
    # 过滤掉 LLM 写的跨书 IP 角色（如华生出现在数学书里）
    available_role_ids = [r for r in available_role_ids if r not in FOREIGN_CHARS]

    # 如果 LLM 没填或全被过滤，从 book_meta 推导
    if not available_role_ids:
        available_role_ids = _derive_roles_from_book(book_meta)
        log(f'🎭 v3.0 LLM 未填 characters.available_roles，按书名[{book_meta.get("name") if book_meta else "?"}] 推导: {available_role_ids}')

    primary_role = available_role_ids[0]

    for i, s in enumerate(scenes):
        # 1. description 转第一人称
        desc = s.get('description', '').strip()
        if desc and not desc.startswith(('你', '你')):
            s['description'] = '你' + desc[0] + '。' + desc[1:] if desc[0] not in '。，！？、' else '你' + desc
        # 2. player_role (v3.0: 不再硬编码 Holmes)
        pr = str(s.get('player_role') or '').strip()
        if not pr or pr in FOREIGN_CHARS:
            s['player_role'] = primary_role
        # 3. world_state (v3.0: 不再硬编码"华生状态")
        if not s.get('world_state') or s.get('world_state') == {}:
            act = s.get('act', '')
            danger = {'起': '🟢', '承': '🟡', '转': '🔴', '合': '⚪'}.get(act, '🟢')
            # 找 available_roles[0] 对应的 role_name 作状态标签
            role_label = primary_role
            s['world_state'] = {
                f'{role_label}状态': '专注',
                '进度': f'线索 {min(i+1, 12)}/12',
                '危险等级': danger,
                '道德记录': [],
            }
    # 2.5 按 act 自动分配角色视角 (v3.0: 用 available_role_ids 循环，不用 Holmes)
    for s in scenes:
        for qi, q in enumerate(s.get('questions', [])):
            if q.get('type') in ('comprehension_mc', 'open_ended', 'inference_mc'):
                rp = str(q.get('role_perspective') or '').strip()
                # 替换跨书 IP 角色
                if rp in FOREIGN_CHARS or not rp:
                    q['role_perspective'] = available_role_ids[qi % len(available_role_ids)]

    # 抽出所有可用 NPC (v3.0: 从 available_role_ids 来，不再是 Holmes)
    script_json['_available_npcs'] = list(available_role_ids)
    script_json['_player_role_options'] = list(available_role_ids) + ['读者 (上帝视角)']

    # 4. choice 题 consequences 兜底
    for s in scenes:
        for q in s.get('questions', []):
            if q.get('type') == 'choice':
                cons = q.get('consequences')
                if not cons or (isinstance(cons, list) and len(cons) != 4):
                    opts = q.get('options', [])
                    cons_list = []
                    for o in opts[:4]:
                        o_clean = str(o).strip()
                        if o_clean[:2] in ('A.', 'B.', 'C.', 'D.') and o_clean[1:3].strip() == '.':
                            o_clean = o_clean[3:].strip()
                        cons_list.append(f"你选择了【{o_clean}】，剧情继续推进...")
                    q['consequences'] = cons_list
                elif isinstance(cons, str):
                    q['consequences'] = [cons] + [f"你选择了另一个选项，剧情继续推进..."] * 3
                opts = q.get('options', [])
                q['options'] = [
                    (o[3:].strip() if o[:2] in ('A.', 'B.', 'C.', 'D.', 'E.') and o[1:3].strip() == '.' else o)
                    for o in opts
                ]
            elif q.get('type') in ('comprehension_mc', 'inference_mc'):
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
    # 从 user_prompt 提取 book_id (铲屎官 2026-08-25 修复: prompt 里实际是 "- ID: {book_id}", 之前 regex 永远匹配不到)
    import re
    m = re.search(r'-\s*ID[\":\s]+(\d+)', user_prompt)
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


def save_to_pg(book_id: int, script_json: dict, provider: str, chapter_index: int = 0) -> int:
    script_hash = str(hash(json.dumps(script_json, sort_keys=True)))
    total_scenes = len(script_json.get('scenes', []))
    # 铲屎官 2026-08-25 钩定: ON CONFLICT DO UPDATE, force 重跑不报 UniqueViolation
    with get_cursor() as cur:
        cur.execute(
            '''INSERT INTO game_scripts (book_id, game_type, chapter_index, script_json, script_hash, total_scenes, status, provider)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (book_id, chapter_index, game_type) DO UPDATE
               SET script_json=EXCLUDED.script_json,
                   script_hash=EXCLUDED.script_hash,
                   total_scenes=EXCLUDED.total_scenes,
                   status=EXCLUDED.status,
                   provider=EXCLUDED.provider,
                   updated_at=now()
               RETURNING id''',
            (book_id, 'v2_mixed', chapter_index, json.dumps(script_json, ensure_ascii=False), script_hash, total_scenes, 'ready', provider),
        )
        script_id = cur.fetchone()['id']
        # 同步 books.total_scenes 避免 web UI 显示 0 (2026-08-25 补: 避免手动 UPDATE)
        # 合集书有多剧本时: SUM 所有剧本的 scenes
        cur.execute(
            'UPDATE books SET total_scenes = '
            '(SELECT COALESCE(SUM(total_scenes), 0) FROM game_scripts WHERE book_id = %s), '
            'game_type = %s '
            'WHERE id = %s',
            (book_id, 'v2_mixed', book_id),
        )
    return script_id


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

    async def gen_one(text: str, audio_path: Path) -> bool:
        """单场景生成，带重试（2026-08-24 补：edge-tts 偶发 'No audio was received'）"""
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(text, voice='zh-CN-YunxiNeural')
                await asyncio.to_thread(communicate.save_sync if hasattr(communicate, 'save_sync') else None) if False else await communicate.save(str(audio_path))
                # 校验非空且大小合理（>1KB）
                if audio_path.exists() and audio_path.stat().st_size > 1024:
                    return True
                log(f'  ⚠️  TTS {audio_path.stem} 第{attempt+1}次输出异常({audio_path.stat().st_size if audio_path.exists() else 0}B), 重试...')
                if audio_path.exists():
                    audio_path.unlink()
            except Exception as e:
                log(f'  ⚠️  TTS {audio_path.stem} 第{attempt+1}次失败: {e}')
                if audio_path.exists():
                    audio_path.unlink()  # 删空文件防下次跳过
                await asyncio.sleep(1.5 * (attempt + 1))
        return False

    async def gen_all():
        nonlocal count
        for scene in script_json.get('scenes', []):
            sid = scene.get('id', 'unknown')
            text = scene.get('narrator_intro', '').strip()
            if not text:
                continue
            audio_path = tts_dir / f'{sid}.mp3'
            if audio_path.exists() and audio_path.stat().st_size > 1024:
                continue
            if await gen_one(text, audio_path):
                count += 1
            else:
                log(f'  ❌ TTS {sid} 3 次重试全败, 跳过')

    asyncio.run(gen_all())
    return count
