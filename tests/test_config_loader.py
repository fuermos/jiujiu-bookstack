#!/usr/bin/env python3
"""test_config_loader.py - 配置加载测试"""
import sys
sys.path.insert(0, 'scripts')

import os
from pathlib import Path


def test_load_example_config():
    """加载 example 配置"""
    from config_loader import load_config
    config = load_config('config/config.example.yaml')
    assert 'llm' in config
    assert 'embedding' in config
    assert 'database' in config
    print('✅ test_load_example_config')


def test_env_override():
    """环境变量覆盖"""
    from config_loader import load_config
    os.environ['LLM_PRIMARY_API_KEY'] = 'env-test-key'

    # 创建临时配置
    tmp = Path('/tmp/test_config.yaml')
    tmp.write_text("""
llm:
  primary:
    provider: anthropic
    api_key: file-key
    model: claude
""", encoding='utf-8')

    try:
        config = load_config(str(tmp))
        assert config['llm']['primary']['api_key'] == 'env-test-key'
        print('✅ test_env_override')
    finally:
        tmp.unlink()
        os.environ.pop('LLM_PRIMARY_API_KEY')


if __name__ == '__main__':
    test_load_example_config()
    test_env_override()
    print('\n🎉 all tests passed!')
