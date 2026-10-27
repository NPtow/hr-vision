"""Review source-bound school identities, refresh text extraction, and add labelled contacts."""
import importlib.util,json,re,concurrent.futures
from urllib.parse import urlparse,urljoin,urldefrag
spec=importlib.util.spec_from_file_location('sr','scripts/school-research.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
schools=json.loads((m.OUT/'schools.json').read_text())
# These candidates have matching educational branding AND reciprocal company links.
identities={'insperia':'insperia_rus','NeoFamily':'neofamily_ege','Умскул':'umschool_official','РЦДП — Региональный центр довузовской подготовки':'rcdpvrn'}
manual_contacts={
 'Вебиум':[('https://t.me/webium_influence','cooperation','@webium_influence — по вопросам инфлюенса и амбассадорства','https://webium.ru/'),('https://t.me/webium_hr','hiring','@webium_hr — тг канал с вакансиями','https://webium.ru/'),('mailto:pr@webium.ru','cooperation','pr@webium.ru — PR-вопросы и партнерство','https://webium.ru/')],
 'Единый центр подготовки к ЕГЭ и ОГЭ':[('mailto:marketing@examscenter.ru','cooperation','По вопросам сотрудничества: marketing@examscenter.ru','https://examscenter.ru/contacts'),('mailto:hr@examscenter.ru','hiring','Для резюме: hr@examscenter.ru','https://examscenter.ru/contacts')],
 'IBLS':[('mailto:familyclass@iblschool.ru','cooperation','Школы-партнёры: familyclass@iblschool.ru','https://ibls.one/kontakty')],
 'Онлайн-школа №1':[('mailto:b2b@onlineschool-1.ru','cooperation','b2b@onlineschool-1.ru — отдел по работе с юр.лицами',None),('mailto:pr@onlineschool-1.ru','cooperation','pr@onlineschool-1.ru — отдел по работе со СМИ',None)],
 'Школково':[('mailto:pr@shkolkovo.online','cooperation','Если вы журналист или блогер, то напишите на почту нашему специалисту по связям с общественностью: pr@shkolkovo.online','https://3.shkolkovo.online/for-media'),('mailto:partner@shkolkovo.online','cooperation','Партнёрская программа: partner@shkolkovo.online','https://3.shkolkovo.online/partner_agreement')],
 'ИнтернетУрок':[('mailto:pr@interneturok.ru','cooperation','Пресс-служба — для запросов СМИ: pr@interneturok.ru',None)],
 'Годограф':[('mailto:hr@godege.ru','hiring','Если у вас есть вопросы, направляйте их на почту hr@godege.ru',None)],
 'ЕГЭ-Студия':[('mailto:Admin@EGE-Study.ru','hiring','Адрес для резюме: Admin@EGE-Study.ru',None)]
}
for c in schools:
 path=m.OUT/'companies'/f"{c['id']}.json";d=json.loads(path.read_text())
 # Repair mis-decoded captures; remove forms/hidden UI examples from saved page text.
 for doc in d['documents']:
  if doc['kind']!='website':continue
  page=m.get(doc['url']);html=page.get('html','')
  if html.count('Ð')>5:
   try:html=html.encode('latin1').decode('utf8')
   except (UnicodeEncodeError,UnicodeDecodeError):pass
  soup=m.BeautifulSoup(html,'html.parser')
  for tag in soup.select('script,style,noscript,svg,nav,header,form,[hidden],[aria-hidden="true"]'):tag.decompose()
  for tag in list(soup.find_all(style=True)):
   if tag.attrs and re.search(r'display\s*:\s*none',tag['style'],re.I):tag.decompose()
  doc['text']=(soup.find('body') or soup).get_text('\n',strip=True)[:90000]
 # Exact channels verified from company branding and links in their own posts.
 user=identities.get(c['name'])
 if user:
  for ch in d['channels']:
   if ch['url'].rsplit('/',1)[-1]==user:
    p=m.get('https://t.me/s/'+user); soup=m.BeautifulSoup(p.get('html',''),'html.parser')
    links=[a['href'] for a in soup.select('.tgme_widget_message_text a[href]') if m.hostname(a['href'])==m.hostname(c['website']) or m.hostname(a['href']).endswith('.'+m.hostname(c['website']))]
    if links: ch.update(basis='reviewedIdentity',context='Название и тематика канала совпадают; в его публикации ссылка на сайт школы: '+links[0],sourceURL=ch['url']); d.pop('telegramComplete',None)
 # Missing Unilogia identity: actual channel found in the paid website's opened cards.
 if c['name']=='Юнилогия (Lomonosov School)':
  d['channels'].append({'url':'https://t.me/unilogia','sourceURL':'https://t.me/unilogia/6720','context':'Канал Юнилогия; сообщение от команды школы и учебная платформа Lomonosov School. Карточка раскрыта в Telemetr.','basis':'reviewedIdentity','purpose':'unknown'})
  d.pop('telegramComplete',None)
 for url,role,quote,source in manual_contacts.get(c['name'],[]):
  if source is None:
   source=next((doc['url'] for doc in d['documents'] if doc['kind']=='website' and url.removeprefix('mailto:') in doc['text']),None)
  if source:
   d['contacts']=[x for x in d['contacts'] if x['url'].lower()!=url.lower()]
   d['contacts'].append({'url':url,'label':url.removeprefix('mailto:'),'purpose':role,'context':quote,'sourceURL':source,'basis':'officialWebsite','reviewed':True})
 # A broad website footer must not assign the wrong purpose to each channel.
 for ch in d['channels']:
  if len(ch.get('context',''))>150 and ch['basis']=='officialWebsite':ch['purpose']='unknown'
 d['channels']=list({x['url'].lower():x for x in d['channels'] if m.telegram_url(x['url'])}.values())
 # A Telegram placeholder saying only 'you can contact' is not an active public page.
 for ch in d['channels']:
  if ch.get('bio','').startswith('If you have\nTelegram'): ch['status']='noPublicPreview'
 m.save(path,d)
print('identity and purpose review saved')
