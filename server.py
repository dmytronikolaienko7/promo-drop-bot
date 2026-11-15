"""PROMO BOT 3.0 — public Standoff 2 promo announcements (not in-game verification)."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import re
import time

ROOT = Path(__file__).resolve().parent
CHANNEL = 'https://t.me/s/play_standoff2'
NEWS = 'https://standoff2.com/news'
# Extra feeds are unverified by default; never automatically treat them as official.
EXTRA = [s.strip() for s in os.getenv('PROMO_EXTRA_CHANNELS', '').split(',') if s.strip()]
EXTRA = [s for s in EXTRA if re.fullmatch(r'https://t\.me/s/[A-Za-z0-9_]{5,32}', s)]
SOURCES = list(dict.fromkeys([CHANNEL, NEWS] + EXTRA))
CACHE = {'at': 0, 'codes': [], 'sources': []}
LABEL_RE = re.compile(r'промокод|промик|promo[ -]?code|secret code|секретн.{0,8}код', re.I)
CODE_RE = re.compile(r'(?:промокод|промик|promo[ -]?code|secret code|секретн.{0,8}код)\s*(?:[-:–—=]|\s+)?\s*[`«"“]?([A-Z0-9]{5,24})\b', re.I)
EXPIRED_RE = re.compile(r'(?:промокод|promo[ -]?code).{0,100}(?:термін дії (?:минув|закінчився)|expired|no longer valid)', re.I | re.S)

class Posts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.posts = []
        self.current = None
        self.depth = 0
        self.textdepth = 0
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        if self.current is not None and tag == 'div':
            self.depth += 1
        if tag == 'div' and 'tgme_widget_message_wrap' in classes and self.current is None:
            self.current = {'text': '', 'source': '', 'published_at': None}
            self.depth = 1
        if self.current is not None:
            if tag == 'div' and 'tgme_widget_message_text' in classes:
                self.textdepth = self.depth
            if tag == 'a' and 'tgme_widget_message_date' in classes:
                self.current['source'] = attrs.get('href', '')
            if tag == 'time' and attrs.get('datetime'):
                self.current['published_at'] = attrs['datetime']
            if tag == 'br' and self.textdepth:
                self.current['text'] += '\n'
    def handle_endtag(self, tag):
        if self.current is None:
            return
        if tag == 'div':
            if self.textdepth == self.depth:
                self.textdepth = 0
            self.depth -= 1
            if self.depth <= 0:
                self.posts.append(self.current)
                self.current = None
                self.depth = 0
                self.textdepth = 0
        elif tag == 'p' and self.textdepth:
            self.current['text'] += '\n'
    def handle_data(self, data):
        if self.current is not None and self.textdepth:
            self.current['text'] += data

def discover():
    if time.time() - CACHE['at'] < 600:
        return CACHE['codes'], CACHE['sources']
    found, checks = {}, []
    for url in SOURCES:
        try:
            req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; PromoDrop/3.0)'})
            with urlopen(req, timeout=12) as response:
                raw = response.read(1_500_001)
            if len(raw) > 1_500_000:
                raise ValueError('Сторінка завелика')
            posts = []
            if url.startswith('https://t.me/s/'):
                parser = Posts()
                parser.feed(raw.decode('utf-8', 'replace'))
                channel_name = urlsplit(url).path.removeprefix('/s/')
                posts = [p for p in parser.posts if re.fullmatch(
                    r'https://t\.me/' + re.escape(channel_name) + r'/\d+', p['source'])]
            # The news landing page is checked for availability only; we do not
            # pretend that its HTML navigation constitutes article content.
            for post in posts:
                content = post['text']
                if not LABEL_RE.search(content):
                    continue
                for match in CODE_RE.finditer(content):
                    code = match.group(1).upper()
                    if code in {'STANDOFF', 'STANDOFF2', 'PROMO', 'PROMOCODE'}:
                        continue
                    official = (url == CHANNEL)
                    expired = bool(EXPIRED_RE.search(content))
                    item = {
                        'code': code,
                        'reward': 'Нагорода не підтверджена',
                        'source': post['source'],
                        'published_at': post['published_at'],
                        'status': 'expired_reported' if expired else ('official_source' if official else 'unknown'),
                        'source_official': official,
                        'verified_in_game': False,
                        'status_label': ('У публікації зазначено завершення дії' if expired else
                                         'Опубліковано в офіційному джерелі' if official else
                                         'Не перевірено'),
                    }
                    previous = found.get(code)
                    if previous is None or (official and not previous['source_official']):
                        found[code] = item
            checks.append({'source': url, 'status': 'ok', 'posts': len(posts),
                           'note': 'Доступність сторінки, не пошук у статтях' if url == NEWS else 'Переглянуто публікації'})
        except Exception as exc:
            checks.append({'source': url, 'status': 'error', 'message': str(exc)[:180]})
    if not any(c['status'] == 'ok' for c in checks):
        raise RuntimeError('Усі джерела недоступні')
    CACHE.update(at=time.time(), codes=list(found.values()), sources=checks)
    return CACHE['codes'], CACHE['sources']

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def cors(self):
        origin = self.headers.get('Origin', '')
        if origin in ('https://dmytronikolaienko7.github.io', 'http://localhost:8000', 'http://127.0.0.1:8000'):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()
    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.cors()
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/health':
            return self.send_json({'status': 'ok', 'version': '3.0'})
        if path == '/api/codes':
            try:
                codes, sources = discover()
                return self.send_json({'codes': codes, 'source': CHANNEL, 'sources': sources,
                                       'verified_in_game': False, 'version': '3.0'})
            except Exception as exc:
                return self.send_json({'error': 'Не вдалося перевірити джерела: ' + str(exc)}, 503)
        return super().do_GET()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    print(f'PROMO BOT 3.0 listening on {port}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
