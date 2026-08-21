#!/usr/bin/env python3
"""llm_client.py - LLM 调用（多 provider fallback + sanitize）"""
import os
import json
import requests
from typing import Optional

from text_sanitize import sanitize


class LLMClient:
    """统一 LLM 调用接口，自动 fallback 到下一级 provider"""

    def __init__(self, config: dict):
        self.providers = [config['primary']] + config.get('fallback', [])

    def call(self, system: str, user: str, max_tokens: int = 4000,
             temperature: float = 0.4) -> tuple[str, str]:
        """调用 LLM，返回 (text, provider_name)

        自动 fallback：当前 provider 失败时切换到下一级
        自动 sanitize：调用前对 prompt 脱敏
        """
        # 先 sanitize 防敏感词拦截
        user = sanitize(user)
        if system:
            system = sanitize(system)

        last_error = None
        for provider in self.providers:
            try:
                text = self._call_one(provider, system, user, max_tokens, temperature)
                return (text, provider.get('name', provider['provider']))
            except Exception as e:
                last_error = e
                print(f'  ⚠️ {provider.get("name", provider["provider"])} 失败: {e}', file=__import__('sys').stderr)
                continue
        raise RuntimeError(f'所有 LLM provider 都失败: {last_error}')

    def _call_one(self, provider: dict, system: str, user: str,
                  max_tokens: int, temperature: float) -> str:
        provider_type = provider['provider']

        if provider_type == 'anthropic':
            return self._call_anthropic(provider, system, user, max_tokens)
        elif provider_type in ('openai', 'ollama'):
            return self._call_openai(provider, system, user, max_tokens, temperature)
        else:
            raise ValueError(f'未知 provider: {provider_type}')

    def _call_anthropic(self, p, system, user, max_tokens):
        resp = requests.post(
            f'{p["base_url"]}/v1/messages',
            headers={
                'Content-Type': 'application/json',
                'x-api-key': p['api_key'],
                'anthropic-version': '2023-06-01',
            },
            json={
                'model': p['model'],
                'max_tokens': max_tokens,
                'system': system,
                'messages': [{'role': 'user', 'content': user}],
            },
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()['content'][0]['text']

    def _call_openai(self, p, system, user, max_tokens, temperature):
        messages = []
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append({'role': 'user', 'content': user})

        resp = requests.post(
            f'{p["base_url"]}/chat/completions',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {p["api_key"]}',
                'User-Agent': 'curl/8.5.0',
            },
            json={
                'model': p['model'],
                'messages': messages,
                'max_tokens': max_tokens,
                'temperature': temperature,
            },
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content']
