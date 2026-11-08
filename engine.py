#!/usr/bin/env python3
"""Webshop price watcher: public JSON-LD product data and persistent price history."""
import argparse
import json
import logging
import os
import re
import smtplib
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
CONFIG = BASE / 'beallitasok.json'
DB = BASE / 'arak.sqlite3'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; AkcioFigyelo/1.0; personal price monitor)'}


def money(value):
    if isinstance(value, dict):
        value = value.get('price') or value.get('lowPrice')
    if value is None or isinstance(value, bool):
        return None
    s = str(value).replace('\u00a0', '').replace(' ', '').replace('Ft', '').replace('HUF', '')
    s = re.sub(r'[^\d,.]', '', s)
    if not s:
        return None
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.') if len(s.split(',')[-1]) <= 2 else s.replace(',', '')
    elif '.' in s and len(s.split('.')[-1]) == 3:
        s = s.replace('.', '')
    try:
        result = Decimal(s)
        return result if result > 0 else None
    except InvalidOperation:
        return None


def nodes(obj):
    if isinstance(obj, list):
        for part in obj:
            yield from nodes(part)
    elif isinstance(obj, dict):
        yield obj
        if '@graph' in obj:
            yield from nodes(obj['@graph'])
        if 'itemListElement' in obj:
            yield from nodes(obj['itemListElement'])
        if 'item' in obj and isinstance(obj['item'], (dict, list)):
            yield from nodes(obj['item'])


def is_product(node):
    types = node.get('@type', [])
    if isinstance(types, str):
        types = [types]
    return any(t.lower().split('/')[-1] == 'product' for t in types if isinstance(t, str))


def parse_products(html, page_url):
    soup = BeautifulSoup(html, 'html.parser')
    found = {}
    for script in soup.find_all('script', type=lambda x: x and 'ld+json' in x.lower()):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for item in nodes(data):
            if not is_product(item):
                continue
            offer = item.get('offers') or {}
            if isinstance(offer, list):
                offer = next((o for o in offer if isinstance(o, dict) and (o.get('price') or o.get('lowPrice'))), {})
            if not isinstance(offer, dict):
                offer = {}
            price = money(offer.get('price') or offer.get('lowPrice'))
            if price is None:
                continue
            currency = str(offer.get('priceCurrency', 'HUF')).upper()
            if currency not in ('HUF', 'FT'):
                continue
            name = str(item.get('name') or '').strip()
            if not name:
                continue
            url = urljoin(page_url, str(item.get('url') or offer.get('url') or page_url))
            if urlparse(url).scheme not in ('http', 'https'):
                continue
            # Reference prices may be absent or unreliable: never fabricate them.
            reference = None
            for field in ('highPrice',):
                candidate = money(offer.get(field))
                if candidate and candidate > price:
                    reference = candidate
            ps = offer.get('priceSpecification')
            specs = ps if isinstance(ps, list) else [ps]
            for spec in specs:
                if not isinstance(spec, dict):
                    continue
                ptype = str(spec.get('priceType', '')).lower()
                if 'strikethrough' in ptype or 'listprice' in ptype:
                    candidate = money(spec.get('price'))
                    if candidate and candidate > price:
                        reference = candidate
            found[url] = {'name': name[:180], 'url': url, 'price': price, 'reference': reference}
    return list(found.values())


def connect():
    db = sqlite3.connect(DB)
    db.execute('CREATE TABLE IF NOT EXISTS prices (url TEXT PRIMARY KEY, name TEXT, first_price TEXT, min_price TEXT, max_price TEXT, latest_price TEXT, last_seen TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS alerts (url TEXT PRIMARY KEY, best_discount INTEGER, last_alert TEXT)')
    db.commit()
    return db


def discount(price, reference):
    if reference is None or reference <= price:
        return 0
    return int((Decimal(1) - price / reference) * 100)


def update_price(db, product):
    row = db.execute('SELECT first_price, min_price, max_price FROM prices WHERE url=?', (product['url'],)).fetchone()
    previous_max = Decimal(row[2]) if row else None
    now = datetime.now(timezone.utc).isoformat()
    p = product['price']
    if row:
        minimum = min(Decimal(row[1]), p)
        maximum = max(Decimal(row[2]), p)
        db.execute('UPDATE prices SET name=?, min_price=?, max_price=?, latest_price=?, last_seen=? WHERE url=?',
                   (product['name'], str(minimum), str(maximum), str(p), now, product['url']))
    else:
        db.execute('INSERT INTO prices VALUES (?,?,?,?,?,?,?)',
                   (product['url'], product['name'], str(p), str(p), str(p), str(p), now))
    return previous_max


def email_alert(config, offers, dry_run=False):
    if not offers:
        return True
    message = EmailMessage()
    message['Subject'] = f'Akciófigyelő: {len(offers)} új, legalább {config["minimum_kedvezmeny"]}%-os ajánlat'
    message['From'] = config['email']['felado']
    message['To'] = config['email']['cimzett']
    lines = ['Új ajánlatok (a kedvezmények forrását külön jelezzük):', '']
    for item in offers:
        lines.extend([f'{item["name"]}', f'Ár: {item["price"]:,.0f} Ft | Kedvezmény: {item["discount"]}% ({item["source"]})', item['url'], ''])
    message.set_content('\n'.join(lines))
    if dry_run:
        print(message.get_content())
        return True
    password = os.environ.get('AKCIOFIGYELO_EMAIL_JELSZO', '')
    if not password:
        logging.error('Hiányzik az AKCIOFIGYELO_EMAIL_JELSZO környezeti változó.')
        return False
    settings = config['email']
    try:
        with smtplib.SMTP_SSL(settings['smtp_szerver'], int(settings['smtp_port']), timeout=30) as smtp:
            smtp.login(settings['felado'], password)
            smtp.send_message(message)
        logging.info('E-mail elküldve (%s ajánlat).', len(offers))
        return True
    except Exception as exc:
        logging.error('E-mail hiba: %s', exc)
        return False


def run(config, dry_run=False):
    db = connect()
    threshold = int(config['minimum_kedvezmeny'])
    session = requests.Session()
    session.headers.update(HEADERS)
    offers = []
    seen = set()
    for page in config['figyelt_oldalak']:
        url = page['url'] if isinstance(page, dict) else page
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            products = parse_products(response.text, response.url)
            logging.info('%s: %s termék strukturált adatokból', url, len(products))
        except requests.RequestException as exc:
            logging.warning('Nem sikerült lekérni: %s (%s)', url, exc)
            continue
        for product in products:
            if product['url'] in seen:
                continue
            seen.add(product['url'])
            previous_max = update_price(db, product)
            advertised = discount(product['price'], product['reference'])
            historical = discount(product['price'], previous_max)
            pct = max(advertised, historical)
            if pct < threshold:
                continue
            source = 'webshop által megadott referenciaár' if advertised >= historical else 'saját korábbi megfigyelés'
            old = db.execute('SELECT best_discount FROM alerts WHERE url=?', (product['url'],)).fetchone()
            if old and old[0] >= pct:
                continue
            offers.append({**product, 'discount': pct, 'source': source})
    db.commit()
    if offers and email_alert(config, offers, dry_run):
        if not dry_run:
            for item in offers:
                db.execute('INSERT INTO alerts(url,best_discount,last_alert) VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET best_discount=excluded.best_discount,last_alert=excluded.last_alert',
                           (item['url'], item['discount'], datetime.now(timezone.utc).isoformat()))
            db.commit()
    elif not offers:
        logging.info('Most nincs új, legalább %s%%-os észlelt ajánlat.', threshold)
    db.close()


def main():
    parser = argparse.ArgumentParser(description='Magyar webshop akciófigyelő')
    parser.add_argument('--teszt', action='store_true', help='Nem küld e-mailt, csak kiírja a találatokat')
    parser.add_argument('--folyamatos', action='store_true', help='Ismétlődő figyelés a beállított időközzel')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    if not 0 <= int(config['minimum_kedvezmeny']) <= 100:
        parser.error('A kedvezmény 0 és 100 között legyen.')
    while True:
        try:
            run(config, dry_run=args.teszt)
        except Exception:
            logging.exception('Váratlan hiba')
        if not args.folyamatos:
            break
        time.sleep(max(15, int(config.get('ellenorzes_perc', 60))) * 60)


if __name__ == '__main__':
    main()
