import importlib.util,json,re,hashlib
from pathlib import Path
spec=importlib.util.spec_from_file_location('sr','scripts/school-research.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
rows=json.loads((m.OUT/'schools.json').read_text())
# Sources opened directly during review. None of these destinations was messaged.
for c in rows:
 path=m.OUT/'companies'/f"{c['id']}.json";d=json.loads(path.read_text())
 if c['name']=='Точка Знаний':
  for url,role,quote in [('https://t.me/tochka_school_sales_bot','enrollment','Для будущих клиентов'),('https://t.me/matematik_servicebot','support','Для действующих клиентов')]:
   d['channels'].append({'url':url,'sourceURL':'https://tochka-school.ru/contact','context':quote,'basis':'officialWebsite','purpose':role})
   d['contacts'].append({'url':url,'label':'Точка Знаний — '+('запись на обучение' if role=='enrollment' else 'отдел сервиса'),'purpose':role,'context':quote,'sourceURL':'https://tochka-school.ru/contact','basis':'officialWebsite','reviewed':True})
  d['checks'].append({'url':'https://tochka-school.ru/contact','status':'read via search browser'})
  d['documents'].append(m.document(c,'https://tochka-school.ru/contact','Контакты Точки Знаний','Для будущих клиентов: Telegram — https://t.me/tochka_school_sales_bot\nДля действующих клиентов: Telegram — https://t.me/matematik_servicebot','website','Сайт школы · раздел контактов',quality='excerpt',official=True))
  d.pop('telegramComplete',None)
 if c['name']=='Профиматика':
  d['channels'].append({'url':'https://t.me/profimatika_for_teachers','sourceURL':'https://t.me/profimatika_for_teachers','context':'В описании канала указан сайт https://profimatika.ru/materials и чат для преподавателей https://t.me/chat_pedagogi_profimatika','basis':'domainInBio','purpose':'unknown'})
  d['channels'].append({'url':'https://t.me/chat_pedagogi_profimatika','sourceURL':'https://t.me/profimatika_for_teachers','context':'Чат для преподавателей','basis':'officialChannelPost','purpose':'unknown','accountType':'chat'})
  d.pop('telegramComplete',None)
 m.save(path,d)
print('verified source additions ready')
