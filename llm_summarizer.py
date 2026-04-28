"""
llm_summarizer.py — Суммаризация пояснительных записок через LLM

Поддерживаемые провайдеры: claude, perplexity, deepseek
Выбор провайдера через переменную LLM_PROVIDER в .env
"""

import os
import requests

LLM_PROVIDER = os.getenv('LLM_PROVIDER', '').strip().lower()

# Claude (Anthropic)
CLAUDE_API_KEY = os.getenv('CLAUDE_API_KEY', '').strip()
CLAUDE_MODEL   = os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-20250514').strip()

# Perplexity
PPLX_API_KEY = os.getenv('PPLX_API_KEY', '').strip()
PPLX_MODEL   = os.getenv('PPLX_MODEL', 'sonar-pro').strip()
PPLX_API_URL = os.getenv('PPLX_API_URL', 'https://api.perplexity.ai/chat/completions').strip()

# DeepSeek
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '').strip()
DEEPSEEK_MODEL   = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat').strip()
DEEPSEEK_API_URL = os.getenv('DEEPSEEK_API_URL', 'https://api.deepseek.com/v1/chat/completions').strip()

TIMEOUT = 90

# ── Системный промпт ─────────────────────────────────────────

SYSTEM_PROMPT = (
    "Ты аналитик по законодательству (GR/Regulatory). "
    "Суммируй пояснительную записку законопроекта на русском.\n\n"
    "Правила:\n"
    "1) Не цитируй длинные фрагменты дословно — только перефразирование.\n"
    "2) Пиши конкретно: что вводится/меняется/запрещается/разрешается, "
    "кто обязан, какие сроки/пороги/штрафы (если есть).\n"
    "3) Никаких оценочных суждений, рекомендаций и 'кому важно'.\n"
    "4) Если в тексте указана дата вступления в силу или переходный период — обязательно укажи.\n"
    "5) Формат вывода (plain text, без Markdown/HTML):\n\n"
    "Суть:\n"
    "<3–10 строк связного текста>\n\n"
    "Ключевые изменения:\n"
    "• ... (4–10 пунктов)\n"
)


def is_enabled() -> bool:
    """Проверяет, настроен ли хотя бы один LLM-провайдер."""
    if LLM_PROVIDER == 'claude' and CLAUDE_API_KEY:
        return True
    if LLM_PROVIDER == 'perplexity' and PPLX_API_KEY:
        return True
    if LLM_PROVIDER == 'deepseek' and DEEPSEEK_API_KEY:
        return True
    return False


def get_provider_name() -> str:
    """Возвращает название активного провайдера."""
    if LLM_PROVIDER == 'claude':
        return f'Claude ({CLAUDE_MODEL})'
    if LLM_PROVIDER == 'perplexity':
        return f'Perplexity ({PPLX_MODEL})'
    if LLM_PROVIDER == 'deepseek':
        return f'DeepSeek ({DEEPSEEK_MODEL})'
    return 'не настроен'


def summarize(note_text: str) -> str:
    """
    Суммаризирует пояснительную записку через выбранный LLM.
    Поднимает RuntimeError если провайдер не настроен.
    """
    if not note_text or len(note_text.strip()) < 100:
        raise RuntimeError('Текст пояснительной записки слишком короткий для суммаризации.')

    text = note_text[:25000]  # ограничение для экономии токенов

    if LLM_PROVIDER == 'claude':
        return _call_claude(text)
    elif LLM_PROVIDER == 'perplexity':
        return _call_perplexity(text)
    elif LLM_PROVIDER == 'deepseek':
        return _call_deepseek(text)
    else:
        raise RuntimeError(
            'LLM-провайдер не настроен. Укажите LLM_PROVIDER в файле .env '
            '(claude / perplexity / deepseek) и соответствующий API-ключ.'
        )


# ── Claude (Anthropic API) ────────────────────────────────────

def _call_claude(text: str) -> str:
    if not CLAUDE_API_KEY:
        raise RuntimeError('CLAUDE_API_KEY не задан в .env')

    r = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={
            'x-api-key': CLAUDE_API_KEY,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        },
        json={
            'model': CLAUDE_MODEL,
            'max_tokens': 2000,
            'system': SYSTEM_PROMPT,
            'messages': [
                {'role': 'user', 'content': text}
            ],
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()

    content = data.get('content', [])
    result = ''
    for block in content:
        if block.get('type') == 'text':
            result += block.get('text', '')

    if not result.strip():
        raise RuntimeError('Claude вернул пустой ответ.')
    return result.strip()


# ── Perplexity ─────────────────────────────────────────────────

def _call_perplexity(text: str) -> str:
    if not PPLX_API_KEY:
        raise RuntimeError('PPLX_API_KEY не задан в .env')

    r = requests.post(
        PPLX_API_URL,
        headers={
            'Authorization': f'Bearer {PPLX_API_KEY}',
            'Content-Type': 'application/json',
        },
        json={
            'model': PPLX_MODEL,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': text},
            ],
            'temperature': 0.2,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    result = (data.get('choices', [{}])[0].get('message', {}).get('content') or '').strip()
    if not result:
        raise RuntimeError('Perplexity вернул пустой ответ.')
    return result


# ── DeepSeek ───────────────────────────────────────────────────

def _call_deepseek(text: str) -> str:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError('DEEPSEEK_API_KEY не задан в .env')

    r = requests.post(
        DEEPSEEK_API_URL,
        headers={
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json',
        },
        json={
            'model': DEEPSEEK_MODEL,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': text},
            ],
            'temperature': 0.2,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    result = (data.get('choices', [{}])[0].get('message', {}).get('content') or '').strip()
    if not result:
        raise RuntimeError('DeepSeek вернул пустой ответ.')
    return result
