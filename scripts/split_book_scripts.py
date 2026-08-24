#!/usr/bin/env python3
"""split_book_scripts.py - 把一本书按 chapter_index 拆成多个剧本

主人 2026-08-24 反馈:
- 一本书一个剧本不够, 一个 story 应该是一个剧本
- 福尔摩斯有 ~60 个真实故事 (5 部小说 + 56 短篇), 应该拆成多个剧本
- 粒度: 按 N 个连续 chapter 为一组 (默认 10)

用法:
    python3 scripts/split_book_scripts.py --book-id 24 --group-size 10
    python3 scripts/split_book_scripts.py --book-id 24 --dry-run
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config_loader import load_config
from db import init_pool, get_cursor
from llm_client import LLMClient
from generate_script import generate_script_and_tts


def get_real_chapter_ranges(book_id: int, min_chars: int = 100) -> list[int]:
    """拿这本书所有真实 (非空) chapter_index, 排序"""
    with get_cursor() as cur:
        cur.execute(
            """SELECT chapter_index FROM chunks
               WHERE book_id = %s AND char_count >= %s
               ORDER BY chapter_index""",
            (book_id, min_chars),
        )
        return [r['chapter_index'] for r in cur.fetchall()]


def split_into_groups(chs: list[int], group_size: int) -> list[tuple[int, int]]:
    """把 chapter 列表按 group_size 切, 返回 [(start, end)] 列表"""
    if not chs:
        return []
    groups = []
    for i in range(0, len(chs), group_size):
        start = chs[i]
        end_idx = min(i + group_size - 1, len(chs) - 1)
        end = chs[end_idx]
        groups.append((start, end))
    return groups


def get_existing_script_ranges(book_id: int) -> list[tuple[int, int]]:
    """拿这本书已生成的 (chapter_index, id) 列表 (用于跳过已有)"""
    with get_cursor() as cur:
        cur.execute(
            """SELECT chapter_index FROM game_scripts WHERE book_id = %s ORDER BY chapter_index""",
            (book_id,),
        )
        return [r['chapter_index'] for r in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser(description='把一本书拆成多个剧本')
    parser.add_argument('--book-id', type=int, required=True, help='书 ID')
    parser.add_argument('--group-size', type=int, default=10, help='每剧本覆盖多少个 chapter (默认 10)')
    parser.add_argument('--dry-run', action='store_true', help='只打印计划, 不真生成')
    parser.add_argument('--force', action='store_true', help='覆盖已有剧本')
    parser.add_argument('--min-chars', type=int, default=100, help='过滤掉 < N 字符的空 chunk (默认 100)')
    args = parser.parse_args()

    cfg = load_config('config/config.yaml')
    init_pool(cfg['database'])

    # 1. 拿真实 chapters
    chs = get_real_chapter_ranges(args.book_id, min_chars=args.min_chars)
    print(f'📚 book={args.book_id} 真实 chapter 数: {len(chs)} (ch={chs[0] if chs else "?"} ~ ch={chs[-1] if chs else "?"})')

    # 2. 切组
    groups = split_into_groups(chs, args.group_size)
    print(f'📂 按 group_size={args.group_size} 切组, 共 {len(groups)} 组:')
    for i, (s, e) in enumerate(groups, 1):
        print(f'   剧本 {i}: chapter {s} - {e}')

    # 3. 已有的 chapter_index (避免重跑)
    existing = get_existing_script_ranges(args.book_id)
    print(f'💾 已存在剧本 chapter_index: {existing}')

    # 4. 跑批
    if args.dry_run:
        print('🔍 dry-run 模式, 没真跑')
        return

    llm = LLMClient(cfg['llm'])
    n_ok = n_skip = n_fail = 0
    for i, (s, e) in enumerate(groups, 1):
        # 整组起点 chapter_index 没脚本时跑
        if s in existing and not args.force:
            print(f'⏭️  [{i}/{len(groups)}] ch={s}-{e} 已有, 跳过')
            n_skip += 1
            continue
        print(f'\\n🚀 [{i}/{len(groups)}] 生成 ch={s}-{e} 的剧本...')
        try:
            sid = generate_script_and_tts(
                args.book_id, llm, force=args.force,
                chapter_range=(s, e),
            )
            if sid:
                print(f'✅ script_id={sid}')
                n_ok += 1
            else:
                print(f'❌ 生成失败 (sid=None)')
                n_fail += 1
        except Exception as ex:
            print(f'❌ 异常: {ex}')
            n_fail += 1

    print(f'\\n🎉 完成! ✅ {n_ok} 成功, ⏭️ {n_skip} 跳过, ❌ {n_fail} 失败')


if __name__ == '__main__':
    main()