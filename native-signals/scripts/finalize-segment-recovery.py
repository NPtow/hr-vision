#!/usr/bin/env python3
"""Combine reviewed recovery reads and explicitly limited indexed excerpts."""
import pathlib,json,hashlib,datetime,importlib.util,re
ROOT=pathlib.Path(__file__).resolve().parents[1];RUN=ROOT/'output/segments-2026-10-14';OUT=RUN/'recovery'
spec=importlib.util.spec_from_file_location('public_collector',ROOT/'scripts/segment-public-research.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
cs={x['id']:x for x in json.loads((RUN/'companies.json').read_text())}
targets=[x for x in cs.values() if not json.loads((RUN/'web/companies'/(x['id']+'.json')).read_text())['documents']]
def record(cid):
    p=OUT/'companies'/(cid+'.json')
    if p.exists():return json.loads(p.read_text())
    c=cs[cid]
    return {'version':1,'companyID':cid,'companyName':c['name'],'searchedAt':m.now(),'documents':[],'channels':[],'contacts':[],'checks':[],'notes':[],'outboundLinks':[],'jobPostings':[],'collector':'segment-recovery/1','segment':c['segment']}
records={c['id']:record(c['id']) for c in targets}
for x in json.loads((OUT/'snippets.json').read_text()):
    r=records[x['companyID']];rid='indexed-'+m.digest(x['url']);retrieved=m.now()
    receipt={'receiptID':rid,'requestedURL':x['url'],'url':x['url'],'retrievedAt':retrieved,'acquisition':'publicWebSearchOrPublicWebOpen','indexCrawlLabel':x.get('crawlLabel'),'excerpt':x['text'],'excerptSHA256':hashlib.sha256(x['text'].encode()).hexdigest(),'excerptMayJoinSeparateVisibleLines':True,'isCompletePage':False,'notes':'Первичный публичный URL прочитан через поисковый индекс/публичный Web reader. Текущий полный HTML недоступен локальному сборщику; его ограничения не обходились.'}
    m.save(OUT/'pages'/(rid+'.json'),receipt)
    doc={'id':'public:'+m.digest(x['companyID']+x['url']),'companyID':x['companyID'],'url':x['url'],'title':x['title'],'text':x['text'],'kind':x.get('kind','website'),'provider':'Публичный веб-поиск · фрагмент первичного источника','retrievedAt':retrieved,'publishedAt':None,'quality':'snippet','official':True,'receiptID':rid,'acquisition':'indexedPrimarySource','currentPageNotVerified':True}
    if not any(d['url']==doc['url'] and d.get('quality')=='fullText' for d in r['documents']):r['documents']=[d for d in r['documents'] if d['url']!=doc['url']]+[doc]
    for c in x['contacts']:
        item={**c,'label':c.get('label',c['url'].replace('mailto:','').replace('tel:','')),'sourceURL':x['url'],'basis':x.get('basis','officialWebsite'),'receiptID':rid,'acquisition':'indexedPrimarySource','currentPageNotVerified':True,'affiliation':'publishedWorkContact','personIdentityVerified':False}
        if 'linkEvidence' not in item:item['linkEvidence']={'method':'indexedVisibleText','sourceURL':x['url']}
        else:item['linkEvidence']['sourceURL']=x['url']
        r['contacts'].append(item)
    for ch in x.get('channels',[]):r['channels'].append({**ch,'sourceURL':x['url'],'retrievedAt':retrieved,'receiptID':rid,'acquisition':'indexedPrimarySource','currentPageNotVerified':True})
    r['checks'].append({'url':x['url'],'status':'indexedExcerptOnly','receiptID':rid})
    r['notes'].extend(x.get('notes',[]))
    if not any('Индексированные' in n for n in r['notes']):r['notes'].append('Индексированные фрагменты сохранены отдельно от полного текста. Дата загрузки — дата чтения индекса, актуальность адресов и доставки не проверена; ограничения текущего сайта не обходились.')

# The single JS-only site was read through the public rendered page; no login or private state.
cid='discovery-netz-school';r=records[cid];url='https://netz.school/';rid='browser-'+m.digest(url);retrieved=m.now()
excerpt='Юлия Попович — основатель школы NETZ\nБесплатная диагностика уровня\nНапиши нам напрямую в Telegram\nКОНТАКТЫ\nTelegram\nInstagram\ninfo@netz.schule'
links=[{'href':'https://t.me/netzschule','anchorText':'Напиши нам напрямую в Telegram'},{'href':'https://www.instagram.com/netzschule/','anchorText':'Instagram'},{'href':'mailto:info@netz.schule','anchorText':'info@netz.schule'}]
m.save(OUT/'pages'/(rid+'.json'),{'receiptID':rid,'requestedURL':url,'url':url,'retrievedAt':retrieved,'acquisition':'readOnlyRenderedBrowserDOM','title':'Netz — онлайн-школа немецкого языка A1–C2','excerpt':excerpt,'excerptSHA256':hashlib.sha256(excerpt.encode()).hexdigest(),'links':links,'isCompletePage':False,'selection':'Founder heading, contact form link, footer business addresses. No form was submitted; no hidden state accessed.'})
r['documents']=[d for d in r['documents'] if d['url']!=url]
r['documents'].append({'id':'public:'+m.digest(cid+url),'companyID':cid,'url':url,'title':'Netz — основатель и рабочие контакты','text':excerpt,'kind':'website','provider':'Официальный сайт · чтение видимого браузера','retrievedAt':retrieved,'publishedAt':None,'quality':'partialText','official':True,'receiptID':rid,'acquisition':'readOnlyRenderedBrowserDOM'})
for link,purpose in zip(links,['enrollment','general','general']):
    r['contacts'].append({'url':link['href'],'label':link['anchorText'],'purpose':purpose,'context':link['anchorText'],'sourceURL':url,'basis':'officialWebsite','receiptID':rid,'affiliation':'publishedWorkContact','personIdentityVerified':False,'linkEvidence':{**link,'sourceURL':url,'method':'readOnlyRenderedBrowserDOM'}})
r['channels'].append({'url':'https://t.me/netzschule','sourceURL':url,'context':'Напиши нам напрямую в Telegram','basis':'officialWebsite','purpose':'enrollment','accountType':'account','status':'linkedFromOfficialWebsite','retrievedAt':retrieved,'receiptID':rid,'linkEvidence':{**links[0],'sourceURL':url}})
r['notes'].append('JavaScript-страница прочитана в браузере без входа. Сохранены выбранные видимые блоки, не полный текст страницы. Telegram — опубликованный вход для записи, не установленная личность основателя.')

# Do not import an alternative brand's content before identity is proven.
home=records['discovery-home-art-school-ru']
if home['documents']:m.save(OUT/'quarantine'/'discovery-home-art-school-ru.json',home)
home['documents']=[];home['contacts']=[];home['channels']=[];home['outboundLinks']=[]
home['notes']=['Найден homeart-mk.ru с тем же брендом и совпадающими описаниями курсов, но прямая связь с исходным home-art-school.ru не подтверждена. Полные альтернативные страницы оставлены в quarantine; в подтверждённое покрытие не включены.']
records['discovery-superchildren-ru']['notes'].append('Индексированный официальный сайт подтверждает проект Ирины Ишковой. Единственный найденный адрес в policy предназначен для вопросов о персональных данных/отписки; не включён как коммерческий контакт. Случайные одноимённые Telegram-профили не добавлялись.')

# University-wide surfaces are not school-specific contacts.
sseu=records['discovery-mba-sseu-ru'];excluded=sseu.get('excludedContacts',[])
for c in sseu['contacts']:
    if c['url'] in ('mailto:ecun@sseu.ru','https://vk.com/sfsseu'):excluded.append({**c,'reason':'universityWideNotBusinessSchool'})
sseu['contacts']=[c for c in sseu['contacts'] if c['url'] not in ('mailto:ecun@sseu.ru','https://vk.com/sfsseu')]
sseu['excludedContacts']=excluded
sseu['outboundLinks']=[x for x in sseu.get('outboundLinks',[]) if x['url']!='https://vk.com/sfsseu']
for link in sseu['outboundLinks']:
    if link['url']=='https://vk.com/club77206275':
        link['affiliation']='officiallyLinked'
        link['identityReview']='School-specific contacts section explicitly publishes this community; university footer community excluded.'
        if link.get('context','').startswith('ВКонтакте:'):
            sseu['contacts'].append({'url':link['url'],'label':'Сообщество Высшей школы менеджмента СГЭУ','purpose':'channel','context':link['context'],'sourceURL':link['sourceURL'],'basis':'officialWebsite','receiptID':link['receiptID'],'affiliation':'officiallyLinked','personIdentityVerified':False,'linkEvidence':{'href':link['url'],'anchorText':link['linkText'],'sourceURL':link['sourceURL']}})

# MGIMO's visible phone and tel: target disagree. Prefer the visibly published
# number for the contact, retaining the original target as conflicting evidence.
mgimo=records['discovery-mba-mgimo-ru'];phone_conflicts=mgimo.get('excludedContactHrefTargets',[]);repaired=[]
for c in mgimo['contacts']:
    if c['url'].startswith('tel:'):
        match=re.search(r'\+7[\s(]*495[)\s-]*225[\s-]*40[\s-]*(?:45|88)',c['context'])
        if match:
            visible='tel:+'+re.sub(r'\D','',match.group())
            if visible!=c['url']:
                phone_conflicts.append({**c,'reason':'visiblePhoneDoesNotMatchHref'})
                repaired.append({**c,'url':visible,'label':match.group(),'context':match.group(),'addressTextMismatch':True,'linkEvidence':{'method':'visibleText','sourceURL':c['sourceURL'],'conflictingHref':c['url']}})
                continue
    repaired.append(c)
mgimo['candidateContacts']=list({(c['url'],c['sourceURL'],c['context']):c for c in [*mgimo.get('candidateContacts',[]),*[{**c,'basis':'addressConflict','verificationState':'candidate'} for c in repaired if c.get('addressTextMismatch')]]}.values())
mgimo['contacts']=[c for c in repaired if not c.get('addressTextMismatch')];mgimo['excludedContactHrefTargets']=phone_conflicts
mgimo['notes'].append('На странице МГИМО значения tel: не совпадают с видимыми телефонами. Видимые +7 495 225-40-45 и +7 495 225-40-88 сохранены как кандидаты, конфликтующие href отдельно. В опубликованные рабочие контакты эти телефоны не добавлены; остаётся почта business@inno.mgimo.ru.')

def key(url):
    if url.startswith('tel:'):
        v=re.sub(r'\D','',url)
        if len(v)==11 and v[0]=='8':v='7'+v[1:]
        return 'tel:'+v
    if 'whatsapp.com/send' in url or 'wa.me/' in url:
        from urllib.parse import urlparse,parse_qs
        p=urlparse(url);v=parse_qs(p.query).get('phone',[p.path.strip('/')])[0]
        return 'whatsapp:'+re.sub(r'\D','',v)
    return url.lower().rstrip('/')
summary=[]
for cid,r in records.items():
    r['contacts']=list({(key(x['url']),x['sourceURL'],x['purpose']):x for x in r['contacts']}.values())
    r['channels']=list({(x['url'].lower(),x['sourceURL']):x for x in r['channels']}.values())
    r['notes']=list(dict.fromkeys(r['notes']))
    r['validationErrors']=m.validate_record(r)
    r['statistics']={'documents':len(r['documents']),'fullText':sum(x['quality']=='fullText' for x in r['documents']),'partialText':sum(x['quality']=='partialText' for x in r['documents']),'snippets':sum(x['quality']=='snippet' for x in r['documents']),'uniqueWorkRoutes':len({key(x['url']) for x in r['contacts']}),'contactEvidenceRows':len(r['contacts'])}
    m.save(OUT/'companies'/(cid+'.json'),r)
    summary.append({'companyID':cid,'name':r['companyName'],**r['statistics'],'errors':r['validationErrors']})
report={'completedAt':m.now(),'scope':19,'companies':summary,'totals':{k:sum(x[k] for x in summary) for k in ['documents','fullText','partialText','snippets','uniqueWorkRoutes','contactEvidenceRows']},'verifiedAlternativeWebsites':[
 {'companyID':'discovery-blackblock-academy','website':'https://blackblock.academy/ru/','proof':'Original root HTML meta refresh to /ru/; same domain.'},
 {'companyID':'discovery-imatclass-com','website':'https://xn--80aa3aec8aad.xn--p1ai/','proof':'Same Matclass brand/company contacts; official blog links original imatclass.com/solovki-2/.'},
 {'companyID':'discovery-knower-pro','website':'https://knower.pro/','proof':'Same catalog domain without www; working about page.'},
 {'companyID':'discovery-mba-sseu-ru','website':'https://www.sseu.ru/biznes-obrazovanie/mba/','proof':'Official university domain and school-specific title, director and contacts.'},
 {'companyID':'discovery-mba-mgimo-ru','website':'https://coaching.mgimo.ru/','proof':'Official university program page identifies School of Business and its contacts; not replacement catalog homepage.'},
 {'companyID':'discovery-musicschool-pro-ru','website':'https://promoschool-on.ru/shkola-vokala','proof':'Direct original-domain redirect and matching published client@school-on.ru; project/domain relation, not legal succession.'},
 {'companyID':'discovery-netz-school','website':'https://netz.school/','proof':'Catalog site publicly rendered in browser. No domain replacement.'}
 ],'unresolved':[{'companyID':'discovery-home-art-school-ru','reason':'Alternative identity pending; new domain quarantined.'},{'companyID':'discovery-superchildren-ru','reason':'TLS failed; no suitable commercial route verified.'}],'limits':['Search-index excerpts may be older than collection; no current delivery verification.','No full-history coverage claimed. No TargetHunter, paid calls, messages, sign-in, CAPTCHA or TLS bypass.','Unique work routes are addresses/surfaces, not persons or decision makers.','Main catalog and live app were not modified.']}
m.save(OUT/'report.json',report)
print(json.dumps(report['totals'],ensure_ascii=False))
print('errors',sum(bool(x['errors']) for x in summary))
