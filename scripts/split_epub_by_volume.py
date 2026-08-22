#!/usr/bin/env python3
"""split_epub_by_volume.py - 把一个多卷 epub 拆成多本书

按 x1, x2, x3... 卷号分拆, 每卷一个 epub

用法: python scripts/split_epub_by_volume.py books/sherlock_holmes.epub
"""
import sys
import re
from pathlib import Path

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import warnings
from bs4 import XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

VOL_PATTERN = re.compile(r'x(\d+)')


def extract_vol(name):
    """从文件名抽卷号, 如 'x1-chapter001.xhtml' → 1"""
    m = VOL_PATTERN.search(name)
    if m:
        return f'x{m.group(1)}'
    return None


def parse_epub(file_path):
    book = epub.read_epub(str(file_path))
    chapters = []  # [(vol, title, content, name), ...]
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        name = item.get_name()
        vol = extract_vol(name)
        if not vol:
            continue  # 跳过非卷内容 (titlepage, cover, copyright)
        # 跳过 copyright 文件
        if 'copyright' in name.lower() or 'cover' in name.lower():
            continue
        content = item.get_content().decode('utf-8', errors='ignore')
        soup = BeautifulSoup(content, 'lxml')
        title = ''
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        elif soup.find('h1'):
            title = soup.find('h1').get_text().strip()
        elif soup.find('h2'):
            title = soup.find('h2').get_text().strip()
        text = soup.get_text(separator='\n', strip=True)
        if not text.strip():
            continue
        # 跳过以"版权"开头的版权信息页
        if text.strip().startswith('版权信息') or 'Copyright' in text[:50]:
            continue
        chapters.append({'vol': vol, 'title': title, 'content': content, 'name': name})
    return chapters


def group_by_vol(chapters):
    groups = {}
    for ch in chapters:
        groups.setdefault(ch['vol'], []).append(ch)
    return groups


def build_volume_epub(vol_key, chapters, output_path):
    """为一卷生成独立 epub - 用 zipfile 直接打包"""
    import zipfile

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # mimetype 必须第一个且不压缩
        zf.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        # container.xml
        zf.writestr('META-INF/container.xml', '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>''')

        # content.opf
        n = len(chapters)
        spine = '\n'.join(f'    <itemref idref="chap{i+1}"/>' for i in range(n))
        manifest_items = '\n'.join(
            f'    <item id="chap{i+1}" href="xhtml/chap{i+1}.xhtml" media-type="application/xhtml+xml"/>'
            for i in range(n)
        )
        nav_items = ''
        toc_links = '\n'.join(f'    <li><a href="xhtml/chap{i+1}.xhtml">{c["title"] or f"Chapter {i+1}"}</a></li>' for i, c in enumerate(chapters))

        opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">sherlock-vol-{vol_key}</dc:identifier>
    <dc:title>福尔摩斯探案全集 - 卷{vol_key[1:]}</dc:title>
    <dc:creator>Arthur Conan Doyle</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
{manifest_items}
  </manifest>
  <spine>
{spine}
  </spine>
</package>'''
        zf.writestr('content.opf', opf)

        # nav.xhtml
        nav = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
<nav epub:type="toc" id="toc">
  <ol>
{toc_links}
  </ol>
</nav>
</body>
</html>'''
        zf.writestr('nav.xhtml', nav)

        # 每个章节
        for i, ch in enumerate(chapters):
            zf.writestr(f'xhtml/chap{i+1}.xhtml', ch['content'])

    return output_path


def main():
    if len(sys.argv) < 2:
        print('用法: python split_epub_by_volume.py <epub_path>')
        sys.exit(1)
    src = Path(sys.argv[1])
    if not src.exists():
        print(f'❌ {src} 不存在')
        sys.exit(1)
    output_dir = src.parent / '_volumes'
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob('*.epub'):
        old.unlink()

    chapters = parse_epub(src)
    print(f'📖 解析 epub: {len(chapters)} 章 (跳过 titlepage/cover/copyright)')
    groups = group_by_vol(chapters)
    print(f'📚 共 {len(groups)} 卷: {sorted(groups.keys())}')

    for vol_key in sorted(groups.keys()):
        vol_chapters = groups[vol_key]
        out = build_volume_epub(vol_key, vol_chapters, output_dir / f'sherlock_vol_{vol_key}.epub')
        size_kb = out.stat().st_size / 1024
        title_sample = vol_chapters[0]['title'][:40]
        print(f'  ✅ {out.name} ({size_kb:.0f}KB, {len(vol_chapters)} 章, 首章: {title_sample})')

    print(f'\n📂 输出: {output_dir}')


if __name__ == '__main__':
    main()