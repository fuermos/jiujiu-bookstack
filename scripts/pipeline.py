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
        run_full_pipeline(book_id, config, llm, args.force)


def run_full_pipeline(book_id, config, llm, force=False):
    """完整 8 步流水线（按数据流顺序）"""
    print(f'\n{"="*60}\n📚 book={book_id} 开始处理\n{"="*60}')

    # Step 1: import（如果 -book-id 模式且书已存在，跳过）
    if not is_book_imported(book_id):
        from import_book import import_epub
        import_epub(book_id, force=force)

    # Step 2: embed
    from embed_chunks import embed_pending_chunks
    embed_pending_chunks(book_id, config['embedding'])

    # Step 3.5: mindmap
    from generate_mindmap import generate_mindmap
    generate_mindmap(book_id, llm, force=force)

    # Step 4: skill（参考 mindmap）
    from generate_skill import generate_skill
    generate_skill(book_id, llm, force=force, mindmap_path=get_mindmap_path(book_id))

    # Step 5-6: script + tts（参考 skill + mindmap）
    from generate_script import generate_script_and_tts
    generate_script_and_tts(book_id, llm, force=force)

    # Step 7: summary（参考 skill + mindmap）
    from generate_summary import generate_summary
    generate_summary(book_id, llm, force=force)

    # Step 8: dedup
    from dedup import dedup_pass
    dedup_pass(book_id)

    print(f'✅ book={book_id} 全流程完成')


if __name__ == '__main__':
    main()
