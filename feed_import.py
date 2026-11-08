"""Akciófigyelő: hozzáférhető, engedélyezetten használt XML termékfeedek beolvasása.

Támogatás: SHOP/SHOPITEM, Google Shopping RSS/item, Pepita Catalog/Product.
Csak HUF árakat dolgoz fel. Az XML feedeket a beallitasok.json
"figyelt_feedek" listájában kell megadni.
"""
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import requests
from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

import engine

LOG = logging.getLogger(__name__)
MAX_BYTES = 20 * 1024 * 1024


def tag_name(node):
    return node.tag.split('}')[-1].lower() if isinstance(node.tag, str) else ''


def field(node, *paths):
    """Példa: field(product, ('Prices', 'DiscountedPrice'), ('SALE_PRICE',))."""
    for path in paths:
        target = node
        for part in path:
            target = next((child for child in target if tag_name(child) == part.lower()), None)
            if target is None:
                break
        if target is not None:
            value = ''.join(target.itertext()).strip()
            if value:
                return value
    return ''


def valid_huf(*values):
    for raw in values:
        raw = raw.upper()
        if (' EUR' in raw or ' USD' in raw or ' GBP' in raw or
                ' CHF' in raw or ' RON' in raw or ' PLN' in raw or '€' in raw or '$' in raw):
            return False
    return True


def products_from_xml(data, feed_url):
    root = SafeET.fromstring(data)
    result = []
    for item in root.iter():
        typ = tag_name(item)
        if typ not in ('shopitem', 'item', 'product'):
            continue
        name = field(item, ('PRODUCTNAME',), ('NAME',), ('title',),
                     ('Descriptions', 'Name'))
        link = field(item, ('URL',), ('ProductUrl',), ('link',))
        category = field(item, ('CATEGORYTEXT',), ('CATEGORY',),
                         ('product_type',), ('Categories', 'Category', 'Name'))
        currency = field(item, ('Prices', 'Currency'), ('currency',))
        availability = field(item, ('availability',), ('Availability', 'Available'))
        if availability.strip().lower() in ('out_of_stock', 'false', '0', 'unavailable'):
            continue

        # SHOPITEM: PRICE_VAT aktuális, OLD_PRICE_VAT referenciaár.
        if typ == 'shopitem':
            current = field(item, ('PRICE_VAT',))
            reference = field(item, ('OLD_PRICE_VAT',))
        # Google Shopping RSS: price normál ár, sale_price akciós ár.
        elif typ == 'item':
            regular = field(item, ('price',))
            sale = field(item, ('sale_price',))
            current, reference = (sale, regular) if sale else (regular, '')
        # Pepita termékfeed: Price normál ár, DiscountedPrice akciós ár.
        else:
            regular = field(item, ('Prices', 'Price'), ('Price',))
            sale = field(item, ('Prices', 'DiscountedPrice'), ('DiscountedPrice',))
            current, reference = (sale, regular) if sale else (regular, '')

        if not valid_huf(current, reference, currency):
            continue
        if currency and currency.upper() not in ('HUF', 'FT'):
            continue
        price = engine.money(current)
        reference_price = engine.money(reference)
        if not price or not name or not link:
            continue
        url = urljoin(feed_url, link)
        if urlsplit(url).scheme != 'https' or not urlsplit(url).netloc:
            continue
        if reference_price is not None and reference_price <= price:
            reference_price = None
        result.append({
            'name': name[:180], 'url': url, 'price': price,
            'reference': reference_price, 'category': category[:180]
        })
    return result


def fetch_xml(session, url):
    if urlsplit(url).scheme != 'https':
        raise ValueError('Csak HTTPS feed URL engedélyezett')
    with session.get(url, timeout=(10, 35), stream=True) as response:
        response.raise_for_status()
        chunks = []
        count = 0
        for chunk in response.iter_content(chunk_size=65536):
            count += len(chunk)
            if count > MAX_BYTES:
                raise ValueError('A feed túl nagy (20 MB felett)')
            chunks.append(chunk)
        return b''.join(chunks), response.url


def scan(config):
    feeds = config.get('figyelt_feedek', [])
    if not feeds:
        LOG.warning('Nincs XML-termékfeed megadva a beallitasok.json figyelt_feedek listájában.')
        return
    threshold = int(config.get('minimum_kedvezmeny', 50))
    session = requests.Session()
    session.headers.update({'User-Agent': 'Akciofigyelo/1.1 (personal monitor; public feed)'})
    db = engine.connect()
    total = 0
    found_count = 0
    errors = 0
    seen = set()
    try:
        for entry in feeds:
            url = entry['url'] if isinstance(entry, dict) else entry
            try:
                xml, actual_url = fetch_xml(session, url)
                products = products_from_xml(xml, actual_url)
                LOG.info('XML feed: %s – %d értelmezhető termék', urlsplit(url).netloc, len(products))
                total += len(products)
                for product in products:
                    if product['url'] in seen:
                        continue
                    seen.add(product['url'])
                    previous_max = engine.update_price(db, product)
                    # A kereskedő által megadott referenciaár vagy valóban megfigyelt ár.
                    advertised = engine.discount(product['price'], product['reference'])
                    historical = engine.discount(product['price'], previous_max)
                    pct = max(advertised, historical)
                    if pct < threshold:
                        continue
                    old = db.execute('SELECT best_discount FROM alerts WHERE url=?',
                                     (product['url'],)).fetchone()
                    if old and old[0] >= pct:
                        continue
                    db.execute('''INSERT INTO alerts(url,best_discount,last_alert)
                        VALUES (?,?,?) ON CONFLICT(url) DO UPDATE SET
                        best_discount=excluded.best_discount,
                        last_alert=excluded.last_alert''',
                        (product['url'], pct, datetime.now(timezone.utc).isoformat()))
                    found_count += 1
                    LOG.info('AJÁNLAT: -%d%%, %s, %s', pct, product['name'], product['url'])
                db.commit()
            except (requests.RequestException, ValueError, SafeET.ParseError, DefusedXmlException) as exc:
                errors += 1
                LOG.warning('XML feed nem dolgozható fel: %s – %s', urlsplit(url).netloc, exc)
        LOG.info('XML összesítés: %d termék, %d új ajánlat, %d forráshiba',
                 total, found_count, errors)
    finally:
        db.close()
        session.close()
