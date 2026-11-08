"""GitHub Actions runner: scan, email, and create a mobile-friendly static page."""
import html
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import engine

BASE = Path(__file__).resolve().parent
config = json.loads((BASE / 'beallitasok.json').read_text(encoding='utf-8'))
config['email']['felado'] = os.getenv('MAIL_FROM', '')
config['email']['cimzett'] = os.getenv('MAIL_TO', '')
if os.getenv('AKCIOFIGYELO_EMAIL_JELSZO') and not (config['email']['felado'] and config['email']['cimzett']):
    raise SystemExit('Az EMAIL_FROM és EMAIL_TO titkokat is be kell állítani.')
engine.run(config)
rows = []
if (BASE / 'arak.sqlite3').exists():
    with sqlite3.connect(BASE / 'arak.sqlite3') as db:
        rows = db.execute('''SELECT p.name,p.latest_price,a.best_discount,p.url,a.last_alert
          FROM alerts a JOIN prices p ON a.url=p.url
          ORDER BY a.last_alert DESC LIMIT 100''').fetchall()
items = []
for name, price, pct, url, seen in rows:
    items.append('<article><span class="sale">−{}%</span><h2>{}</h2><p><strong>{:,.0f} Ft</strong></p><a href="{}" rel="noopener noreferrer" target="_blank">Megnézem a webshopban ↗</a><small>Észlelve: {}</small></article>'.format(
        int(pct), html.escape(name), float(price), html.escape(url, quote=True), html.escape(seen[:16].replace('T', ' '))))
body = '\n'.join(items) if items else '<p class="empty">Még nincs rögzített, legalább 50%-os ajánlat. A figyelés futtatás után gyűjt adatokat.</p>'
page = '''<!doctype html><html lang="hu"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#123b38"><meta name="description" content="Magyar webshopok akciófigyelése"><title>Magyar Akciófigyelő</title><link rel="manifest" href="manifest.webmanifest"><style>
*{box-sizing:border-box}body{font-family:system-ui,-apple-system,sans-serif;margin:0;background:#f2f6f4;color:#19312d}header{background:#123b38;color:white;padding:25px 18px}main{max-width:780px;margin:auto;padding:16px}h1{font-size:1.6rem;margin:0 0 8px}h2{font-size:1.1rem}header p{margin:0;color:#d1e7df}article{background:white;padding:20px;margin:0 0 14px;border-radius:14px;box-shadow:0 2px 8px #153a3010}.sale{display:inline-block;background:#d5f6e5;color:#095934;border-radius:20px;padding:5px 12px;font-weight:800}a{color:#09624e;font-weight:700}small{display:block;color:#68736f;margin-top:16px}.empty{background:white;padding:24px;border-radius:14px}footer{font-size:.82rem;color:#576b64;margin:28px 0}strong{font-size:1.3rem}</style></head><body><header><main><h1>🛍️ Magyar Akciófigyelő</h1><p>Legalább 50%-os észlelt kedvezmények</p></main></header><main><p>Utolsó ellenőrzés: UPDATED (UTC)</p>CONTENT<footer>A kedvezmények webshopos referenciaárból vagy korábban megfigyelt árból származhatnak. A korábbi ár nem feltétlenül 30 napos legalacsonyabb ár. A készletet és az aktuális árat mindig ellenőrizd a kereskedőnél.</footer></main></body></html>'''
page = page.replace('UPDATED', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')).replace('CONTENT', body)
(BASE / 'docs').mkdir(exist_ok=True)
(BASE / 'docs' / 'index.html').write_text(page, encoding='utf-8')
print('Mobiloldal frissítve, találatok:', len(rows))
