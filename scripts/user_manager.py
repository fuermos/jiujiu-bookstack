#!/usr/bin/env python3
"""user_manager.py - 用户注册登录 + 剧本使用记录

主人 2026-08-22 钦定: 加用户隔离模块
- 邮箱注册 + 简单验证 (bcrypt 哈希 + 邮箱格式)
- 剧本进度恢复 (script_play_records)

主人 2026-08-24 反馈: 刷新页面就要重登录。
- 加 token 机制: 登录成功后生成 token, 写入 query_params
- 页面初始化时从 query_params 读 token -> 还原 session
- token 文件落 data/auth_tokens.json (手动失效机制)
"""
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from typing import Optional

from db import get_cursor

# token 存储位置 (与 data/books.db 同级, 不上传 git)
TOKEN_FILE = Path(__file__).parent.parent / 'data' / 'auth_tokens.json'
TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 天有效期

EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# 用 hashlib + salt 代替 bcrypt (避免额外依赖)
def hash_password(password: str, salt: Optional[str] = None) -> str:
    if not salt:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f'{salt}${h.hex()}'

def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, _ = password_hash.split('$', 1)
        return hash_password(password, salt) == password_hash
    except Exception:
        return False


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email))


# ============== 用户 CRUD ==============

def register_user(email: str, password: str, nickname: str = '') -> Optional[dict]:
    """注册新用户. 邮箱已存在返回 None."""
    email = email.strip().lower()
    if not is_valid_email(email):
        return {'error': '邮箱格式不对'}
    if len(password) < 6:
        return {'error': '密码至少 6 位'}
    pwhash = hash_password(password)
    nickname = nickname or email.split('@')[0]
    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute(
                'INSERT INTO users (email, password_hash, nickname) VALUES (%s, %s, %s) RETURNING id, email, nickname',
                (email, pwhash, nickname),
            )
            row = cur.fetchone()
            print(f'  ✅ 注册成功: {email} (id={row["id"]})')
            return row
    except Exception as e:
        if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
            return {'error': '邮箱已注册'}
        return {'error': str(e)}


def login_user(email: str, password: str) -> Optional[dict]:
    email = email.strip().lower()
    with get_cursor(dict_cursor=True) as cur:
        cur.execute(
            'SELECT id, email, nickname, password_hash FROM users WHERE email = %s',
            (email,),
        )
        row = cur.fetchone()
    if not row:
        return {'error': '邮箱未注册'}
    if not verify_password(password, row['password_hash']):
        return {'error': '密码错'}
    # 更新 last_login
    with get_cursor() as cur:
        cur.execute('UPDATE users SET last_login = NOW() WHERE id = %s', (row['id'],))
    return {'id': row['id'], 'email': row['email'], 'nickname': row['nickname']}


def get_user(user_id: int) -> Optional[dict]:
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('SELECT id, email, nickname FROM users WHERE id = %s', (user_id,))
        return cur.fetchone()


# ============== Token 机制 (2026-08-24 主人反馈加) ==============

def _load_tokens() -> dict:
    """读 token 表 {token: {user_id, created_at, expires_at}}"""
    if not TOKEN_FILE.exists():
        return {}
    try:
        with TOKEN_FILE.open(encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_tokens(tokens: dict):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TOKEN_FILE.open('w', encoding='utf-8') as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)


def _cleanup_expired(tokens: dict) -> dict:
    """过期的 token 清理掉"""
    now = time.time()
    return {t: v for t, v in tokens.items() if v.get('expires_at', 0) > now}


def issue_token(user_id: int) -> str:
    """生成 token, 存入文件, 返回 token 字符串"""
    token = secrets.token_urlsafe(32)
    now = time.time()
    tokens = _cleanup_expired(_load_tokens())
    tokens[token] = {
        'user_id': user_id,
        'created_at': now,
        'expires_at': now + TOKEN_TTL_SECONDS,
    }
    _save_tokens(tokens)
    return token


def resolve_token(token: str) -> Optional[dict]:
    """通过 token 拿 user 信息, 过期/不存在返回 None"""
    if not token:
        return None
    tokens = _cleanup_expired(_load_tokens())
    info = tokens.get(token)
    if not info:
        return None
    return get_user(info['user_id'])


def revoke_token(token: str):
    """退出登录时调用"""
    tokens = _load_tokens()
    tokens.pop(token, None)
    _save_tokens(tokens)


# ============== 剧本进度记录 ==============

def save_progress(user_id: int, book_id: int, script_id: int,
                  player_role: str, scene_idx: int,
                  game_history: list, world_state: dict,
                  total_score: float, status: str = 'playing') -> bool:
    """保存或更新玩家进度 (upsert by user_id+script_id)"""
    import json
    with get_cursor() as cur:
        cur.execute('''
            INSERT INTO script_play_records
              (user_id, book_id, script_id, player_role, current_scene_idx,
               game_history, world_state, total_score, status, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, script_id) DO UPDATE SET
              current_scene_idx = EXCLUDED.current_scene_idx,
              player_role = EXCLUDED.player_role,
              game_history = EXCLUDED.game_history,
              world_state = EXCLUDED.world_state,
              total_score = EXCLUDED.total_score,
              status = EXCLUDED.status,
              updated_at = NOW()
        ''', (user_id, book_id, script_id, player_role, scene_idx,
              json.dumps(game_history, ensure_ascii=False),
              json.dumps(world_state, ensure_ascii=False),
              total_score, status))
    return True


def load_progress(user_id: int, book_id: int, script_id: int) -> Optional[dict]:
    """加载玩家上次的进度, 没有返回 None"""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('''
            SELECT current_scene_idx, player_role, game_history, world_state,
                   total_score, status, started_at, updated_at
            FROM script_play_records
            WHERE user_id = %s AND book_id = %s AND script_id = %s
        ''', (user_id, book_id, script_id))
        return cur.fetchone()


def list_user_history(user_id: int, limit: int = 50) -> list:
    """列出用户玩过的所有剧本 (历史 + 当前进度)"""
    with get_cursor(dict_cursor=True) as cur:
        cur.execute('''
            SELECT r.id, r.book_id, r.script_id, r.player_role,
                   r.current_scene_idx, r.total_score, r.status,
                   r.started_at, r.updated_at,
                   b.name AS book_name, b.category
            FROM script_play_records r
            JOIN books b ON b.id = r.book_id
            WHERE r.user_id = %s
            ORDER BY r.updated_at DESC
            LIMIT %s
        ''', (user_id, limit))
        return cur.fetchall()


def delete_progress(user_id: int, script_id: int) -> bool:
    """删除一条记录 (重玩时清空进度)"""
    with get_cursor() as cur:
        cur.execute(
            'DELETE FROM script_play_records WHERE user_id = %s AND id = %s',
            (user_id, script_id),
        )
        return cur.rowcount > 0