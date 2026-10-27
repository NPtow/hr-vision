import importlib.util,json,re,hashlib,shutil,csv,datetime
from urllib.parse import urlparse,urldefrag
spec=importlib.util.spec_from_file_location('sr','scripts/school-research.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
records=[]
for path in (m.OUT/'companies').glob('*.json'):
 d=json.loads(path.read_text());excluded=set()
 for ch in d['channels']:
  if ('Спросить про учебу' in ch['context'] and d['companyName']=='NeoFamily') or 'РАЗРАБОТКА САЙТА' in ch['context']:
   ch['basis']='excludedReference';excluded.add(ch['url']);continue
  if ch['url'].split('/')[-1].startswith('+'):
   page=m.get(ch['url']);soup=m.BeautifulSoup(page.get('html',''),'html.parser');extra=soup.select_one('.tgme_page_extra')
   ch['accountType']='account' if ch['url'].split('/')[-1][1:].isdigit() else 'channel' if extra and 'subscriber' in extra.get_text() else 'chat'
  if ch['context'].count('Ð')>3:
   try:ch['context']=ch['context'].encode('latin1').decode('utf8')
   except Exception:pass
 # Exclude student testimonials and web vendors from company contacts and channel biographies.
 d['documents']=[x for x in d['documents'] if not any(x['url']==url or x['url'].startswith(url+'/') for url in excluded)]
 d['contacts']=[x for x in d['contacts'] if x['url'] not in excluded]
 # Generic accounts linked by school websites have no inferred cooperation remit.
 for ch in d['channels']:
  if ch['basis'] in ('officialWebsite','reviewedIdentity','domainInBio','officialChannelPost') and ch.get('accountType')=='account':
   if not any(x['url']==ch['url'] for x in d['contacts']):
    d['contacts'].append({'url':ch['url'],'label':ch.get('title') or ch['url'],'purpose':'general','context':ch['context'] or ch.get('bio',''),'sourceURL':ch['sourceURL'],'basis':ch['basis']})
 # Named founder backed by the official about page; not asserted to handle cooperation.
 if d['companyName']=='Онлайн Гимназия №1':
  d['contacts']=[x for x in d['contacts'] if x['url']!='https://t.me/alexander_sakmarov']
  d['contacts'].append({'url':'https://t.me/alexander_sakmarov','label':'Александр Сакмаров — основатель и генеральный директор','purpose':'general','context':'Сакмаров Александр Викторович. Генеральный директор, автор идеи и основатель ОНЛАЙН ГИМНАЗИИ №1. Телеграм канал: https://t.me/alexander_sakmarov','sourceURL':'https://og1.ru/','basis':'officialWebsite'})
 # Keep one snapshot of each URL, excluding fragment-only repeated pages.
 docs={}
 for doc in d['documents']:
  url=urldefrag(doc['url'])[0];key=(url,doc['kind']);doc['url']=url
  if key not in docs or len(doc['text'])>len(docs[key]['text']):docs[key]=doc
 d['documents']=list(docs.values())
 d['checks']=list({(x['url'],x['status']):x for x in d['checks']}.values())
 d['notes']=['Проверены сайт, поисковая выдача и открытые Telegram-страницы. Закрытые чаты не читались. Отсутствие контакта в этих источниках не означает, что его нет.','Telegram: сохранены доступные тексты последних публикаций открытых каналов. Медиа, комментарии и вся история не загружены.']
 m.save(path,d);records.append(d)
summary={'schools':len(records),'withTelegram':sum(any(x['basis'] in ('officialWebsite','reviewedIdentity','domainInBio','officialChannelPost') for x in r['channels']) for r in records),'verifiedTelegramLinks':sum(sum(x['basis'] in ('officialWebsite','reviewedIdentity','domainInBio','officialChannelPost') for x in r['channels']) for r in records),'documents':sum(len(r['documents']) for r in records),'cooperationCompanies':sum(any(c['purpose']=='cooperation' for c in r['contacts']) for r in records),'cooperationContacts':sum(len({c['url'] for c in r['contacts'] if c['purpose']=='cooperation'}) for r in records),'telegramCooperation':sum(len({c['url'] for c in r['contacts'] if c['purpose']=='cooperation' and c['url'].startswith('https://t.me/')}) for r in records)}
m.save(m.OUT/'summary.json',summary)
with (m.OUT/'schools-coverage.csv').open('w') as f:
 w=csv.writer(f);w.writerow(['Школа','Telegram с подтверждённым источником','Сотрудничество','Материалы','Ограничения'])
 for r in sorted(records,key=lambda r:r['companyName']):w.writerow([r['companyName'],'; '.join(c['url'] for c in r['channels'] if c['basis'] in ('officialWebsite','reviewedIdentity','domainInBio','officialChannelPost')) or 'Не найден в просмотренных источниках','; '.join(c['url'] for c in r['contacts'] if c['purpose']=='cooperation') or 'Отдельный контакт не найден',len(r['documents']),r['notes'][0]])
print(json.dumps(summary,ensure_ascii=False))
# No record is installed until the Swift decoder/projection audit succeeds.
