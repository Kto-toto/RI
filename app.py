"""
app.py — Regulatory Intelligence Platform

Единая модель данных:
  initiatives — одна таблица на весь жизненный цикл НПА
  status: 'project' | 'adopted' (проект или принятый акт)
  scope:  'product' | 'general' (продуктовый или общерегуляторный)
"""

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import sqlite3
import json
import os
import logging
import hashlib
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

import sozd_parser as sozd
import llm_summarizer as llm
import confluence_sync as cf
from monitor import Monitor

app = Flask(__name__)
CORS(app)

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'ri.db')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '300'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('ri')


# ════════════════════════════════════════════════════════════
#  БАЗА ДАННЫХ
# ════════════════════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db(); c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS initiatives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        -- Основное
        title TEXT NOT NULL,
        bill_number TEXT,
        doc_type TEXT,
        initiator TEXT,
        description TEXT,

        -- Классификация
        status TEXT DEFAULT 'project',
        scope TEXT DEFAULT 'product',
        products TEXT DEFAULT '[]',
        risk TEXT DEFAULT 'Средний',
        stage TEXT,

        -- Даты
        date_submitted TEXT,
        date_forecast TEXT,
        date_effective TEXT,

        -- Ссылки, заметки
        links TEXT DEFAULT '[]',
        notes TEXT DEFAULT '[]',

        -- Мониторинг
        last_event_hash TEXT,
        note_text_hash TEXT,
        note_summary TEXT,

        -- Мета
        is_new INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    conn.commit()
    conn.close()


def row_to_dict(row):
    if not row:
        return None
    d = dict(row)
    for f in ['products', 'links', 'notes']:
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except Exception:
                d[f] = []
    return d


def all_items(status=None, scope=None, product=None):
    conn = get_db()
    query = 'SELECT * FROM initiatives WHERE 1=1'
    params = []
    if status:
        query += ' AND status=?'; params.append(status)
    if scope:
        query += ' AND scope=?'; params.append(scope)
    query += ' ORDER BY created_at DESC'
    rows = conn.execute(query, params).fetchall()
    conn.close()
    items = [row_to_dict(r) for r in rows]
    if product:
        items = [i for i in items if product in (i.get('products') or [])]
    return items


def get_item(item_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM initiatives WHERE id=?', (item_id,)).fetchone()
    conn.close()
    return row_to_dict(row)


# ════════════════════════════════════════════════════════════
#  МАРШРУТЫ — СТРАНИЦЫ
# ════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def api_status():
    return jsonify({
        'confluence_enabled': cf.ENABLED,
        'llm_enabled':        llm.is_enabled(),
        'llm_provider':       llm.get_provider_name(),
        'monitor_interval':   CHECK_INTERVAL,
    })


# ════════════════════════════════════════════════════════════
#  МАРШРУТЫ — CRUD
# ════════════════════════════════════════════════════════════

@app.route('/api/initiatives', methods=['GET'])
def list_initiatives():
    status  = request.args.get('status')
    scope   = request.args.get('scope')
    product = request.args.get('product')
    return jsonify(all_items(status=status, scope=scope, product=product))


@app.route('/api/initiatives/<int:iid>', methods=['GET'])
def get_initiative(iid):
    item = get_item(iid)
    if not item:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(item)


@app.route('/api/initiatives', methods=['POST'])
def create_initiative():
    data = request.json
    conn = get_db(); c = conn.cursor()

    notes = data.get('notes', [])
    if not notes:
        notes = [{'date': datetime.now().strftime('%d.%m.%Y'),
                  'text': 'Запись создана.', 'type': 'system'}]

    c.execute('''INSERT INTO initiatives
        (title, bill_number, doc_type, initiator, description,
         status, scope, products, risk, stage,
         date_submitted, date_forecast, date_effective,
         links, notes, last_event_hash, note_summary, is_new)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
        data.get('title', ''),
        data.get('bill_number', ''),
        data.get('doc_type', ''),
        data.get('initiator', ''),
        data.get('description', ''),
        data.get('status', 'project'),
        data.get('scope', 'product'),
        json.dumps(data.get('products', [])),
        data.get('risk', 'Средний'),
        data.get('stage', ''),
        data.get('date_submitted', ''),
        data.get('date_forecast', ''),
        data.get('date_effective', ''),
        json.dumps(data.get('links', [])),
        json.dumps(notes),
        data.get('last_event_hash', ''),
        data.get('note_summary', ''),
        1,
    ))
    nid = c.lastrowid
    conn.commit()
    item = row_to_dict(conn.execute('SELECT * FROM initiatives WHERE id=?', (nid,)).fetchone())
    conn.close()

    _try_sync(item)
    return jsonify(item), 201


@app.route('/api/initiatives/<int:iid>', methods=['PUT'])
def update_initiative(iid):
    data = request.json
    conn = get_db()

    conn.execute('''UPDATE initiatives SET
        title=?, bill_number=?, doc_type=?, initiator=?, description=?,
        status=?, scope=?, products=?, risk=?, stage=?,
        date_submitted=?, date_forecast=?, date_effective=?,
        links=?, is_new=0, updated_at=CURRENT_TIMESTAMP
        WHERE id=?''', (
        data.get('title', ''),
        data.get('bill_number', ''),
        data.get('doc_type', ''),
        data.get('initiator', ''),
        data.get('description', ''),
        data.get('status', 'project'),
        data.get('scope', 'product'),
        json.dumps(data.get('products', [])),
        data.get('risk', 'Средний'),
        data.get('stage', ''),
        data.get('date_submitted', ''),
        data.get('date_forecast', ''),
        data.get('date_effective', ''),
        json.dumps(data.get('links', [])),
        iid,
    ))
    conn.commit()
    item = row_to_dict(conn.execute('SELECT * FROM initiatives WHERE id=?', (iid,)).fetchone())
    conn.close()

    _try_sync(item)
    return jsonify(item)


@app.route('/api/initiatives/<int:iid>', methods=['DELETE'])
def delete_initiative(iid):
    conn = get_db()
    row = conn.execute('SELECT title FROM initiatives WHERE id=?', (iid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    title = row['title']
    conn.execute('DELETE FROM initiatives WHERE id=?', (iid,))
    conn.commit()
    conn.close()

    try:
        cf.delete_page(title)
        cf.sync_summaries(all_items())
    except Exception as e:
        logger.warning(f'Confluence после удаления: {e}')

    return jsonify({'ok': True})


# ════════════════════════════════════════════════════════════
#  ЗАМЕТКИ
# ════════════════════════════════════════════════════════════

@app.route('/api/initiatives/<int:iid>/notes', methods=['POST'])
def add_note(iid):
    data = request.json
    conn = get_db()
    row = conn.execute('SELECT * FROM initiatives WHERE id=?', (iid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    item = row_to_dict(row)
    notes = item.get('notes', [])
    note = {
        'date': datetime.now().strftime('%d.%m.%Y'),
        'text': data.get('text', '').strip(),
        'type': 'user',  # пользовательская заметка — не перезаписывается автоматикой
    }
    notes.insert(0, note)

    conn.execute('UPDATE initiatives SET notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                 (json.dumps(notes, ensure_ascii=False), iid))
    conn.commit()
    conn.close()

    item['notes'] = notes
    _try_sync(item)
    return jsonify(note), 201


# ════════════════════════════════════════════════════════════
#  ПОДГРУЗКА ИЗ СОЗД
# ════════════════════════════════════════════════════════════

@app.route('/api/fetch-sozd/<bill_number>', methods=['GET'])
def fetch_from_sozd(bill_number):
    """
    Загружает данные по номеру законопроекта с сайта Госдумы.
    Возвращает предзаполненные поля для карточки.
    """
    result = sozd.fetch_bill_full(bill_number)
    if not result:
        return jsonify({'error': f'Законопроект {bill_number} не найден на сайте Госдумы'}), 404

    # Формируем заметки из ключевых событий
    notes = []
    for ev in result.get('key_events', []):
        notes.append({
            'date': ev.get('date_display', ''),
            'text': ev.get('title', ''),
            'type': 'system',
        })

    response = {
        'title':          result['title'],
        'bill_number':    bill_number,
        'doc_type':       result['doc_type'],
        'stage':          result['stage'],
        'date_submitted': result['date_submitted'],
        'sozd_url':       result['sozd_url'],
        'notes':          notes,
        'links': [{
            'title': f'Законопроект {bill_number} на сайте Госдумы',
            'url':   result['sozd_url'],
            'type':  'СОЗД Госдума',
        }],
        'last_event_hash': '',
    }

    # Хеш последнего события для мониторинга
    if result.get('all_events'):
        response['last_event_hash'] = sozd.make_event_hash(result['all_events'][0])

    return jsonify(response)


@app.route('/api/summarize/<int:iid>', methods=['POST'])
def summarize_note(iid):
    """
    Суммаризирует пояснительную записку через LLM.
    Использует bill_number для загрузки ПЗ с сайта СОЗД.
    """
    if not llm.is_enabled():
        return jsonify({'error': 'LLM не настроен. Укажите LLM_PROVIDER в .env'}), 400

    item = get_item(iid)
    if not item:
        return jsonify({'error': 'Not found'}), 404

    bill_number = item.get('bill_number', '')
    if not bill_number:
        return jsonify({'error': 'Нет номера законопроекта — невозможно загрузить ПЗ'}), 400

    # Проверяем кеш
    note_text = sozd.extract_note_text(bill_number)
    if not note_text:
        return jsonify({'error': 'Не удалось извлечь пояснительную записку с сайта СОЗД'}), 404

    note_hash = sozd.extract_note_hash(note_text)

    # Если хеш не изменился и суммаризация уже есть — возвращаем кеш
    if item.get('note_text_hash') == note_hash and item.get('note_summary'):
        return jsonify({'summary': item['note_summary'], 'cached': True})

    # Суммаризируем
    try:
        summary = llm.summarize(sozd.clean(note_text))
    except Exception as e:
        return jsonify({'error': f'Ошибка LLM: {str(e)}'}), 500

    # Сохраняем в БД
    conn = get_db()
    conn.execute(
        'UPDATE initiatives SET description=?, note_text_hash=?, note_summary=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
        (summary, note_hash, summary, iid)
    )
    conn.commit()
    conn.close()

    return jsonify({'summary': summary, 'cached': False})


# ════════════════════════════════════════════════════════════
#  ИМПОРТ ПАЧКОЙ
# ════════════════════════════════════════════════════════════

@app.route('/api/import-bills', methods=['POST'])
def import_bills():
    """Импорт массива номеров законопроектов."""
    data = request.json
    numbers = data.get('numbers', [])
    if not numbers:
        return jsonify({'error': 'Пустой список'}), 400

    results = []
    for num in numbers[:50]:  # ограничение
        num = num.strip()
        if not num:
            continue

        # Проверка дубля
        conn = get_db()
        existing = conn.execute('SELECT id FROM initiatives WHERE bill_number=?', (num,)).fetchone()
        conn.close()
        if existing:
            results.append({'number': num, 'status': 'skipped', 'reason': 'Уже в базе'})
            continue

        # Загружаем из СОЗД
        bill_data = sozd.fetch_bill_full(num)
        if not bill_data:
            results.append({'number': num, 'status': 'error', 'reason': 'Не найден на сайте ГД'})
            continue

        # Формируем запись
        notes = [
            {'date': ev.get('date_display', ''), 'text': ev.get('title', ''), 'type': 'system'}
            for ev in bill_data.get('key_events', [])
        ]

        init_data = {
            'title':          bill_data['title'],
            'bill_number':    num,
            'doc_type':       bill_data['doc_type'],
            'status':         'project',
            'scope':          'product',
            'products':       [],
            'risk':           'Средний',
            'stage':          bill_data['stage'],
            'date_submitted': bill_data['date_submitted'],
            'description':    '',
            'links': [{'title': f'Законопроект {num}', 'url': bill_data['sozd_url'], 'type': 'СОЗД Госдума'}],
            'notes':          notes,
            'last_event_hash': sozd.make_event_hash(bill_data['all_events'][0]) if bill_data.get('all_events') else '',
        }

        # Создаём через внутренний API
        conn = get_db(); c = conn.cursor()
        c.execute('''INSERT INTO initiatives
            (title, bill_number, doc_type, status, scope, products, risk, stage,
             date_submitted, description, links, notes, last_event_hash, is_new)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            init_data['title'], num, init_data['doc_type'],
            'project', 'product', json.dumps([]), 'Средний', init_data['stage'],
            init_data['date_submitted'], '',
            json.dumps(init_data['links']), json.dumps(notes),
            init_data['last_event_hash'], 1
        ))
        conn.commit(); conn.close()

        results.append({'number': num, 'status': 'ok', 'title': bill_data['title']})

    return jsonify({'results': results})


# ════════════════════════════════════════════════════════════
#  ПРИНУДИТЕЛЬНАЯ СИНХРОНИЗАЦИЯ
# ════════════════════════════════════════════════════════════

@app.route('/api/sync', methods=['POST'])
def force_sync():
    if not cf.ENABLED:
        return jsonify({'error': 'Confluence не настроен'}), 400
    try:
        cf.sync_summaries(all_items())
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Вспомогательные ──────────────────────────────────────────

def _try_sync(item):
    """Безопасная синхронизация одного элемента с Confluence."""
    if not cf.ENABLED:
        return
    try:
        cf.sync_item(item, all_items())
    except Exception as e:
        logger.warning(f'Confluence sync ошибка: {e}')


# ════════════════════════════════════════════════════════════
#  ЗАПУСК
# ════════════════════════════════════════════════════════════

if __name__ == '__main__':
    init_db()

    import socket
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = '127.0.0.1'

    print('\n' + '=' * 60)
    print('  ⚖️  Regulatory Intelligence — запущен!')
    print('=' * 60)
    print(f'  Приложение:    http://localhost:5000')
    print(f'  В сети:        http://{local_ip}:5000')
    print(f'  Мониторинг:    каждые {CHECK_INTERVAL} сек')

    if llm.is_enabled():
        print(f'  LLM:           {llm.get_provider_name()}')
    else:
        print(f'  LLM:           не настроен (заполните .env)')

    if cf.ENABLED:
        print(f'  Confluence:    {cf.CONFLUENCE_URL} [{cf.CONFLUENCE_SPACE}]')
    else:
        print(f'  Confluence:    не настроен (заполните .env)')

    print('  Остановка:     Ctrl+C')
    print('=' * 60 + '\n')

    # Фоновый мониторинг
    def on_item_updated(row_dict):
        """Callback из монитора — синхронизируем обновлённую запись."""
        item = row_to_dict(row_dict) if not isinstance(row_dict, dict) else row_dict
        # Парсим JSON-поля если они строки
        for f in ['products', 'links', 'notes']:
            if f in item and isinstance(item[f], str):
                try:
                    item[f] = json.loads(item[f])
                except Exception:
                    item[f] = []
        _try_sync(item)

    mon = Monitor(app, DB_PATH, interval=CHECK_INTERVAL, sync_callback=on_item_updated)
    mon.start()

    # Начальная синхронизация Confluence
    if cf.ENABLED:
        try:
            cf.sync_summaries(all_items())
            logger.info('Confluence: начальная синхронизация завершена')
        except Exception as e:
            logger.warning(f'Confluence: ошибка начальной синхронизации: {e}')

    app.run(host='0.0.0.0', port=5000, debug=False)
