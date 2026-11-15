
"""PROMO BOT 3.2: public-source discovery, not game redemption verification."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urljoin, urlencode
from pathlib import Path
import json, os, re, time, threading

ROOT = Path(__file__).resolve().parent
CHANNEL = 'https://t.me/s/play_standoff2'
NEWS = 'https://standoff2.com/news'
HELP = 'https://help.standoff2.com/en/articles/5323853-how-can-i-get-a-promo-code'
EXTRA = [s.strip() for s in os.getenv('PROMO_EXTRA_CHANNELS','').split(',') if s.strip()]
EXTRA = [s for s in EXTRA if re.fullmatch(r'https://t\.me/s/[A-Za-z0-9_]{5,32}',s)]
SOURCES = list(dict.fromkeys([CHANNEL, NEWS, HELP]+EXTRA))
REFRESH_SECONDS = max(300, int(os.getenv('PROMO_REFRESH_SECONDS','600')))
TELEGRAM_PAGES = max(1,min(40,int(os.getenv('PROMO_TELEGRAM_PAGES','15'))))
MAX_ARTICLES = max(0,min(25,int(os.getenv('PROMO_MAX_ARTICLES','10'))))
CACHE = {'at':0,'codes':[],'sources':[]}
LOCK = threading.RLock()
SCAN_LOCK = threading.Lock()
LABEL_RE = re.compile(r'промо\s*[- ]?код|промик|promo\s*[- ]?code|secret\s+code|секретн\w*\s+код|раздач\w*\s+промо',re.I)
INLINE_RE = re.compile(r'(?:промо\s*[- ]?код|промик|promo\s*[- ]?code|secret\s+code|секретн\w*\s+код)\s*(?:[:=–—-]|\s)+\s*[`«"“]?([A-Z0-9]{5,24})\b',re.I)
BARE_RE = re.compile(r'(?<![A-Za-z0-9])([A-Z0-9]{6,20})(?![A-Za-z0-9])')
BAD = {'STANDOFF','STANDOFF2','PROMO','PROMOCODE','PROMOCODES','TELEGRAM','YOUTUBE','DISCORD','AXLEBOLT','STANDOFF2COM','HTTPS','SECRET','CODE','CODES'}
EXPIRED_RE = re.compile(r'ист[её]к|законч\w*|expired|no longer valid|термін дії минув|закінчився термін',re.I)

class TelegramParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.posts=[]; self.current=None; self.depth=0; self.text_depth=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs); cls=a.get('class','').split()
        if tag=='div' and 'tgme_widget_message_wrap' in cls and self.current is None:
            self.current={'text':'','source':'','published_at':None}; self.depth=0; self.text_depth=0
        if self.current is None:return
        if tag=='div':
            self.depth+=1
            if 'tgme_widget_message_text' in cls:self.text_depth=self.depth
        if tag=='a' and 'tgme_widget_message_date' in cls:self.current['source']=a.get('href','')
        if tag=='time' and a.get('datetime'):self.current['published_at']=a['datetime']
        if tag=='br' and self.text_depth:self.current['text']+='\n'
    def handle_endtag(self,tag):
        if self.current is None:return
        if tag=='div':
            if self.text_depth==self.depth:self.text_depth=0
            self.depth-=1
            if self.depth<=0:
                self.posts.append(self.current);self.current=None;self.depth=0;self.text_depth=0
        if tag=='p' and self.text_depth:self.current['text']+='\n'
    def handle_data(self,data):
        if self.current is not None and self.text_depth:self.current['text']+=data

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__();self.text=[];self.links=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('script','style','noscript'):self.skip+=1
        if tag=='a' and a.get('href'):self.links.append(a['href'])
        if tag in ('p','h1','h2','h3','div','br','li'):self.text.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript') and self.skip:self.skip-=1
    def handle_data(self,data):
        if not self.skip:self.text.append(data)

def fetch(url):
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; PromoDrop/3.2; public pages only)'})
    with urlopen(req,timeout=12) as response:
        data=response.read(1_500_001)
    if len(data)>1_500_000:raise ValueError('Сторінка завелика')
    return data.decode('utf-8','replace')

def extract_codes(content):
    """Conservative detection: code must appear near promo context, or inline label."""
    lines=[s.strip(' \t\r\n`«»“”"\'.,:;()[]') for s in content.splitlines()]
    result=set()
    for match in INLINE_RE.finditer(content):
        code=match.group(1).upper()
        if code not in BAD and not code.isdigit():result.add(code)
    for i,line in enumerate(lines):
        # Nearby promo language is required; never mine random all-caps text.
        context=' '.join(lines[max(0,i-5):min(len(lines),i+3)])
        if not LABEL_RE.search(context):continue
        for match in BARE_RE.finditer(line):
            code=match.group(1).upper()
            if code in BAD or code.isdigit():continue
            if re.fullmatch(r'\d{1,2}[A-Z]{3,}',code):continue
            # Avoid unrelated long uppercase prose words and site identifiers.
            if not any(ch.isdigit() for ch in code) and len(code)<9:continue
            result.add(code)
    return result

def item_for(code,url,published,official,content):
    expired=bool(EXPIRED_RE.search(content))
    return {'code':code,'reward':'Нагорода не підтверджена','source':url,
            'published_at':published,'status':'expired_reported' if expired else ('official_source' if official else 'unknown'),
            'source_official':official,'verified_in_game':False,
            'status_label':'У публікації згадано завершення дії; у грі не перевірено' if expired else
              'Знайдено в офіційному джерелі; у грі не перевірено' if official else 'Не перевірено'}

def record(found,code,url,published,official,content):
    prev=found.get(code)
    if prev is None or (official and not prev['source_official']):
        found[code]=item_for(code,url,published,official,content)

def scan_telegram(base,found):
    channel=urlsplit(base).path.removeprefix('/s/')
    seen=set();before=None;pages=0
    while pages<TELEGRAM_PAGES:
        url=base if before is None else base+'?'+urlencode({'before':before})
        parser=TelegramParser();parser.feed(fetch(url))
        posts=[p for p in parser.posts if re.fullmatch(r'https://t\.me/'+re.escape(channel)+r'/\d+',p['source'])]
        fresh=[p for p in posts if p['source'] not in seen]
        if not fresh:break
        pages+=1
        for post in fresh:
            seen.add(post['source'])
            for code in extract_codes(post['text']):record(found,code,post['source'],post['published_at'],base==CHANNEL,post['text'])
        ids=[int(p['source'].rsplit('/',1)[-1]) for p in posts]
        if not ids:break
        oldest=min(ids)
        if before is not None and oldest>=before:break
        before=oldest
    return {'source':base,'status':'ok','posts':len(seen),'pages':pages,
            'note':'Публічні дописи перевірено; історії Telegram і перевірка у грі недоступні'}

def scan_web(base,found):
    # Only follow links under the exact official news path. No arbitrary crawling.
    parser=PageParser();parser.feed(fetch(base))
    text=''.join(parser.text)
    for code in extract_codes(text):record(found,code,base,None,True,text)
    visited={base};candidates=[]
    if base==NEWS:
        for href in parser.links:
            link=urljoin(base,href).split('#')[0].split('?')[0]
            parts=urlsplit(link)
            if parts.scheme=='https' and parts.netloc=='standoff2.com' and parts.path.startswith('/news/') and link not in visited:
                visited.add(link);candidates.append(link)
    done=0;errors=0
    for link in candidates[:MAX_ARTICLES]:
        try:
            p=PageParser();p.feed(fetch(link));body=''.join(p.text)
            for code in extract_codes(body):record(found,code,link,None,True,body)
            done+=1
        except Exception:errors+=1
    return {'source':base,'status':'ok','posts':0,'articles_scanned':done,'articles_failed':errors,
            'note':'Пошук у доступному тексті сторінки та статей; вміст відео/історій не перевіряється'}

def scan():
    with SCAN_LOCK:
        found={};checks=[]
        for source in SOURCES:
            try:
                checks.append(scan_telegram(source,found) if source.startswith('https://t.me/s/') else scan_web(source,found))
            except Exception as exc:
                checks.append({'source':source,'status':'error','message':str(exc)[:180]})
        if not any(x['status']=='ok' for x in checks):
            with LOCK:
                if CACHE['at']:return CACHE['codes'],CACHE['sources']
            raise RuntimeError('Усі джерела недоступні')
        with LOCK:
            CACHE.update(at=time.time(),codes=list(found.values()),sources=checks)
            return CACHE['codes'],CACHE['sources']

def discover():
    with LOCK:
        if CACHE['at'] and time.time()-CACHE['at']<REFRESH_SECONDS:return CACHE['codes'],CACHE['sources']
    return scan()

def background_refresh():
    while True:
        try:scan()
        except Exception as exc:print('Refresh error:',str(exc),flush=True)
        time.sleep(REFRESH_SECONDS)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def cors(self):
        origin=self.headers.get('Origin','')
        if origin in ('https://dmytronikolaienko7.github.io','http://localhost:8000','http://127.0.0.1:8000'):
            self.send_header('Access-Control-Allow-Origin',origin);self.send_header('Vary','Origin')
        self.send_header('Access-Control-Allow-Methods','GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type')
    def do_OPTIONS(self):
        self.send_response(204);self.cors();self.end_headers()
    def send_json(self,payload,status=200):
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
        self.cors();self.end_headers();self.wfile.write(data)
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/health':return self.send_json({'status':'ok','version':'3.2'})
        if path=='/api/codes':
            try:
                codes,sources=discover()
                return self.send_json({'codes':codes,'source':CHANNEL,'sources':sources,
                                       'verified_in_game':False,'version':'3.2','updated_at':int(CACHE['at'])})
            except Exception as exc:return self.send_json({'error':'Не вдалося перевірити джерела: '+str(exc)},503)
        return super().do_GET()

if __name__=='__main__':
    port=int(os.getenv('PORT','8000'))
    print('PROMO BOT 3.2 listening on',port,flush=True)
    threading.Thread(target=background_refresh,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
