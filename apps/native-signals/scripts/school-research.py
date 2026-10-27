#!/usr/bin/env python3
"""Collect public school websites and Telegram previews, with source context.

No authentication, messages, private chats or Telemetr endpoints. Captures are
staged for review; --publish copies only validated public research to the app.
"""
import argparse, concurrent.futures, datetime, hashlib, json, re, time
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/schools-research-2026-10-12'
APP = Path.home() / 'Library/Application Support/TG Signals'
NOW = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
USERNAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{3,31}$')
EXCLUDED = {'share', 'addstickers', 'addemoji', 'proxy', 'socks', 'login', 'iv', 'joinchat', 'addlist', 'boost', 'c', 's'}
ROLES = [('cooperation', r'сотруднич|партн[её]р|реклам|сми|пресс|cooperat|partnership'),
         ('hiring', r'ваканси|работ[аеуы] у нас|карьер|hr\b|рекрутер'),
         ('support', r'поддерж|технич|support|помощ'),
         ('enrollment', r'запис|консульт|подбор|пробн|поступить|обучени|заявк')]

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)

def shortid(value): return hashlib.sha256(value.encode()).hexdigest()[:20]
def compact(text): return re.sub(r'\s+', ' ', text).strip()
def hostname(url): return (urlparse(url).hostname or '').removeprefix('www.')
def telegram_url(url):
    p=urlparse(url if '://' in url else 'https://' + url)
    if p.hostname not in ('t.me', 'telegram.me', 'www.t.me'): return None
    parts=p.path.strip('/').split('/')
    if parts and parts[0]=='s': parts=parts[1:]
    name=parts[0] if parts else ''
    if name.startswith('+') and re.fullmatch(r'\+[A-Za-z0-9_-]{8,}',name): return 'https://t.me/'+name
    if name=='joinchat' and len(parts)>1: return 'https://t.me/+'+parts[1]
    if not USERNAME.fullmatch(name) or name.lower() in EXCLUDED: return None
    return 'https://t.me/' + name

def get(url):
    key=shortid(url); cache=OUT/'pages'/f'{key}.json'
    if cache.exists(): return json.loads(cache.read_text())
    result={'requestedURL':url,'retrievedAt':NOW()}
    try:
        r=requests.get(url,timeout=(8,16),headers={'User-Agent':'Mozilla/5.0 (compatible; CompanyResearch/1.0)'})
        r.raise_for_status()
        result.update(url=r.url,status=r.status_code,contentType=r.headers.get('Content-Type',''))
        if 'html' in r.headers.get('Content-Type','') and not r.content.startswith(b'%PDF'):
            result['html']=r.text[:2_000_000]
        else: result['error']='NonHTML'
    except requests.RequestException as e:
        result.update(error=type(e).__name__)
    save(cache,result)
    return result

def purpose(context):
    for name,pattern in ROLES:
        if re.search(pattern,context,re.I): return name
    return 'unknown'

def contact_context(container, anchor):
    # A labelled contact must be linked to its own line, not a different bio line.
    lines=[compact(x) for x in container.get_text('\n',strip=True).splitlines() if compact(x)]
    label=compact(anchor.get_text(' ',strip=True))
    for i,line in enumerate(lines):
        if label and label in line:
            previous=lines[i-1] if i else ''
            return (previous+' '+line).strip() if len(line)<80 and '@' not in previous and 't.me/' not in previous else line
    return label

def nearby(anchor):
    label=compact(anchor.get_text(' ',strip=True))
    current=anchor
    for _ in range(4):
        if not current.parent: break
        current=current.parent
        text=compact(current.get_text(' ',strip=True))
        if len(text)>450: break
        if len(text)>max(12,len(label)+4): return text
    return label

def document(company,url,title,text,kind,provider,published=None,quality='fullText',official=False):
    return {'id':'public:'+shortid(company['id']+url),'companyID':company['id'],'url':url,'title':title,
            'text':text,'kind':kind,'provider':provider,'retrievedAt':NOW(),'publishedAt':published,
            'quality':quality,'official':official}

def crawl_school(c):
    dest=OUT/'companies'/f"{c['id']}.json"
    if dest.exists(): return json.loads(dest.read_text())
    value={'version':1,'companyID':c['id'],'companyName':c['name'],'searchedAt':NOW(),
           'documents':[],'channels':[],'contacts':[],'checks':[]}
    candidates={}
    todo=[c['website']]; seen=set(); officialHost=hostname(c['website'])
    for url in todo:
        if url in seen or len(seen)>=4: continue
        seen.add(url); page=get(url)
        value['checks'].append({'url':url,'status':page.get('error','read')})
        if 'html' not in page: continue
        soup=BeautifulSoup(page['html'],'html.parser')
        title=compact(soup.title.get_text()) if soup.title else c['name']
        links=list(soup.select('a[href]'))
        for a in links:
            href=urljoin(page['url'],a.get('href',''))
            tg=telegram_url(href)
            context=nearby(a)
            if tg:
                entry={'url':tg,'sourceURL':page['url'],'context':context,'basis':'officialWebsite','purpose':purpose(context)}
                old=candidates.get(tg.lower())
                if not old or (old['purpose']=='unknown' and entry['purpose']!='unknown'): candidates[tg.lower()]=entry
            if hostname(href)==officialHost and re.search(r'контакт|сотруднич|партн[её]р|ваканси|contact|partner|career', a.get_text(' ',strip=True)+' '+urlparse(href).path,re.I):
                if len(todo)<6 and href not in todo: todo.append(href)
        for tag in soup(['script','style','noscript','svg','nav']): tag.decompose()
        body=soup.find('body') or soup
        text=body.get_text('\n',strip=True)
        value['documents'].append(document(c,page['url'],title,text[:90000],'website','Официальный сайт',official=hostname(page['url'])==officialHost))
    # Search-index candidates remain unverified until they link back to this domain.
    for entry in existing_candidates(c):
        candidates.setdefault(entry['url'].lower(),entry)
    for seed in candidates.values(): value['channels'].append(seed)
    save(dest,value)
    print(json.dumps({'company':c['name'],'pages':len(value['documents']),'telegramCandidates':len(candidates)},ensure_ascii=False),flush=True)
    return value

SOURCE_ROWS={}
def existing_candidates(c):
    result=[]
    for row in SOURCE_ROWS.get(c['id'],[]):
        raw=row.get('url',''); tg=telegram_url(raw)
        parsed=urlparse(raw); parts=unquote(parsed.path).strip('/').split('/')
        if not tg and ('tgstat.' in (parsed.hostname or '') or parsed.hostname=='telemetr.me'):
            if len(parts)>1 and parts[-2] in ('channel','content'):
                tg=telegram_url('https://t.me/'+parts[-1].lstrip('@'))
        if tg: result.append({'url':tg,'sourceURL':row['url'],'context':row.get('title','')+' '+row.get('snippet',''),'basis':'searchCandidate','purpose':'unknown'})
    return result

def profile_research(c):
    path=OUT/'companies'/f"{c['id']}.json"; value=json.loads(path.read_text())
    if value.get('telegramComplete'): return value
    candidates={x['url'].lower():x for x in value['channels'] if telegram_url(x['url'])}
    for entry in existing_candidates(c): candidates.setdefault(entry['url'].lower(),entry)
    channels=[]; contacts=[x for x in value['contacts'] if x.get('reviewed')]
    for seed in candidates.values():
        url=seed['url']; username=url.rsplit('/',1)[-1]
        preview=url if username.startswith('+') else 'https://t.me/s/'+username
        page=get(preview)
        value['checks'].append({'url':preview,'status':page.get('error','read')})
        if 'html' not in page:
            channels.append(dict(seed,status='unavailable')); continue
        soup=BeautifulSoup(page['html'],'html.parser')
        desc=soup.select_one('.tgme_channel_info_description') or soup.select_one('.tgme_page_description')
        title=soup.select_one('.tgme_channel_info_header_title') or soup.select_one('.tgme_page_title')
        bio=desc.get_text('\n',strip=True) if desc else ''
        name=title.get_text(' ',strip=True) if title else username
        posts=soup.select('.tgme_widget_message[data-post]')
        official=seed['basis'] in ('officialWebsite','reviewedIdentity','officialChannelPost') or hostname(c['website']) in bio.lower()
        basis=seed['basis'] if seed['basis'] in ('officialWebsite','reviewedIdentity','officialChannelPost') else 'domainInBio' if official else 'searchCandidate'
        channel=dict(seed,title=name,bio=bio,status='read' if desc or posts else 'noPublicPreview',basis=basis,
                     accountType='channel' if posts or soup.select_one('.tgme_channel_info') or (soup.select_one('.tgme_page_extra') and 'subscriber' in soup.select_one('.tgme_page_extra').get_text()) else 'chat' if username.startswith('+') and not username[1:].isdigit() else 'bot' if username.lower().endswith('bot') else 'account',retrievedAt=page['retrievedAt'])
        channels.append(channel)
        if not official: continue
        value['documents'].append(document(c,url,name,bio,'telegramProfile','Telegram · открытая страница',official=True))
        if seed['purpose'] in ('cooperation','hiring','support','enrollment'):
            contacts.append({'url':url,'label':name,'purpose':seed['purpose'],'context':seed['context'],'sourceURL':seed['sourceURL'],'basis':basis})
        if desc:
            for a in desc.select('a[href]'):
                contact=telegram_url(a['href'])
                if not contact or contact==url: continue
                # Bio is short and published by the source. Preserve entire wording.
                line=contact_context(desc,a)
                role=purpose(line)
                if role!='unknown': contacts.append({'url':contact,'label':a.get_text(' ',strip=True) or contact,'purpose':role,'context':line,'sourceURL':url,'basis':basis})
        for post in posts:
            textnode=post.select_one('.tgme_widget_message_text')
            if not textnode: continue
            text=textnode.get_text('\n',strip=True)
            if not text: continue
            ref=post.get('data-post'); link='https://t.me/'+ref
            dt=post.select_one('time[datetime]')
            value['documents'].append(document(c,link,name+' · публикация',text,'telegramPost','Telegram · открытый пост',dt.get('datetime') if dt else None,official=True))
            for a in textnode.select('a[href]'):
                contact=telegram_url(a['href'])
                if not contact or contact==url: continue
                context=nearby(a); role=purpose(context)
                if role in ('cooperation','hiring'):
                    contacts.append({'url':contact,'label':a.get_text(' ',strip=True) or contact,'purpose':role,'context':context,'sourceURL':link,'basis':'officialChannelPost'})
    value['channels']=channels
    unique={}
    for contact in contacts: unique[(contact['url'].lower(),contact['purpose'],contact['sourceURL'])]=contact
    value['contacts']=list(unique.values()); value['telegramComplete']=True
    value['documents']=list({x['id']:x for x in value['documents']}.values())
    save(path,value)
    print(json.dumps({'company':c['name'],'readChannels':sum(x.get('status')=='read' for x in channels),'documents':len(value['documents']),'contacts':len(contacts)},ensure_ascii=False),flush=True)
    return value

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('phase',choices=['websites','telegram','report']); args=parser.parse_args()
    schools=[x for x in json.loads((ROOT/'Sources/SignalsCore/Resources/discovered-companies.json').read_text()) if x['segment']=='Школьное образование и экзамены']
    for path in (APP/'source-runs').glob('*.json'):
        for row in json.loads(path.read_text()).get('results',[]): SOURCE_ROWS.setdefault(row['companyID'],[]).append(row)
    save(OUT/'schools.json',schools)
    if args.phase in ('websites','telegram'):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6 if args.phase=='websites' else 3) as pool:
            list(pool.map(crawl_school if args.phase=='websites' else profile_research,schools))
    rows=[json.loads(p.read_text()) for p in (OUT/'companies').glob('*.json')]
    summary={'companies':len(rows),'documents':sum(len(x['documents']) for x in rows),'channels':sum(len(x['channels']) for x in rows),'contacts':sum(len(x['contacts']) for x in rows),'generatedAt':NOW()}
    save(OUT/'summary.json',summary); print(json.dumps(summary))
if __name__=='__main__': main()
