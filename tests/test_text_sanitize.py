#!/usr/bin/env python3
"""test_text_sanitize.py - 敏词脱敏测试"""
import sys
sys.path.insert(0, 'scripts')

from text_sanitize import sanitize, load_rules, add_to_word_library


def test_sanitize_replaces_known():
    """已知词应被替换"""
    text = '他吃了肉桂糖棍感觉很棒'
    result = sanitize(text)
    assert '肉桂糖棍' not in result
    assert '肉桂' in result  # 部分保留
    print('✅ test_sanitize_replaces_known')


def test_sanitize_keeps_clean():
    """干净文本应原样保留"""
    text = '今天天气真好, 出去散步吧'
    result = sanitize(text)
    assert result == text
    print('✅ test_sanitize_keeps_clean')


def test_load_rules():
    """词库加载"""
    rules = load_rules()
    assert isinstance(rules, list)
    # 应该至少有 1 个词（默认词库）
    print(f'✅ test_load_rules (loaded {len(rules)} rules)')


def test_add_to_word_library():
    """加词库 (临时测试, 加完清理)"""
    import json
    test_word = '__test_word__'
    add_to_word_library(test_word, '__replace__')
    rules = load_rules()
    assert any(test_word in p for p, _ in rules), '加词应生效'
    print('✅ test_add_to_word_library')

    # 清理
    from text_sanitize import WORDS_PATH
    data = json.loads(WORDS_PATH.read_text(encoding='utf-8'))
    data['words'] = [w for w in data['words'] if w['pattern'] != test_word]
    WORDS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    test_sanitize_replaces_known()
    test_sanitize_keeps_clean()
    test_load_rules()
    test_add_to_word_library()
    print('\n🎉 all tests passed!')
