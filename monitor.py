"""
monitor.py — Фоновый мониторинг изменений в проектах

Каждые N минут проверяет RSS-ленты всех отслеживаемых проектов.
При обнаружении нового события:
  - обновляет стадию в базе
  - добавляет системную заметку
  - вызывает синхронизацию с Confluence
"""

import threading
import time
import json
import logging
from datetime import datetime

from sozd_parser import (
    fetch_all_rss_events, detect_stage, make_event_hash,
    _clean_rss_title, _parse_rss_date_full, STAGE_ORDER,
)

logger = logging.getLogger('ri.monitor')

# Стадии которые означают «принят» — запись переходит в раздел «Принятые акты»
ADOPTED_STAGES = {'Подписан', 'Вступил в силу', 'Одобрен СФ', 'Утверждён', 'Принято'}


class Monitor:
    """Фоновый мониторинг RSS-лент."""

    def __init__(self, app, db_path, interval=300, sync_callback=None):
        """
        app         — Flask app (для контекста БД)
        db_path     — путь к SQLite файлу
        interval    — интервал проверки в секундах (по умолчанию 300 = 5 минут)
        sync_callback — функция(item) вызываемая после обновления записи
        """
        self.app = app
        self.db_path = db_path
        self.interval = interval
        self.sync_callback = sync_callback
        self._thread = None
        self._stop_event = threading.Event()

    def start(self):
        """Запускает фоновый поток мониторинга."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name='ri-monitor')
        self._thread.start()
        logger.info(f'Мониторинг запущен (интервал: {self.interval} сек)')

    def stop(self):
        """Останавливает мониторинг."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self):
        """Основной цикл мониторинга."""
        # Ждём 30 секунд перед первой проверкой (даём серверу запуститься)
        self._stop_event.wait(30)

        while not self._stop_event.is_set():
            try:
                self._check_all()
            except Exception as e:
                logger.error(f'Ошибка в цикле мониторинга: {e}')

            self._stop_event.wait(self.interval)

    def _check_all(self):
        """Проверяет все записи с bill_number."""
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Берём все записи у которых есть bill_number и статус НЕ финальный
        rows = conn.execute(
            "SELECT id, bill_number, title, stage, last_event_hash, status "
            "FROM initiatives WHERE bill_number IS NOT NULL AND bill_number != ''"
        ).fetchall()

        if not rows:
            conn.close()
            return

        logger.info(f'Проверяю {len(rows)} записей...')
        updated_count = 0

        for row in rows:
            try:
                changed = self._check_one(conn, dict(row))
                if changed:
                    updated_count += 1
            except Exception as e:
                logger.error(f'Ошибка проверки #{row["id"]} ({row["bill_number"]}): {e}')
            time.sleep(0.5)  # пауза между запросами

        conn.close()

        if updated_count > 0:
            logger.info(f'Обновлено записей: {updated_count}')

    def _check_one(self, conn, row: dict) -> bool:
        """
        Проверяет одну запись. Возвращает True если были изменения.
        """
        bill_number = row['bill_number']
        current_hash = row.get('last_event_hash', '')

        # Получаем RSS
        events = fetch_all_rss_events(bill_number, limit=10)
        if not events:
            return False

        # Хеш последнего события
        latest = events[0]
        new_hash = make_event_hash(latest)

        if new_hash == current_hash:
            return False  # ничего не изменилось

        # Определяем новую стадию
        new_stage = detect_stage(events)
        old_stage = row.get('stage', '')

        # Определяем — перешёл ли в «принятые»
        new_status = row.get('status', 'project')
        if new_stage in ADOPTED_STAGES and new_status == 'project':
            new_status = 'adopted'
            logger.info(f'  #{row["id"]} {bill_number}: переведён в «Принятые акты» (стадия: {new_stage})')

        # Формируем системную заметку
        event_title = _clean_rss_title(latest.get('raw_title', latest.get('title', '')))
        event_date = _parse_rss_date_full(latest.get('raw_title', '')) or datetime.now().strftime('%d.%m.%Y')
        note = {
            'date': event_date,
            'text': event_title,
            'type': 'system',
        }

        # Читаем текущие заметки, добавляем новую
        notes_raw = conn.execute('SELECT notes FROM initiatives WHERE id=?', (row['id'],)).fetchone()
        notes = json.loads(notes_raw['notes'] or '[]') if notes_raw else []
        notes.insert(0, note)

        # Обновляем запись
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            '''UPDATE initiatives SET
                stage=?, status=?, last_event_hash=?, notes=?, updated_at=?
               WHERE id=?''',
            (new_stage, new_status, new_hash, json.dumps(notes, ensure_ascii=False), now, row['id'])
        )
        conn.commit()

        if old_stage != new_stage:
            logger.info(f'  #{row["id"]} {bill_number}: стадия {old_stage} → {new_stage}')
        else:
            logger.info(f'  #{row["id"]} {bill_number}: новое событие ({event_title[:50]}...)')

        # Callback для синхронизации с Confluence
        if self.sync_callback:
            try:
                # Перечитываем полную запись из базы
                full = conn.execute('SELECT * FROM initiatives WHERE id=?', (row['id'],)).fetchone()
                self.sync_callback(dict(full))
            except Exception as e:
                logger.error(f'Ошибка sync callback для #{row["id"]}: {e}')

        return True
