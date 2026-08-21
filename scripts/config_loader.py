#!/usr/bin/env python3
"""config_loader.py - 配置加载"""
import os
import yaml
from pathlib import Path
from typing import Optional


def load_config(path: str = 'config/config.yaml') -> dict:
    """加载 YAML 配置，支持环境变量覆盖

    优先级: 环境变量 > config.yaml > config.example.yaml
    """
    config_path = Path(path)
    if not config_path.exists():
        # fallback 到 example
        example = config_path.parent / 'config.example.yaml'
        if example.exists():
            print(f'⚠️  {config_path} 不存在，使用 {example}')
            config_path = example
        else:
            raise FileNotFoundError(f'找不到配置文件: {path}')

    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))

    # 环境变量覆盖（敏感字段）
    if 'llm' in config:
        if primary := config['llm'].get('primary'):
            primary['api_key'] = os.environ.get('LLM_PRIMARY_API_KEY', primary.get('api_key', ''))
        for fb in config['llm'].get('fallback', []):
            fb['api_key'] = os.environ.get(f'LLM_{fb.get("name", "").upper()}_API_KEY', fb.get('api_key', ''))

    if 'embedding' in config:
        config['embedding']['api_key'] = os.environ.get('EMBEDDING_API_KEY', config['embedding'].get('api_key', ''))

    if 'database' in config:
        db = config['database']
        db['password'] = os.environ.get('DB_PASSWORD', db.get('password', ''))
        db['host'] = os.environ.get('DB_HOST', db.get('host', 'localhost'))
        db['port'] = int(os.environ.get('DB_PORT', db.get('port', 15433)))
        db['user'] = os.environ.get('DB_USER', db.get('user', 'admin'))
        db['dbname'] = os.environ.get('DB_NAME', db.get('dbname', 'jiujiu_mind'))

    return config
