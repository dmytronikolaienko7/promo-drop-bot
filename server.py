"""PROMO DROP: local server and conservative public-post code discovery."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from pathlib import Path
import json, re, time, os

ROOT = Path(__file__).resolve().parent
CHANNEL = 'https://t.me/s/play_standoff2'
CACHE = {'at': 0, 'codes': []}
class Posts(HTMLParser):
    def __init__(self):
        super().__init__(); self.depth=0; self.current=None; self.posts=[]; self.textdepth=0
    def handle_starttag(self, tag, attrs):
        a=dict(attrs); cls=a.get('class','')
        if self.current is not None and tag=='div': self.depth+=1
        if tag=='div' and 'tgme_widget_message_wrap' in cls and self.current is None:
            self.current={'text':'','source':''};self.depth=1
        if self.current is not None:
            if tag=='div' and 'tgme_widget_message_text' in cls: self.textdepth=self.depth
            if tag=='a' and 'tgme_widget_message_date' in cls: self.current['source']=a.get('href','')
            if tag=='br' and self.textdepth: self.current['text']+='\n'
    def handle_endtag(self,tag):
        if self.current is None:return
        if tag=='div':
            if self.textdepth==self.depth:self.textdepth=0
            self.depth-=1
            if self.depth<=0:
                self.posts.append(self.current);self.current=None;self.depth=0;self.textdepth=0
        elif self.depth>0 and tag in ('p',): self.current['text']+='\n'
    def handle_data(self,data):
        if self.current is not None and self.textdepth:self.current['text']+=data

def discover():
    if time.time()-CACHE['at']<600:return CACHE['codes']
    req=Request(CHANNEL,headers={'User-Agent':'Mozilla/5.0 (compatible; PromoDrop/1.0)'})
    with urlopen(req,timeout=12) as r:
        raw=r.read(1_500_001)
    if len(raw)>1_500_000:raise ValueError('Завелика сторінка джерела')
    parser=Posts();parser.feed(raw.decode('utf-8','replace'))
    results={}
    for post in parser.posts:
        text=post['text']; source=post['source']
        if not re.search(r'промокод|промокод[ыи]|промик|promo.?code|secret code|секретн.{0,8}код',text,re.I):continue
        if not source.startswith('https://t.me/play_standoff2/'):continue
        # Conservative: accept only explicit code labels, not arbitrary uppercase text.
        matches=re.findall(r'(?:промокод|промик|promo.?code|secret code|секретн.{0,8}код)\s*(?:[-:–—=]|\s+)?\s*[`«"“]?([A-Z0-9]{5,24})\b',text,re.I)
        for code in matches:
            code=code.upper()
            if code in ('STANDOFF','STANDOFF2','PROMO','PROMOCODE'):continue
            results[code]={'code':code,'reward':'Нагорода не підтверджена','source':source}
    CACHE.update(at=time.time(),codes=list(results.values()))
    return CACHE['codes']

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def do_OPTIONS(self):
        self.send_response(204); self.cors(); self.end_headers()
    def cors(self):
        origin=self.headers.get("Origin", "")
        if origin in ("https://dmytronikolaienko7.github.io", "http://localhost:8000", "http://127.0.0.1:8000"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
    def do_GET(self):
        if urlsplit(self.path).path=='/health':
            data=b'{"status":"ok"}'
            self.send_response(200);self.send_header('Content-Type','application/json');self.cors();self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        elif urlsplit(self.path).path=='/api/codes':
            try: payload={'codes':discover(),'source':CHANNEL,'verified_in_game':False};status=200
            except Exception as e:payload={'error':'Не вдалося прочитати офіційні публікації: '+str(e)};status=503
            data=json.dumps(payload,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.cors();self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        else:super().do_GET()

if __name__=='__main__':
    port=int(os.environ.get('PORT','8000'))
    print(f'PROMO DROP server listening on {port}', flush=True)
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
