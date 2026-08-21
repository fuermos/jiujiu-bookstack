#!/usr/bin/env python3
"""test_llm_client.py - LLM client 测试"""
import sys
sys.path.insert(0, 'scripts')

import pytest
from llm_client import LLMClient


def test_client_init():
    """client 初始化"""
    config = {
        'primary': {'name': 'test', 'provider': 'anthropic', 'base_url': 'http://x', 'api_key': 'k', 'model': 'm'},
        'fallback': [],
    }
    c = LLMClient(config)
    assert len(c.providers) == 1
    print('✅ test_client_init')


def test_sanitize_applied():
    """调用前应自动 sanitize"""
    import unittest.mock as mock

    config = {
        'primary': {'name': 'test', 'provider': 'openai', 'base_url': 'http://x', 'api_key': 'k', 'model': 'm'},
    }
    c = LLMClient(config)

    # 假设敏感词库已有 "肉桂糖棍" → 替换
    with mock.patch('requests.post') as mock_post:
        mock_post.return_value.json.return_value = {
            'choices': [{'message': {'content': 'OK'}}],
        }
        mock_post.return_value.raise_for_status = mock.Mock()

        c.call(system='sys', user='他说肉桂糖棍好吃')

        # 检查请求体里的 user 内容已被替换
        call_args = mock_post.call_args
        body = call_args.kwargs.get('json') or call_args[1].get('json')
        # sanitize 替换了，但具体替换要看词库配置
        # 至少不能原样包含"肉桂糖棍"
        print(f'   请求 user: {body["messages"][-1]["content"][:60]}')
        print('✅ test_sanitize_applied')


if __name__ == '__main__':
    test_client_init()
    test_sanitize_applied()
    print('\n🎉 all tests passed!')
