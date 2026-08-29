#!/usr/bin/env python3
"""pipeline.py - 主入口：完整 8 步流水线

用法:
    python pipeline.py books/                        # 处理整个目录
    python pipeline.py books/my_book.epub           # 处理单本
    python pipeline.py --book-id 384 --force        # 单本强制重生成
    python pipeline.py books/ --steps summary       # 只跑 summary 步骤
"""
import argparse
import sys
from pathlib import Path

from llm_client import LLMClient
from text_sanitize import sanitize
from config_loader import load_config


def main():
    parser = argparse.ArgumentParser(description='jiujiu-bookstack 主流水线')
    parser.add_argument('books_dir', nargs='?', help='epub 文件或目录')
    parser.add_argument('--book-id', type=int, help='指定书 ID')
    parser.add_argument('--force', action='store_true', help='强制重生成')
    parser.add_argument('--steps', help='只跑指定步骤（逗号分隔）')
    parser.add_argument('--concurrency', type=int, default=1, help='并发数')
    parser.add_argument('--config', default='config/config.yaml', help='配置文件路径')
    args = parser.parse_args()

    config = load_config(args.config)
    llm = LLMClient(config['llm'])

    # 初始化数据库连接池
    import db as db_mod
    db_mod.init_pool(config['database'])

    # 检测新书
    if args.book_id:
        book_ids = [args.book_id]
    elif args.books_dir:
        from import_book import detect_new_books
        book_ids = detect_new_books(Path(args.books_dir))
    else:
        parser.print_help()
        sys.exit(1)

    for book_id in book_ids:
        steps = [s.strip() for s in args.steps.split(',')] if args.steps else None
        run_full_pipeline(book_id, config, llm, args.force,
                          books_mode=bool(args.books_dir), steps=steps)


# 合法 step 名 (--steps 用)
VALID_STEPS = {'import', 'embed', 'classify', 'mindmap', 'skill', 'script', 'split', 'summary', 'dedup'}


def run_full_pipeline(book_id, config, llm, force=False, books_mode=False, steps=None):
    """完整 8 步流水线（按数据流顺序）

    Args:
        steps: 可选 step 白名单 (str list). None = 全部跑.
               合法值: import / embed / classify / mindmap / skill / script / split / summary / dedup
               示例: ['embed', 'classify', 'dedup'] 只跑这 3 步
    """
    if steps is not None:
        unknown = set(steps) - VALID_STEPS
        if unknown:
            raise ValueError(f'未知 step: {unknown}, 合法值: {sorted(VALID_STEPS)}')
        print(f'\n⚙️ --steps 过滤: 只跑 {steps}')
    print(f'\n{"="*60}\n📚 book={book_id} 开始处理\n{"="*60}')

    def should_run(name: str) -> bool:
        return steps is None or name in steps

    # Step 1: import（如果 -book-id 模式且书已存在，跳过）
    # Step 1: import（books_dir 模式已在 detect_new_books 里完成入库）
    if should_run('import'):
        if books_mode:
            pass  # 已在 detect_new_books 时入库
        else:
            from import_book import import_epub
            from db import get_cursor
            with get_cursor() as cur:
                cur.execute('SELECT path FROM books WHERE id = %s', (book_id,))
                row = cur.fetchone()
            if row and row.get('path'):
                import_epub(Path(row['path']), force=force)
    else:
        print('⏭️ skip import')

    # Step 2: embed
    if should_run('embed'):
        from embed_chunks import embed_pending_chunks
        embed_pending_chunks(book_id, config['embedding'])
    else:
        print('⏭️ skip embed')

    # Step 3: classify (主人 2026-08-22 发现: pipeline 缺分类步骤导致分类分布为 0)
    if should_run('classify'):
        from classify_book import classify_book
        classify_book(book_id, force=force)
    else:
        print('⏭️ skip classify')

    # Step 3.5: mindmap
    if should_run('mindmap'):
        from generate_mindmap import generate_mindmap
        generate_mindmap(book_id, llm, force=force)
    else:
        print('⏭️ skip mindmap')

    # Step 4: skill（参考 mindmap）
    mindmap_path = f'mindmaps/{book_id}.mmd'
    if not Path(mindmap_path).exists():
        mindmap_path = None
    if should_run('skill'):
        from generate_skill import generate_skill
        generate_skill(book_id, llm, force=force, mindmap_path=mindmap_path)
    else:
        print('⏭️ skip skill')

    # Step 5-6: script + tts（参考 skill + mindmap）
    # 铲屎官 2026-08-25 钩定: 必须传 skill_path + mindmap_path, 否则走了默认路径找不到
    skill_path = f'skills/book_{book_id}_SKILL.md'
    if not Path(skill_path).exists():
        skill_path = f'data/{book_id}_SKILL.md'
    if not Path(skill_path).exists():
        skill_path = None
    if should_run('script'):
        from generate_script import generate_script_and_tts
        generate_script_and_tts(book_id, llm, force=force,
                                skill_path=skill_path,
                                mindmap_path=mindmap_path)
    else:
        print('⏭️ skip script')

    # Step 6.5: auto-split（铲屎官 2026-08-25 反馈: 49/56 种合集只生成 1 剧本严重不足）
    # 触发条件: 真实章节 > 50 且未覆盖章节 > 50% → 自动拆 (idempotent: 已覆盖则跳)
    if should_run('split'):
        from db import get_cursor
        with get_cursor() as cur:
            cur.execute("SELECT chapter_index FROM game_scripts WHERE book_id = %s", (book_id,))
            covered = {r['chapter_index'] for r in cur.fetchall()}
        # 拿真实 chapters (和 split_book_scripts 同口径: char_count>=200)
        with get_cursor() as cur:
            cur.execute(
                "SELECT DISTINCT chapter_index FROM chunks "
                "WHERE book_id = %s AND char_count >= 200 ORDER BY chapter_index",
                (book_id,),
            )
            real_chs = [r['chapter_index'] for r in cur.fetchall()]
        n_real = len(real_chs)
        uncovered = [c for c in real_chs if c not in covered]
        SPLIT_THRESHOLD = 50
        if n_real > SPLIT_THRESHOLD and len(uncovered) > n_real * 0.5:
            print(f'\n📦 检测到合集书未完整拆分 (chapters={n_real}, 未覆盖={len(uncovered)}), 自动 split...')
            from split_book_scripts import main as split_main
            import sys as _sys
            _sys.argv = ['split_book_scripts.py', '--book-id', str(book_id), '--group-size', '80', '--min-chars', '200']
            try:
                split_main()
            except SystemExit:
                pass
            except Exception as ex:
                print(f'⚠️ split 失败 (不影响主流程): {ex}')
        else:
            print(f'📖 book={book_id} chapters={n_real}, scripts={len(covered)} (无需 split)')
    else:
        print('⏭️ skip split')

    # Step 7: summary（参考 skill + mindmap）
    if should_run('summary'):
        from generate_summary import generate_summary
        generate_summary(book_id, llm, force=force)
    else:
        print('⏭️ skip summary')

    # Step 8: dedup
    if should_run('dedup'):
        from dedup import dedup_pass
        dedup_pass(book_id)
    else:
        print('⏭️ skip dedup')

    print(f'✅ book={book_id} 全流程完成')


if __name__ == '__main__':
    main()
