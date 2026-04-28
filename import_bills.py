"""
import_bills.py — Импорт законопроектов из bills.txt

Читает номера из bills.txt, загружает данные с сайта Госдумы,
добавляет в базу данных.
"""

import os
import re
import sys
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

# Добавляем текущую папку в путь
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sozd_parser import fetch_bill_full, make_event_hash

BASE_DIR   = Path(__file__).resolve().parent
DB_PATH    = BASE_DIR / 'data' / 'ri.db'
BILLS_FILE = BASE_DIR / 'bills.txt'


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def bill_exists(conn, bill_number: str) -> bool:
    row = conn.execute('SELECT id FROM initiatives WHERE bill_number=?', (bill_number,)).fetchone()
    return row is not None


def read_bill_numbers() -> list[str]:
    if not BILLS_FILE.exists():
        print(f'File {BILLS_FILE} not found.')
        print('Create it and add bill numbers, one per line.')
        print('Example: 1234567-8')
        sys.exit(1)

    numbers = []
    with open(BILLS_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            line = re.sub(r'\s+', '', line)
            if re.fullmatch(r'\d+-\d+', line):
                numbers.append(line)
            else:
                print(f'  Skipping invalid format: {line!r}')
    return numbers


def main():
    print()
    print('=' * 55)
    print('  Import bills from sozd.duma.gov.ru')
    print('=' * 55)

    numbers = read_bill_numbers()
    if not numbers:
        print('No valid bill numbers found in bills.txt')
        sys.exit(1)

    print(f'  Found {len(numbers)} bill number(s)')
    print()

    if not DB_PATH.exists():
        print('Database not found. Run the app first (1_install_and_run.bat)')
        sys.exit(1)

    conn = get_db()
    added = skipped = errors = 0

    for i, num in enumerate(numbers, 1):
        print(f'[{i}/{len(numbers)}] {num}', end=' ... ', flush=True)

        if bill_exists(conn, num):
            print('skipped (already in database)')
            skipped += 1
            continue

        bill = fetch_bill_full(num)
        if not bill:
            print('ERROR: not found on sozd.duma.gov.ru')
            errors += 1
            time.sleep(1)
            continue

        title = bill['title']
        print(f'\n    Title: {title[:70]}{"..." if len(title) > 70 else ""}')
        print(f'    Stage: {bill["stage"]}')

        # Формируем заметки из ключевых событий
        notes = []
        for ev in bill.get('key_events', []):
            notes.append({
                'date': ev.get('date_display', ''),
                'text': ev.get('title', ''),
                'type': 'system',
            })

        links = [{'title': f'Zakonoproekt {num}', 'url': bill['sozd_url'], 'type': 'SOZD'}]

        event_hash = ''
        if bill.get('all_events'):
            event_hash = make_event_hash(bill['all_events'][0])

        c = conn.cursor()
        c.execute('''INSERT INTO initiatives
            (title, bill_number, doc_type, initiator, description,
             status, scope, products, risk, stage,
             date_submitted, date_forecast, date_effective,
             links, notes, last_event_hash, is_new)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            title, num, bill['doc_type'], '', '',
            'project', 'product', json.dumps([]), 'Средний', bill['stage'],
            bill['date_submitted'], '', '',
            json.dumps(links), json.dumps(notes), event_hash, 1
        ))
        conn.commit()
        print(f'    OK (ID {c.lastrowid})')
        added += 1
        time.sleep(0.8)

    conn.close()

    print()
    print('-' * 55)
    print(f'  Done: added {added}, skipped {skipped}, errors {errors}')
    if added > 0:
        print()
        print('  Next steps:')
        print('  1. Start the app (2_run.bat)')
        print('  2. Open each project and fill in:')
        print('     - Products (Kreditovanie / MFO / Vklady / OSAGO / Strakhovanie)')
        print('     - Risk (Vysokij / Srednij / Nizkij)')
        print('     - Description (or use "Summarize" button for LLM)')
        print()


if __name__ == '__main__':
    main()
