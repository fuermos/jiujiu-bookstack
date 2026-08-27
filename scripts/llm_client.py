#!/usr/bin/env python3
"""llm_client.py - LLM 调用（多 provider fallback + sanitize + retry + 详细错误）

主人 2026-08-25 钩定:
- 加 retry (默认 3 次指数退避 2s/4s/8s)
- 错误信息要明确 (调用哪个模型、错码、详情、修复建议)
- 支持根据模型 max_input_tokens 动态适配 prompt 大小
"""
import os
import sys
import json
import time
import requests
from typing import Optional

from text_sanitize import sanitize


# 提供商可选预估：默认 context window (输入+输出 tokens)。可根据 provider 配置覆盖
_DEFAULT_MAX_INPUT_TOKENS = 32000  # Qwen3.8-27b 默认


def _retry(fn, max_retries: int = 3, base_delay: float = 2.0,
           retry_on_400: bool = False, provider_name: str = '?') -> str:
    """统一重试包装: 指数退避 (2s/4s/8s), 4xx 默认不重试 (请求本身错)

    retry_on_400=True 时重试 400 (偶尔 server 抖动)
    """
    last_err = None
    for i in range(max_retries):
        try:
            return fn()
        except requests.exceptions.HTTPError as e:
            last_err = e
            code = e.response.status_code if e.response else 0
            # 4xx (除 408/429) 不重试 — 请求本身错, 重试也没用
            if not retry_on_400 and 400 <= code < 500 and code not in (408, 429):
                raise
            if i < max_retries - 1:
                delay = base_delay * (2 ** i)
                print(f'  ⏳ [{provider_name}] HTTP {code}, {i+1}/{max_retries} 重试 (等 {delay}s)...',
                      file=sys.stderr, flush=True)
                time.sleep(delay)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = e
            if i < max_retries - 1:
                delay = base_delay * (2 ** i)
                print(f'  ⏳ [{provider_name}] 网络错 {type(e).__name__}, {i+1}/{max_retries} 重试 (等 {delay}s)...',
                      file=sys.stderr, flush=True)
                time.sleep(delay)
        except Exception:
            raise  # 其他错误不重试 (语法错、配置错等)
    raise last_err


class LLMClient:
    """统一 LLM 调用接口，自动 fallback 到下一级 provider"""

    def __init__(self, config: dict):
        self.providers = [config['primary']] + config.get('fallback', [])

    def call(self, system: str, user: str, max_tokens: int = 4000,
             temperature: float = 0.4) -> tuple[str, str]:
        """调用 LLM，返回 (text, provider_name)

        自动 fallback: 当前 provider 失败时切换到下一级
        自动 retry: 每个 provider 内部重试 3 次 (默认不重试 400)
        自动 sanitize: 调用前对 prompt 脱敏
        """
        # 先 sanitize 防敏感词拦截
        user = sanitize(user)
        if system:
            system = sanitize(system)

        last_error = None
        for provider in self.providers:
            name = provider.get('name', provider.get('provider', '?'))
            try:
                text = _retry(
                    lambda: self._call_one(provider, system, user, max_tokens, temperature),
                    max_retries=3,
                    base_delay=2.0,
                    provider_name=name,
                )
                return (text, name)
            except Exception as e:
                last_error = e
                # 详细错误: 让人一眼看出问题
                detail = self._format_error(e, provider)
                print(f'  ⚠️ [{name}] 失败: {detail}', file=sys.stderr, flush=True)
                continue

        # 全部失败 — 错误信息要可读 + 给出建议
        raise RuntimeError(self._format_all_failed_error(last_error))

    def _format_error(self, e: Exception, provider: dict) -> str:
        """详细错误信息: 模型名 + 错误码 + 响应 + 修复建议"""
        name = provider.get('name', provider.get('provider', '?'))
        model = provider.get('model', '?')
        if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
            code = e.response.status_code
            try:
                body = e.response.json()
                body_str = json.dumps(body, ensure_ascii=False)[:300]
            except Exception:
                body_str = e.response.text[:300]
            hint = ''
            if code == 400:
                hint = '【提示】可能是 prompt 超长 (超过模型 max_input_tokens) 或格式不对, 试试减小 prompt 或换模型'
            elif code == 401:
                hint = '【提示】API Key 无效, 检查 provider.api_key 配置'
            elif code == 404:
                hint = '【提示】模型不存在或 base_url 错, 检查 provider.model / base_url'
            elif code == 429:
                hint = '【提示】触发限流, 等会儿再试或在 provider 加 rate limit'
            elif code >= 500:
                hint = '【提示】provider 服务异常, 等会儿再试或切换到 fallback'
            return f'HTTP {code} | provider={name} | 模型={model} | {body_str} | {hint}'
        return f'{type(e).__name__}: {e} | provider={name} | 模型={model}'

    def _format_all_failed_error(self, last_error) -> str:
        """所有 provider 都失败时的汇总错误"""
        providers_list = ', '.join(p.get('name', p.get('provider', '?')) for p in self.providers)
        if isinstance(last_error, requests.exceptions.HTTPError) and last_error.response is not None:
            return (
                f'所有 LLM provider ({providers_list}) 都失败\n'
                f'最后一次错误: HTTP {last_error.response.status_code}\n'
                f'常见原因:\n'
                f'  1. prompt 超长 → 减少输入文本量 (调 generate_summary.py 里 full_text 限制)\n'
                f'  2. 模型不在线/未加载 → 检查 LM Studio 或 API 服务\n'
                f'  3. API Key 过期 → 检查 provider.api_key\n'
                f'原始错: {last_error}'
            )
        return f'所有 LLM provider ({providers_list}) 都失败: {last_error}'

    def get_max_input_tokens(self, provider_index: int = 0) -> int:
        """获取某个 provider 的 max_input_tokens (用于 prompt 预算)

        provider 配置里可选设 max_input_tokens, 否则用默认 32K
        """
        if provider_index >= len(self.providers):
            provider_index = 0
        return self.providers[provider_index].get('max_input_tokens', _DEFAULT_MAX_INPUT_TOKENS)

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
