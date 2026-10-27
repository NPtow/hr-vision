import importlib.util,json,re,concurrent.futures
from urllib.parse import urljoin
spec=importlib.util.spec_from_file_location('sr','scripts/school-research.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
rows=json.loads((m.OUT/'schools.json').read_text())
urls={'Точка Знаний':['https://tochka-school.ru/contact'], 'Профиматика':['https://profimatika.ru/materials','https://profimatika.ru/oferta1'], 'СМИТАП':['https://smitup.ru/pervaya-nauchnaya-revolyuciya-kak-izmenilas-nauka-i-myshlenie-cheloveka'], 'Сотка':['https://sotkaonline.ru/']}
for c in rows:
 if c['name'] not in urls:continue
 path=m.OUT/'companies'/f"{c['id']}.json";d=json.loads(path.read_text());chs={x['url'].lower():x for x in d['channels']};docs={x['id']:x for x in d['documents']}
 for url in urls[c['name']]:
  page=m.get(url);d['checks'].append({'url':url,'status':page.get('error','read')})
  if 'html' not in page: print(c['name'],page.get('error'));continue
  html=page['html']
  if html.count('Ð')>5:
   try: html=html.encode('latin1').decode('utf8')
   except Exception:pass
  soup=m.BeautifulSoup(html,'html.parser')
  for a in soup.select('a[href]'):
   tg=m.telegram_url(urljoin(page['url'],a['href']))
   if tg:
    ctx=m.nearby(a);chs[tg.lower()]={'url':tg,'sourceURL':url,'context':ctx,'basis':'officialWebsite','purpose':m.purpose(ctx) if len(ctx)<150 else 'unknown'}
  for tag in soup.select('script,style,noscript,svg,nav,header,form,[hidden],[aria-hidden="true"]'):tag.decompose()
  doc=m.document(c,page['url'],soup.title.get_text() if soup.title else c['name'],(soup.find('body') or soup).get_text('\n',strip=True),'website','Официальный сайт',official=True);docs[doc['id']]=doc
 d['channels']=list(chs.values());d['documents']=list(docs.values());d.pop('telegramComplete',None);m.save(path,d); print(c['name'],len(chs))
