import importlib.util,json,re,concurrent.futures
from urllib.parse import urlparse,urljoin,urldefrag
spec=importlib.util.spec_from_file_location('sr','scripts/school-research.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
schools=json.loads((m.OUT/'schools.json').read_text())
source={}
for p in (m.APP/'source-runs').glob('*.json'):
 for r in json.loads(p.read_text()).get('results',[]): source.setdefault(r['companyID'],[]).append(r)
def enrich(c):
 path=m.OUT/'companies'/f"{c['id']}.json"; d=json.loads(path.read_text()); candidates={x['url'].lower():x for x in d['channels']}; docs={x['id']:x for x in d['documents']}; host=m.hostname(c['website'])
 urls=list(dict.fromkeys(urldefrag(x['url'])[0] for x in d['documents'] if x['kind']=='website'))
 for r in source.get(c['id'],[]):
  if m.hostname(r['url'])==host and re.search(r'контакт|contact|партн|partner|cooperat|сотруднич|о нас|about',r['title']+' '+r['url'],re.I) and r['url'] not in urls: urls.append(r['url'])
 for url in list(urls[:6]):
  page=m.get(url)
  if 'html' not in page or page['html'].startswith('%PDF'): continue
  soup=m.BeautifulSoup(page['html'],'html.parser')
  for a in soup.select('a[href]'):
   href=urljoin(page['url'],a['href']); tg=m.telegram_url(href); context=m.nearby(a)
   if tg:
    # Shared footer labels are too broad to infer the recipient's function.
    role=m.purpose(context) if len(context)<150 else 'unknown'
    seed={'url':tg,'sourceURL':page['url'],'context':context,'basis':'officialWebsite','purpose':role}
    if tg.lower() not in candidates or candidates[tg.lower()]['basis']=='searchCandidate': candidates[tg.lower()]=seed
   if m.hostname(href)==host and re.search(r'контакт|сотруднич|партн|contact|partner|career',a.get_text(' ',strip=True)+' '+urlparse(href).path,re.I):
    href=urldefrag(href)[0]
    if href not in urls and not href.endswith('.pdf'): urls.append(href)
  for tag in soup(['script','style','noscript','svg','nav']): tag.decompose()
  body=soup.find('body') or soup
  doc=m.document(c,page['url'],m.compact(soup.title.get_text()) if soup.title else c['name'],body.get_text('\n',strip=True)[:90000],'website','Официальный сайт',official=m.hostname(page['url'])==host)
  docs[doc['id']]=doc
 # Read genuinely new contact pages discovered from visible links.
 for url in urls[6:9]:
  page=m.get(url)
  if 'html' not in page: continue
  soup=m.BeautifulSoup(page['html'],'html.parser')
  for a in soup.select('a[href]'):
   tg=m.telegram_url(urljoin(page['url'],a['href']))
   if tg: candidates.setdefault(tg.lower(),{'url':tg,'sourceURL':page['url'],'context':m.nearby(a),'basis':'officialWebsite','purpose':'unknown'})
  for tag in soup(['script','style','noscript','svg','nav']): tag.decompose()
  doc=m.document(c,page['url'],m.compact(soup.title.get_text()) if soup.title else c['name'],(soup.find('body') or soup).get_text('\n',strip=True)[:90000],'website','Официальный сайт',official=m.hostname(page['url'])==host); docs[doc['id']]=doc
 d['channels']=list(candidates.values()); d['documents']=list(docs.values()); d.pop('telegramComplete',None); m.save(path,d)
 print(c['name'],len(candidates),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as p: list(p.map(enrich,schools))
