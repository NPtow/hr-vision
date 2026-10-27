import importlib.util,json,re
spec=importlib.util.spec_from_file_location('sr','scripts/school-research.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
fallback={'Школа Чацкого':'school@chatskii.ru','Аксиома — центр подготовки к школьным экзаменам':'zakaz@axioma-centrum.ru','Пять с плюсом — ЕГЭ Центр':'ege-c@yandex.ru','Школа Квентин':'vrnquentin@mail.ru','Школа им. Н. И. Лобачевского':'distantlobach@bk.ru','Студия математики «Пифагор»':'studiapifagor@gmail.com','ПифагорУМ':'shcolapifagorum@mail.ru','Единый центр подготовки к ЕГЭ и ОГЭ':'welcome@examscenter.ru'}
count=0
for path in (m.OUT/'companies').glob('*.json'):
 d=json.loads(path.read_text());channel_map={x['url'].lower():x for x in d['channels']};pages={}
 for doc in d['documents']:
  if doc['kind']!='telegramPost':continue
  parts=doc['url'].split('/');username=parts[-2];postid=parts[-1]
  if username not in pages:
   page=m.get('https://t.me/s/'+username);pages[username]=m.BeautifulSoup(page.get('html',''),'html.parser')
  node=pages[username].select_one('.tgme_widget_message[data-post="'+username+'/'+postid+'"] .tgme_widget_message_text')
  if node:
   copy=m.BeautifulSoup(str(node),'html.parser')
   for br in copy.find_all('br'):br.replace_with('\n')
   text=copy.get_text()
   doc['text']='\n'.join(re.sub(r'[ \t]+',' ',line).strip() for line in text.splitlines()).strip();count+=1
 for contact in d['contacts']:
  ch=channel_map.get(contact['url'].lower())
  if ch and contact['label']==contact['url']:contact['label']=ch.get('title') or contact['label']
 email=fallback.get(d['companyName'])
 if email and not any(x['url']=='mailto:'+email for x in d['contacts']):
  doc=next((x for x in d['documents'] if x['kind']=='website' and email in x['text']),None)
  if doc:
   i=doc['text'].index(email);quote=doc['text'][max(0,i-65):i+len(email)+70]
   d['contacts'].append({'url':'mailto:'+email,'label':email,'purpose':'general','context':quote,'sourceURL':doc['url'],'basis':'officialWebsite'})
 m.save(path,d)
print('Restored paragraph formatting for',count,'Telegram publications')
