"""
sozd_parser.py — Парсинг данных с сайта Госдумы (sozd.duma.gov.ru)

Логика извлечена из bot.py и расширена:
- Определение стадии по иерархии всех RSS-событий (не только последнего)
- Извлечение ключевых событий для заметок
- Извлечение пояснительной записки
- Определение типа документа
"""

import re
import calendar as cal_mod
import hashlib
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
HEADERS = {"User-Agent": "Mozilla/5.0 (RI-platform)"}
TIMEOUT = 20

SOZD_BILL_URL = "https://sozd.duma.gov.ru/bill/{n}"
SOZD_RSS_URL  = "https://sozd.duma.gov.ru/bill/{n}/rss"


# ════════════════════════════════════════════════════════════
#  Стадии — иерархия для законопроектов
# ════════════════════════════════════════════════════════════

# Порядок от финальной к начальной — при обнаружении в RSS берём самую продвинутую
STAGE_HIERARCHY = [
    # Финальные
    ('вступил в силу',                      'Вступил в силу'),
    ('опубликован на портале правовой',     'Вступил в силу'),
    ('подписан президентом',                'Подписан'),
    # Совет Федерации
    ('одобрен советом федерации',           'Одобрен СФ'),
    ('рассмотрение советом федерации',      'Одобрен СФ'),
    # Госдума — чтения
    ('принят в третьем чтении',             '3-е чтение'),
    ('третье чтение',                       '3-е чтение'),
    ('принят во втором чтении',             '2-е чтение'),
    ('второе чтение',                       '2-е чтение'),
    ('принять законопроект во втором чтении','2-е чтение'),
    ('принят в первом чтении',              '1-е чтение'),
    ('первое чтение',                       '1-е чтение'),
    ('принять законопроект в первом чтении','1-е чтение'),
    ('рассмотрение законопроекта',          '1-е чтение'),
    # Внесение
    ('внесение законопроекта',              'Внесён в ГД'),
    ('зарегистрирован',                     'Внесён в ГД'),
    ('внесен',                              'Внесён в ГД'),
    ('внесён',                              'Внесён в ГД'),
    ('поступил',                            'Внесён в ГД'),
    ('направлен',                           'Внесён в ГД'),
]

# Порядок стадий для сортировки (индекс = приоритет, выше = дальше по процессу)
STAGE_ORDER = {
    'Инициатива': 0,
    'ОРВ / regulation.gov.ru': 1,
    'Внесён в ГД': 2,
    '1-е чтение': 3,
    '2-е чтение': 4,
    '3-е чтение': 5,
    'Принят ГД': 6,
    'Одобрен СФ': 7,
    'Подписан': 8,
    'Вступил в силу': 9,
    # Подзаконные
    'Обсуждение': 1,
    'Проект опубликован': 2,
    'Утверждён': 7,
    'Регистрация в Минюсте': 8,
    'Разработка': 0,
    'ОРВ': 1,
    'Принято': 7,
}

# Ключевые слова для фильтрации «значимых» событий (для заметок)
KEY_EVENT_KEYWORDS = [
    'внесен', 'внесён', 'зарегистрирован', 'поступил',
    'первое чтение', 'первом чтении', 'принят в первом',
    'второе чтение', 'втором чтении', 'принят во втором',
    'третье чтение', 'третьем чтении', 'принят в третьем',
    'одобрен советом', 'совет федерации',
    'подписан', 'опубликован',
    'отклонен', 'отклонён', 'снят с рассмотрения', 'возвращен',
    'направлен в комитет', 'назначен ответственный',
    'рассмотрение законопроекта',
]


# ════════════════════════════════════════════════════════════
#  Утилиты
# ════════════════════════════════════════════════════════════

def clean(s: str) -> str:
    """Нормализация пробелов."""
    return re.sub(r'\s+', ' ', (s or '').strip())


def _parse_rss_date(title_str: str) -> str | None:
    """Извлекает дату из скобок в заголовке RSS: (25.12.2024 10:30:00)."""
    m = re.search(r'\((\d{2}\.\d{2}\.\d{4})\s*\d{2}:\d{2}:\d{2}\)', title_str)
    if m:
        try:
            d = datetime.strptime(m.group(1), '%d.%m.%Y')
            return d.strftime('%Y-%m-%d')
        except Exception:
            pass
    return None


def _parse_rss_date_full(title_str: str) -> str | None:
    """Извлекает дату в формате ДД.ММ.ГГГГ из скобок."""
    m = re.search(r'\((\d{2}\.\d{2}\.\d{4})', title_str)
    if m:
        return m.group(1)
    return None


def _clean_rss_title(title: str) -> str:
    """Убирает дату и номер раздела из заголовка RSS."""
    t = re.sub(r'\)\s*\d+\.\d+\s*', ') ', title)
    t = re.sub(r'^\(.*?\)\s*', '', t)
    return t.strip()


def _entry_date(entry) -> datetime | None:
    """Дата записи RSS как datetime UTC."""
    t = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
    if t:
        ts = cal_mod.timegm(t)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


# ════════════════════════════════════════════════════════════
#  Парсинг названия со страницы СОЗД
# ════════════════════════════════════════════════════════════

def fetch_title(bill_number: str) -> str | None:
    """
    Вытягивает официальное название законопроекта.
    Логика из bot.py:fetch_official_title_sync
    """
    url = SOZD_BILL_URL.format(n=bill_number)
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
    except Exception:
        return None

    soup = BeautifulSoup(r.text, 'html.parser')
    raw_text = soup.get_text('\n', strip=True)

    # Ищем строку начинающуюся с «О» или «Об»
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r'^(О|Об)\s', line) and len(line) > 10:
            return line

    # Фоллбэк — og:title
    og = soup.find('meta', property='og:title')
    raw = og['content'].strip() if og and og.get('content') else ''
    raw = re.sub(r'\s+', ' ', raw).strip()
    raw = re.sub(r'\s*::\s*Система обеспечения законодательной деятельности\s*$', '', raw).strip()
    raw = re.sub(r'\s*\|\s*Система обеспечения законодательной деятельности.*$', '', raw).strip()
    return raw or None


# ════════════════════════════════════════════════════════════
#  Парсинг RSS — все события + определение стадии
# ════════════════════════════════════════════════════════════

def fetch_all_rss_events(bill_number: str, limit: int = 50) -> list[dict]:
    """
    Получает все события из RSS-ленты проекта.
    Возвращает список словарей, отсортированный от новых к старым.
    """
    url = SOZD_RSS_URL.format(n=bill_number)
    try:
        feed = feedparser.parse(url)
    except Exception:
        return []

    if not feed.entries:
        return []

    events = []
    for entry in feed.entries[:limit]:
        title = clean(getattr(entry, 'title', '') or '')
        description = clean(getattr(entry, 'description', '') or getattr(entry, 'summary', '') or '')

        date_iso = _parse_rss_date(title)
        date_display = _parse_rss_date_full(title) or ''
        clean_title = _clean_rss_title(title)

        # Дата из published
        dt_utc = _entry_date(entry)
        if dt_utc and not date_iso:
            date_iso = dt_utc.strftime('%Y-%m-%d')
        if dt_utc and not date_display:
            date_display = dt_utc.astimezone(MSK).strftime('%d.%m.%Y')

        events.append({
            'date_iso':     date_iso or '',
            'date_display': date_display,
            'title':        clean_title,
            'description':  description,
            'raw_title':    title,
        })

    return events


def detect_stage(events: list[dict]) -> str:
    """
    Определяет текущую стадию рассмотрения по ВСЕМ RSS-событиям.
    Перебирает от самой продвинутой стадии к начальной, ищет совпадение.
    """
    # Собираем весь текст всех событий
    all_text = ' '.join(
        (e.get('title', '') + ' ' + e.get('description', '')).lower()
        for e in events
    )

    best_stage = 'Внесён в ГД'  # дефолт для законопроектов в СОЗД
    best_order = STAGE_ORDER.get(best_stage, 0)

    for keyword, stage in STAGE_HIERARCHY:
        if keyword in all_text:
            order = STAGE_ORDER.get(stage, 0)
            if order > best_order:
                best_order = order
                best_stage = stage

    return best_stage


def get_key_events(events: list[dict], max_count: int = 3) -> list[dict]:
    """
    Выбирает ключевые события из RSS для заметок.
    Фильтрует по значимым ключевым словам, берёт последние max_count.
    """
    key_events = []
    for e in events:
        combined = (e.get('title', '') + ' ' + e.get('description', '')).lower()
        is_key = any(kw in combined for kw in KEY_EVENT_KEYWORDS)
        if is_key:
            key_events.append(e)

    # Если ключевых мало — добавляем первые (последние по времени)
    if len(key_events) < max_count:
        for e in events:
            if e not in key_events:
                key_events.append(e)
            if len(key_events) >= max_count:
                break

    return key_events[:max_count]


def get_date_submitted(events: list[dict]) -> str:
    """Дата внесения — ищем самую раннюю запись с «внесён» или берём самую старую."""
    # Ищем явное внесение
    for e in reversed(events):
        combined = (e.get('title', '') + ' ' + e.get('description', '')).lower()
        if any(kw in combined for kw in ['внесен', 'внесён', 'зарегистрирован', 'поступил']):
            return e.get('date_iso', '')

    # Фоллбэк — самая старая запись
    if events:
        return events[-1].get('date_iso', '')
    return ''


def detect_doc_type(title: str, events: list[dict]) -> str:
    """
    Определяет тип документа.
    Для СОЗД это всегда законопроект, но пытаемся определить — депутатский или правительственный.
    """
    all_text = title.lower()
    for e in events:
        all_text += ' ' + (e.get('title', '') + ' ' + e.get('description', '')).lower()

    if 'правительство' in all_text and ('внесен' in all_text or 'внесён' in all_text):
        return 'Законопроект (правительственный)'
    return 'Законопроект (депутатский)'


# ════════════════════════════════════════════════════════════
#  Пояснительная записка
# ════════════════════════════════════════════════════════════

def extract_note_text(bill_number: str) -> str | None:
    """
    Извлекает текст пояснительной записки.
    Логика из bot.py:extract_note_text_sync
    """
    url = SOZD_BILL_URL.format(n=bill_number) + "#bh_note"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
    except Exception:
        return None

    soup = BeautifulSoup(r.text, 'html.parser')

    # Ищем блок пояснительной записки
    anchor = soup.find(id='bh_note')
    if anchor:
        container = anchor
        for _ in range(3):
            if container and container.parent:
                container = container.parent
        t = container.get_text('\n', strip=True) if container else ''
        t = t.strip()
        if len(t) >= 200:
            return t

    # Фоллбэк — весь текст страницы
    raw = soup.get_text('\n', strip=True).strip()
    return raw if len(raw) >= 200 else None


def extract_note_hash(note_text: str) -> str:
    """Хеш для кеширования суммаризации."""
    return hashlib.sha1(clean(note_text).encode('utf-8')).hexdigest()


# ════════════════════════════════════════════════════════════
#  Сроки / дедлайны
# ════════════════════════════════════════════════════════════

def extract_deadlines(bill_number: str) -> dict:
    """
    Извлекает сроки из карточки проекта.
    Логика из bot.py:extract_deadlines_sync
    """
    url = SOZD_BILL_URL.format(n=bill_number)
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
    except Exception:
        return {}

    soup = BeautifulSoup(r.text, 'html.parser')
    deadlines = {}

    for tag in soup.find_all(string=True):
        text = (tag or '').strip()
        if not text:
            continue
        text_lower = text.lower()
        date_matches = re.findall(r'\b(\d{2}\.\d{2}\.\d{4})\b', text)
        if not date_matches:
            continue

        if any(k in text_lower for k in ['представить', 'срок представления', 'предлагаемый срок']):
            if any(ignore in text_lower for ignore in ['дата рассмотрения', 'включить в порядок', 'рассмотрения государственной думой']):
                continue
            if 'поправ' in text_lower:
                deadlines['Поправки'] = date_matches[0]
            elif any(w in text_lower for w in ['отзыв', 'предлож', 'замеч']):
                deadlines['Отзывы и предложения'] = date_matches[0]

    return deadlines


# ════════════════════════════════════════════════════════════
#  Комплексная загрузка данных по номеру проекта
# ════════════════════════════════════════════════════════════

def fetch_bill_full(bill_number: str) -> dict | None:
    """
    Загружает все данные по номеру законопроекта.
    Возвращает словарь готовый для вставки в базу, или None если проект не найден.
    """
    # 1. Название
    title = fetch_title(bill_number)
    if not title:
        return None

    # 2. RSS-события
    events = fetch_all_rss_events(bill_number)

    # 3. Стадия (по иерархии всех событий)
    stage = detect_stage(events)

    # 4. Тип документа
    doc_type = detect_doc_type(title, events)

    # 5. Дата внесения
    date_submitted = get_date_submitted(events)

    # 6. Ключевые события для заметок
    key_events = get_key_events(events, max_count=3)

    # 7. Сроки
    deadlines = extract_deadlines(bill_number)

    # 8. Ссылка на СОЗД
    sozd_url = SOZD_BILL_URL.format(n=bill_number)

    return {
        'bill_number':    bill_number,
        'title':          title,
        'doc_type':       doc_type,
        'stage':          stage,
        'date_submitted': date_submitted,
        'deadlines':      deadlines,
        'key_events':     key_events,
        'all_events':     events,
        'sozd_url':       sozd_url,
    }


def make_event_hash(event: dict) -> str:
    """Хеш события для определения новых изменений (из bot.py)."""
    base = (event.get('title', '') + '||' + event.get('description', '')).encode('utf-8')
    return hashlib.sha1(base).hexdigest()
