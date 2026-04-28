"""
confluence_sync.py — Синхронизация с Confluence Cloud (v2)

Работает с единой таблицей initiatives вместо раздельных projects/actives.
"""

import os
import requests
import json
from datetime import datetime

CONFLUENCE_URL       = os.getenv('CONFLUENCE_URL', '').rstrip('/')
CONFLUENCE_EMAIL     = os.getenv('CONFLUENCE_EMAIL', '')
CONFLUENCE_TOKEN     = os.getenv('CONFLUENCE_TOKEN', '')
CONFLUENCE_SPACE     = os.getenv('CONFLUENCE_SPACE', '')
CONFLUENCE_PARENT_ID = os.getenv('CONFLUENCE_PARENT_ID', '')

ENABLED = bool(CONFLUENCE_URL and CONFLUENCE_EMAIL and CONFLUENCE_TOKEN and CONFLUENCE_SPACE)

PROD_BG = {
    'Кредитование': ('#dbeafe', '#1d4ed8'),
    'МФО':          ('#fef3c7', '#b45309'),
    'Вклады':       ('#ede9fe', '#6d28d9'),
    'ОСАГО':        ('#fee2e2', '#b91c1c'),
    'Страхование':  ('#dcfce7', '#15803d'),
}
RISK_BG = {
    'Высокий': ('#fee2e2', '#991b1b'),
    'Средний': ('#fef3c7', '#92400e'),
    'Низкий':  ('#dcfce7', '#166534'),
}
PRODUCTS = ['Кредитование', 'МФО', 'Вклады', 'ОСАГО', 'Страхование']
PRODUCT_ICONS = {'Кредитование':'💳','МФО':'🏦','Вклады':'💰','ОСАГО':'🚗','Страхование':'🛡️'}

STAGE_CHAINS = {
    'Законопроект (депутатский)':['Инициатива','Внесён в ГД','1-е чтение','2-е чтение','3-е чтение','Принят ГД','Одобрен СФ','Подписан'],
    'Законопроект (правительственный)':['ОРВ','Внесён в ГД','1-е чтение','2-е чтение','3-е чтение','Принят ГД','Одобрен СФ','Подписан'],
    'Постановление Правительства':['Разработка','ОРВ','Принято'],
    'Распоряжение Правительства':['Разработка','ОРВ','Принято'],
    'Приказ ФОИВ':['Разработка','ОРВ','Подписан','Рег. в Минюсте'],
    'Указание ЦБ':['Обсуждение','Проект опубл.','Утверждён','Рег. в Минюсте'],
    'Положение ЦБ':['Обсуждение','Проект опубл.','Утверждён','Рег. в Минюсте'],
}

# ── API ───────────────────────────────────────────────────────
def _auth(): return (CONFLUENCE_EMAIL, CONFLUENCE_TOKEN)
def _hdrs(): return {'Content-Type':'application/json','Accept':'application/json'}

def _api(method, path, **kw):
    url = f"{CONFLUENCE_URL}/wiki/rest/api{path}"
    r = requests.request(method, url, auth=_auth(), headers=_hdrs(), timeout=15, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}

def _find(title):
    try:
        d = _api('GET', f'/content?spaceKey={CONFLUENCE_SPACE}&title={requests.utils.quote(title)}&expand=version')
        res = d.get('results', [])
        return res[0] if res else None
    except: return None

def _create(title, body, parent_id=None):
    payload = {'type':'page','title':title,'space':{'key':CONFLUENCE_SPACE},
               'body':{'storage':{'value':body,'representation':'storage'}}}
    pid = parent_id or CONFLUENCE_PARENT_ID
    if pid: payload['ancestors'] = [{'id':str(pid)}]
    return _api('POST', '/content', json=payload)

def _update(page_id, title, body, version):
    return _api('PUT', f'/content/{page_id}', json={
        'type':'page','title':title,'version':{'number':version+1},
        'body':{'storage':{'value':body,'representation':'storage'}}})

def _upsert(title, body, parent_id=None):
    ex = _find(title)
    if ex: return _update(ex['id'], title, body, ex['version']['number'])
    return _create(title, body, parent_id)

def _get_or_create(title):
    p = _find(title)
    if p: return p['id']
    created = _create(title, f'<h1>{title}</h1>', CONFLUENCE_PARENT_ID or None)
    return created['id']

# ── Визуальные хелперы ────────────────────────────────────────
def _fmt(val):
    if not val: return '—'
    if isinstance(val, str) and len(val) == 10:
        try: return datetime.strptime(val, '%Y-%m-%d').strftime('%d.%m.%Y')
        except: pass
    return str(val)

def _days(val):
    if not val: return 9999
    try:
        d = datetime.strptime(val, '%Y-%m-%d') if isinstance(val, str) else val
        return (d - datetime.now()).days
    except: return 9999

def _pill(text, bg, fg, bold=False):
    fw = 'font-weight:600;' if bold else 'font-weight:500;'
    return (f'<span style="display:inline-block;background:{bg};color:{fg};'
            f'padding:3px 10px;border-radius:12px;font-size:11px;{fw}'
            f'white-space:nowrap;margin:2px 3px 2px 0;">{text}</span>')

def _risk_pill(r):
    bg, fg = RISK_BG.get(r, ('#f1f5f9','#475569'))
    return _pill(r, bg, fg, bold=True)

def _stage_pill(s):
    return _pill(s or '—', '#e2e8f0', '#334155')

def _prod_pills(products):
    if not products: return '—'
    if isinstance(products, str):
        try: products = json.loads(products)
        except: products = [p.strip() for p in products.split(',') if p.strip()]
    return ' '.join(_pill(p, *PROD_BG.get(p, ('#f1f5f9','#374151'))) for p in products)

def _stage_pipeline(doc_type, cur):
    chains = STAGE_CHAINS.get(doc_type, [])
    if not chains: return _stage_pill(cur)
    ci = -1
    for i, s in enumerate(chains):
        if cur and (s in cur or cur in s): ci = i; break
    parts = []
    for i, s in enumerate(chains):
        if i == ci:   parts.append(_pill(s, '#2563eb', '#ffffff', bold=True))
        elif i < ci:  parts.append(_pill(s, '#dcfce7', '#166534'))
        else:         parts.append(_pill(s, '#e2e8f0', '#334155'))
    return ' '.join(parts)

def _section_h(text):
    return f'<div style="background:#1e293b;color:#94a3b8;padding:7px 14px;border-radius:6px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin:20px 0 10px;">{text}</div>'

def _page_header(icon, title, sub=''):
    s = (f'<table style="width:100%;border-collapse:collapse;margin-bottom:20px;">'
         f'<tr><td style="background:#0f172a;padding:16px 20px;border-radius:8px;">'
         f'<div style="font-size:20px;font-weight:700;color:#ffffff;">{icon} {title}</div>')
    if sub: s += f'<div style="font-size:12px;color:#64748b;margin-top:4px;">{sub}</div>'
    return s + '</td></tr></table>'

def _metric_card(icon, label, val, sub, bg, fg):
    return (f'<td style="width:25%;padding:16px 20px;background:{bg};border-radius:8px;'
            f'vertical-align:top;border:1px solid rgba(0,0,0,.04);">'
            f'<div style="font-size:11px;color:{fg};font-weight:600;opacity:.8;">{icon} {label}</div>'
            f'<div style="font-size:34px;font-weight:700;color:{fg};margin:8px 0 4px;line-height:1;">{val}</div>'
            f'<div style="font-size:11px;color:{fg};opacity:.6;">{sub}</div></td>')


# ════════════════════════════════════════════════════════════
#  Публичный API
# ════════════════════════════════════════════════════════════

def sync_item(item, all_items):
    """Синхронизировать одну запись + сводные страницы."""
    if not ENABLED: return
    try:
        is_proj = item.get('status') == 'project'
        section = _get_or_create('📁 Карточки проектов' if is_proj else '📁 Принятые акты')
        prefix  = 'Карточка: ' if is_proj else 'НПА: '
        _upsert(prefix + item['title'], _build_card_html(item), section)
        sync_summaries(all_items)
    except Exception as e:
        print(f'[Confluence] sync_item error: {e}')

def sync_summaries(all_items):
    """Пересобрать все сводные страницы."""
    if not ENABLED: return
    root = CONFLUENCE_PARENT_ID or None
    projs   = [i for i in all_items if i.get('status') == 'project']
    adopted = [i for i in all_items if i.get('status') == 'adopted']

    _upsert('🏠 Дашборд — Regulatory Intelligence', _build_dashboard_html(projs, adopted), root)
    _upsert('📋 Реестр проектов НПА', _build_list_html(projs, 'Проекты и инициативы', True), root)
    _upsert('✅ Принятые акты', _build_list_html(adopted, 'Принятые акты', False), root)
    _upsert('📅 Календарь изменений', _build_calendar_html(projs, adopted), root)

    # Общерегуляторные
    general = [i for i in all_items if i.get('scope') == 'general']
    if general:
        _upsert('🌐 Общерегуляторные', _build_list_html(general, 'Общерегуляторные инициативы', True), root)

    # Продуктовые страницы
    prod_section = _get_or_create('🗂️ По продуктам')
    for prod in PRODUCTS:
        icon = PRODUCT_ICONS.get(prod, '📦')
        p_items = [i for i in all_items if prod in (i.get('products') or [])]
        _upsert(f'{icon} {prod}', _build_product_html(prod, p_items), prod_section)

def delete_page(title):
    if not ENABLED: return
    for prefix in ['Карточка: ', 'НПА: ']:
        try:
            p = _find(prefix + title)
            if p: _api('DELETE', f'/content/{p["id"]}')
        except: pass


# ════════════════════════════════════════════════════════════
#  Построение HTML-страниц (тут используем _pill вместо слитного текста)
# ════════════════════════════════════════════════════════════

def _tbl_row(item, i, show_stage=True, show_risk=True):
    bg = '#ffffff' if i % 2 == 0 else '#f8fafc'
    d = _days(item.get('date_forecast') or item.get('date_effective'))
    dfg = '#dc2626' if 0 <= d <= 60 else '#374151'
    dbold = 'font-weight:600;' if 0 <= d <= 60 else ''
    new = (' '+_pill('NEW','#2563eb','#fff',True)) if item.get('is_new') else ''
    is_proj = item.get('status') == 'project'
    prefix = 'Карточка: ' if is_proj else 'НПА: '
    ct = prefix + item['title']
    date_val = item.get('date_forecast') or item.get('date_effective') or ''

    html = f'<tr style="background:{bg};border-bottom:1px solid #e8e8e3;">'
    html += (f'<td style="padding:10px 12px;">'
             f'<ac:link><ri:page ri:content-title="{ct}" ri:space-key="{CONFLUENCE_SPACE}"/>'
             f'<ac:plain-text-link-body><![CDATA[{item["title"]}]]></ac:plain-text-link-body></ac:link>'
             f'{new}<br><span style="font-size:10px;color:#94a3b8;">{item.get("doc_type","")}</span></td>')
    html += f'<td style="padding:10px 12px;">{_prod_pills(item.get("products"))}</td>'
    if show_risk:
        html += f'<td style="padding:10px 12px;text-align:center;">{_risk_pill(item.get("risk",""))}</td>'
    if show_stage:
        html += f'<td style="padding:10px 12px;">{_stage_pill(item.get("stage",""))}</td>'
    html += f'<td style="padding:10px 12px;text-align:center;color:{dfg};{dbold}">{_fmt(date_val)}</td>'
    html += '</tr>'
    return html

def _build_dashboard_html(projs, adopted):
    total = len(projs)
    high = sum(1 for p in projs if p.get('risk') == 'Высокий')
    soon = sum(1 for a in adopted if 0 < _days(a.get('date_effective')) <= 60)
    ts = datetime.now().strftime('%d.%m.%Y %H:%M')

    html = _page_header('⚖️','Regulatory Intelligence — Дашборд', f'Обновлено: {ts}')
    html += '<table style="width:100%;border-collapse:collapse;margin-bottom:24px;"><tr>'
    html += _metric_card('📄','Всего проектов', total, 'в мониторинге', '#f8fafc','#334155')
    html += _metric_card('🔴','Высокий риск', high, 'требуют внимания', '#fff1f2','#991b1b')
    html += _metric_card('⏰','Скоро вступают', soon, 'в ближайшие 60 дней', '#fffbeb','#92400e')
    html += '</tr></table>'

    html += _section_h(f'Проекты и инициативы — {total}')
    html += '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
    html += ('<tr style="background:#1e293b;color:#e2e8f0;">'
             '<th style="padding:9px 12px;text-align:left;">Проект</th>'
             '<th style="padding:9px 12px;text-align:left;">Продукты</th>'
             '<th style="padding:9px 12px;text-align:center;">Риск</th>'
             '<th style="padding:9px 12px;text-align:left;">Стадия</th>'
             '<th style="padding:9px 12px;text-align:center;">Дата / Прогноз</th></tr>')
    for i, p in enumerate(projs):
        html += _tbl_row(p, i)
    html += '</table>'

    upcoming = sorted([a for a in adopted if _days(a.get('date_effective'))>0],
                      key=lambda a: a.get('date_effective','9999'))[:5]
    if upcoming:
        html += _section_h('Ближайшие вступления в силу')
        html += '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        html += ('<tr style="background:#14532d;color:#dcfce7;">'
                 '<th style="padding:9px 12px;text-align:left;">Акт</th>'
                 '<th style="padding:9px 12px;text-align:left;">Продукты</th>'
                 '<th style="padding:9px 12px;text-align:center;">Вступает в силу</th></tr>')
        for i, a in enumerate(upcoming):
            bg = '#fff' if i%2==0 else '#f8fafc'
            d = _days(a.get('date_effective')); fg = '#dc2626' if d<=30 else '#374151'
            ct = 'НПА: '+a['title']
            html += (f'<tr style="background:{bg};border-bottom:1px solid #e8e8e3;">'
                     f'<td style="padding:10px 12px;font-weight:500;">'
                     f'<ac:link><ri:page ri:content-title="{ct}" ri:space-key="{CONFLUENCE_SPACE}"/>'
                     f'<ac:plain-text-link-body><![CDATA[{a["title"]}]]></ac:plain-text-link-body></ac:link></td>'
                     f'<td style="padding:10px 12px;">{_prod_pills(a.get("products"))}</td>'
                     f'<td style="padding:10px 12px;text-align:center;color:{fg};">{_fmt(a.get("date_effective"))}</td></tr>')
        html += '</table>'
    return html

def _build_list_html(items, title, is_project):
    ts = datetime.now().strftime('%d.%m.%Y %H:%M')
    icon = '📋' if is_project else '✅'
    html = _page_header(icon, title, f'Всего: {len(items)} · Обновлено: {ts}')
    html += '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
    hdr_bg = '#1e293b' if is_project else '#14532d'
    hdr_fg = '#e2e8f0' if is_project else '#dcfce7'
    date_label = 'Дата / Прогноз' if is_project else 'Вступает в силу'
    html += (f'<tr style="background:{hdr_bg};color:{hdr_fg};">'
             f'<th style="padding:9px 12px;text-align:left;">Название</th>'
             f'<th style="padding:9px 12px;text-align:left;">Продукты</th>'
             f'<th style="padding:9px 12px;text-align:center;">Риск</th>'
             f'<th style="padding:9px 12px;text-align:left;">Стадия</th>'
             f'<th style="padding:9px 12px;text-align:center;">{date_label}</th></tr>')
    for i, item in enumerate(items):
        html += _tbl_row(item, i)
    html += '</table>'
    return html

def _build_calendar_html(projs, adopted):
    events = []
    for p in projs:
        if p.get('date_forecast'):
            events.append({**p, '_date':p['date_forecast'], '_type':'project'})
    for a in adopted:
        if a.get('date_effective'):
            events.append({**a, '_date':a['date_effective'], '_type':'adopted'})
    events.sort(key=lambda e: e['_date'])

    ts = datetime.now().strftime('%d.%m.%Y %H:%M')
    html = _page_header('📅','Календарь изменений', f'Обновлено: {ts}')

    # Легенда — таблица
    html += ('<table style="border-collapse:collapse;margin-bottom:16px;"><tr>'
             f'<td style="padding:4px 14px 4px 0;">{_pill("Проект","#dbeafe","#1d4ed8")}</td>'
             '<td style="padding:4px 14px 4px 0;font-size:12px;color:#374151;">прогнозная дата</td>'
             '<td style="padding:4px 20px;color:#94a3b8;font-size:18px;">|</td>'
             f'<td style="padding:4px 14px 4px 0;">{_pill("Принятый акт","#ede9fe","#6d28d9")}</td>'
             '<td style="padding:4px 14px 4px 0;font-size:12px;color:#374151;">дата вступления в силу</td>'
             '<td style="padding:4px 20px;color:#94a3b8;font-size:18px;">|</td>'
             f'<td style="padding:4px 14px 4px 0;">{_pill("⚠ Красным","#fee2e2","#991b1b")}</td>'
             '<td style="padding:4px 0;font-size:12px;color:#374151;">ближайшие 14 дней</td>'
             '</tr></table>')

    html += '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
    html += ('<tr style="background:#3b0764;color:#e9d5ff;">'
             '<th style="padding:9px 12px;text-align:center;width:100px;">Дата</th>'
             '<th style="padding:9px 12px;text-align:center;width:100px;">Тип</th>'
             '<th style="padding:9px 12px;text-align:left;">Название</th>'
             '<th style="padding:9px 12px;text-align:left;">Продукты</th>'
             '<th style="padding:9px 12px;text-align:center;">Риск</th></tr>')

    months = ['','Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь']
    last_m = ''
    for i, e in enumerate(events):
        try:
            d = datetime.strptime(e['_date'],'%Y-%m-%d')
            mk = f'{d.year}-{d.month:02d}'
            if mk != last_m:
                html += f'<tr><td colspan="5" style="background:#1e293b;color:#64748b;padding:6px 12px;font-size:11px;font-weight:700;text-align:center;letter-spacing:.05em;text-transform:uppercase;">{months[d.month]} {d.year}</td></tr>'
                last_m = mk
        except: pass

        days = _days(e['_date']); isUrg = 0<=days<=14; isPast = days<0
        bg = '#fff1f2' if isUrg else ('#f8fafc' if isPast else ('#fff' if i%2==0 else '#f8fafc'))
        dfg = '#dc2626' if isUrg else ('#94a3b8' if isPast else '#374151')
        dbold = 'font-weight:700;' if isUrg else ''
        is_p = e['_type']=='project'
        t_pill = _pill('Проект','#dbeafe','#1d4ed8') if is_p else _pill('Принятый','#ede9fe','#6d28d9')
        prefix = 'Карточка: ' if is_p else 'НПА: '
        ct = prefix + e['title']

        html += (f'<tr style="background:{bg};border-bottom:1px solid #e8e8e3;">'
                 f'<td style="padding:9px 12px;text-align:center;color:{dfg};{dbold};font-variant-numeric:tabular-nums;">{_fmt(e["_date"])}</td>'
                 f'<td style="padding:9px 12px;text-align:center;">{t_pill}</td>'
                 f'<td style="padding:9px 12px;font-weight:500;">'
                 f'<ac:link><ri:page ri:content-title="{ct}" ri:space-key="{CONFLUENCE_SPACE}"/>'
                 f'<ac:plain-text-link-body><![CDATA[{e["title"]}]]></ac:plain-text-link-body></ac:link></td>'
                 f'<td style="padding:9px 12px;">{_prod_pills(e.get("products"))}</td>'
                 f'<td style="padding:9px 12px;text-align:center;">{_risk_pill(e.get("risk","")) if e.get("risk") else "—"}</td></tr>')
    html += '</table>'
    return html

def _build_product_html(product, items):
    color_bg, color_fg = PROD_BG.get(product, ('#f1f5f9','#374151'))
    projs = [i for i in items if i.get('status')=='project']
    adopted = [i for i in items if i.get('status')=='adopted']
    ts = datetime.now().strftime('%d.%m.%Y %H:%M')

    html = _page_header(_pill(product, color_bg, color_fg, True),
                        f'Нормативные инициативы — {product}', f'Обновлено: {ts}')

    html += '<table style="width:100%;border-collapse:collapse;margin-bottom:20px;"><tr>'
    html += _metric_card('📄','Проектов',len(projs),'','#f8fafc','#334155')
    html += _metric_card('🔴','Высокий риск',sum(1 for p in projs if p.get('risk')=='Высокий'),'','#fff1f2','#991b1b')
    html += _metric_card('✅','Принятых',len(adopted),'','#f0fdf4','#166534')
    html += '</tr></table>'

    if projs:
        html += _section_h(f'Проекты — {len(projs)}')
        html += '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        html += ('<tr style="background:#1e293b;color:#e2e8f0;">'
                 '<th style="padding:9px 12px;text-align:left;">Проект</th>'
                 '<th style="padding:9px 12px;text-align:left;">Продукты</th>'
                 '<th style="padding:9px 12px;text-align:center;">Риск</th>'
                 '<th style="padding:9px 12px;text-align:left;">Стадия</th>'
                 '<th style="padding:9px 12px;text-align:center;">Прогноз</th></tr>')
        for i, p in enumerate(projs): html += _tbl_row(p, i)
        html += '</table>'

    if adopted:
        html += _section_h(f'Принятые акты — {len(adopted)}')
        html += '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        html += ('<tr style="background:#14532d;color:#dcfce7;">'
                 '<th style="padding:9px 12px;text-align:left;">Акт</th>'
                 '<th style="padding:9px 12px;text-align:left;">Продукты</th>'
                 '<th style="padding:9px 12px;text-align:center;">Вступает в силу</th></tr>')
        for i, a in enumerate(sorted(adopted, key=lambda x: x.get('date_effective','9999'))):
            bg = '#fff' if i%2==0 else '#f8fafc'
            d = _days(a.get('date_effective')); fg = '#dc2626' if 0<d<=30 else '#374151'
            ct = 'НПА: '+a['title']
            html += (f'<tr style="background:{bg};border-bottom:1px solid #e8e8e3;">'
                     f'<td style="padding:10px 12px;font-weight:500;">'
                     f'<ac:link><ri:page ri:content-title="{ct}" ri:space-key="{CONFLUENCE_SPACE}"/>'
                     f'<ac:plain-text-link-body><![CDATA[{a["title"]}]]></ac:plain-text-link-body></ac:link></td>'
                     f'<td style="padding:10px 12px;">{_prod_pills(a.get("products"))}</td>'
                     f'<td style="padding:10px 12px;text-align:center;color:{fg};">{_fmt(a.get("date_effective"))}</td></tr>')
        html += '</table>'

    if not projs and not adopted:
        html += f'<p style="color:#a1a1aa;font-size:13px;font-style:italic;margin:12px 0;">По продукту «{product}» записей нет</p>'
    return html

def _build_card_html(item):
    is_proj = item.get('status') == 'project'
    icon = '📄' if is_proj else '✅'
    sub_parts = [item.get('doc_type','')]
    if is_proj and item.get('initiator'): sub_parts.append(item['initiator'])
    elif not is_proj: sub_parts = [item.get('doc_type','')]

    html = _page_header(icon, item.get('title',''), ' · '.join(filter(None, sub_parts)))

    # Свойства + описание
    html += '<table style="width:100%;border-collapse:collapse;margin-bottom:24px;"><tr>'
    html += '<td style="width:44%;vertical-align:top;padding-right:18px;">'
    html += '<table style="width:100%;border-collapse:collapse;font-size:13px;">'

    fields = [('Продукты', _prod_pills(item.get('products')))]
    if is_proj:
        fields += [
            ('Риск', _risk_pill(item.get('risk','—'))),
            ('Стадия', _stage_pipeline(item.get('doc_type',''), item.get('stage',''))),
            ('Инициатор', item.get('initiator','—')),
            ('Дата внесения', _fmt(item.get('date_submitted'))),
            ('Прогноз вступления', f'<span style="color:{"#dc2626" if 0<=_days(item.get("date_forecast"))<=60 else "#374151"}">{_fmt(item.get("date_forecast"))}</span>'),
        ]
    else:
        fields += [
            ('Дата принятия', _fmt(item.get('date_submitted') or item.get('date_effective'))),
            ('Вступает в силу', f'<span style="color:{"#dc2626" if 0<_days(item.get("date_effective"))<=30 else "#374151"}">{_fmt(item.get("date_effective"))}</span>'),
        ]
    if item.get('date_effective') and is_proj:
        fields.append(('Дата вступления в силу', _fmt(item.get('date_effective'))))

    scope_label = 'Общерегуляторный' if item.get('scope') == 'general' else 'Продуктовый'
    fields.append(('Тип', _pill(scope_label, '#f1f5f9', '#475569')))

    for k, v in fields:
        html += (f'<tr style="border-bottom:1px solid #f0f0ed;">'
                 f'<td style="padding:7px 0;font-size:10px;color:#a1a1aa;font-weight:600;text-transform:uppercase;letter-spacing:.04em;width:38%;white-space:nowrap;vertical-align:top;padding-right:10px;">{k}</td>'
                 f'<td style="padding:7px 0;">{v}</td></tr>')
    html += '</table></td>'

    desc = (item.get('description') or '').replace('\n','<br>')
    border_color = '#2563eb' if is_proj else '#16a34a'
    bg_color = '#f8fafc' if is_proj else '#f0fdf4'
    html += (f'<td style="vertical-align:top;background:{bg_color};border-radius:8px;padding:16px;border-left:3px solid {border_color};">'
             f'<div style="font-size:10px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px;">Суть изменений</div>'
             f'<div style="font-size:13px;line-height:1.7;color:#18181b;">{desc or "<em style=\'color:#a1a1aa;\'>Описание не добавлено</em>"}</div>'
             f'</td></tr></table>')

    # Ссылки
    links = item.get('links', [])
    html += _section_h('🔗 Источники и документы')
    if links:
        html += '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        for lnk in links:
            url = lnk.get('url',''); name = lnk.get('title',''); ltype = lnk.get('type','')
            html += (f'<tr style="border-bottom:1px solid #f0f0ed;">'
                     f'<td style="padding:9px 0;width:28%;">{_pill(ltype,"#eff6ff","#1d4ed8")}</td>'
                     f'<td style="padding:9px 6px;font-weight:500;"><a href="{url}" style="color:#2563eb;">{name}</a></td>'
                     f'<td style="padding:9px 0;font-size:10px;color:#94a3b8;">{url}</td></tr>')
        html += '</table>'
    else:
        html += '<p style="color:#a1a1aa;font-size:13px;font-style:italic;">Ссылки не добавлены</p>'

    # Заметки
    notes = item.get('notes', [])
    html += _section_h('📝 Рабочие заметки')
    if notes:
        for n in notes:
            is_sys = n.get('type') == 'system'
            border = '#94a3b8' if is_sys else '#2563eb'
            bg = '#f8fafc' if is_sys else '#eff6ff'
            prefix = '🔄 ' if is_sys else '📝 '
            html += (f'<div style="border-left:3px solid {border};padding:10px 14px;margin-bottom:8px;background:{bg};border-radius:0 6px 6px 0;">'
                     f'<div style="font-size:10px;color:#94a3b8;margin-bottom:4px;">{prefix}{n.get("date","")}</div>'
                     f'<div style="font-size:13px;color:#18181b;line-height:1.5;">{n.get("text","")}</div></div>')
    else:
        html += '<p style="color:#a1a1aa;font-size:13px;font-style:italic;">Заметок пока нет</p>'

    return html
