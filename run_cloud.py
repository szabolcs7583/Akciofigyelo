"""GitHub Actions: akciók ellenőrzése és JSON-adatok frissítése.
A docs/index.html fájlt szándékosan nem módosítja.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import engine

BASE = Path(__file__).resolve().parent
config = json.loads((BASE / 'beallitasok.json').read_text(encoding='utf-8'))
config.setdefault('email', {})
config['email']['felado'] = os.getenv('MAIL_FROM', '')
config['email']['cimzett'] = os.getenv('MAIL_TO', '')
if os.getenv('AKCIOFIGYELO_EMAIL_JELSZO') and not (config['email']['felado'] and config['email']['cimzett']):
    raise SystemExit('A MAIL_FROM és MAIL_TO titkokat is be kell állítani.')

engine.run(config)
rows = []
if (BASE / 'arak.sqlite3').exists():
    with sqlite3.connect(BASE / 'arak.sqlite3') as db:
        rows = db.execute('''SELECT p.name,p.latest_price,a.best_discount,p.url,a.last_alert
          FROM alerts a JOIN prices p ON a.url=p.url
          ORDER BY a.last_alert DESC LIMIT 100''').fetchall()

items = [
    {'name': name, 'price': float(price), 'discount': int(pct), 'url': url, 'seen': seen}
    for name, price, pct, url, seen in rows
]
output = {
    'updated_utc': datetime.now(timezone.utc).isoformat(),
    'minimum_discount': int(config['minimum_kedvezmeny']),
    'offers': items,
}
docs = BASE / 'docs'
docs.mkdir(exist_ok=True)
(docs / 'akciok.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
print('Akcióadatok frissítve, találatok:', len(items))
