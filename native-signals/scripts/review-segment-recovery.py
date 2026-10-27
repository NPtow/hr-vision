#!/usr/bin/env python3
"""Create the immutable import layer after recovery QA corrections."""
import pathlib,json,hashlib,datetime,re
from urllib.parse import urlparse,parse_qs
ROOT=pathlib.Path(__file__).resolve().parents[1];OUT=ROOT/'output/segments-2026-10-14/recovery';DEST=OUT/'reviewed/companies'
DEST.mkdir(parents=True,exist_ok=True)
now=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
proof_id='public-web-observed-contact-links-20261014'
proof=json.loads((OUT/'pages'/(proof_id+'.json')).read_text())
observed={(x['sourceURL'],x.get('normalizedPublicTelegramURL',x['observedDestinationURL'])):x for x in proof['observations']}
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2))
def key(url):
 if url.startswith('tel:'):
  v=re.sub(r'\D','',url)
  if len(v)==11 and v[0]=='8':v='7'+v[1:]
  return 'tel:'+v
 if 'whatsapp.com/send' in url or 'wa.me/' in url:
  p=urlparse(url);return 'whatsapp:'+re.sub(r'\D','',parse_qs(p.query).get('phone',[p.path.strip('/')])[0])
 return url.lower().rstrip('/')
rows=[];errors=[]
for path in sorted((OUT/'companies').glob('*.json')):
 raw=path.read_bytes();r=json.loads(raw);docs={d['url']:d for d in r['documents']}
 for contact in r['contacts']:
  pair=(contact['sourceURL'],contact['url'])
  if pair in observed:
   contact['linkEvidence']['observationReceiptID']=proof_id
   contact['linkEvidence']['observedDestinationURL']=observed[pair]['observedDestinationURL']
   contact['basis']='reviewedIdentity'
   contact['proofMethod']='primaryPageToPublicTargetObserved'
  elif contact.get('acquisition')=='indexedPrimarySource':
   contact['originalBasis']=contact['basis'];contact['basis']='indexedPrimarySource'
   contact['verificationState']='candidate'
   contact['affiliation']='indexedWorkContactCandidate'
  url=contact['url'].lower()
  if 't.me/' in url and url.rstrip('/').endswith('bot'):
   contact['businessPurpose']=contact['purpose'];contact['accountType']='bot';contact['purpose']='bot'
   contact['personIdentityVerified']=False
  if any(domain in url for domain in ('instagram.com/','youtube.com/','youtu.be/')):
   contact['businessPurpose']=contact['purpose'];contact['accountType']='channel';contact['purpose']='channel'
   contact['personIdentityVerified']=False
  if 'vk.com/' in url and '/app' not in url:
   contact['accountType']='community';contact['purpose']='channel';contact['personIdentityVerified']=False
  if contact['sourceURL'] not in docs or contact['context'] not in docs[contact['sourceURL']]['text']:errors.append({'companyID':r['companyID'],'url':contact['url'],'error':'quoteMissing'})
 for ch in r['channels']:
  if ch.get('acquisition')=='indexedPrimarySource':ch['originalBasis']=ch['basis'];ch['basis']='identityCandidate';ch['status']='awaitingCurrentIdentityCheck'
 for d in r['documents']:
  if r['companyID']=='discovery-itlogia-ru' and d['url']=='https://t.me/itlogia':d['official']=False;d['relationship']='identityCandidate'
 r['reviewedAt']=now;r['rawRecordSHA256']=hashlib.sha256(raw).hexdigest();r['collector']='segment-recovery/1-reviewed'
 r['notes'].append('QA: адреса только из поисковых фрагментов остаются кандидатами до текущей проверки; отдельная наблюдённая цепочка ссылка→публичный бот сохранена в receipt. Боты и соцканалы не являются людьми или прямыми личными контактами.')
 unique={key(c['url']):c for c in r['contacts']}
 confirmed={k:c for k,c in unique.items() if c['basis'] in ['officialWebsite','reviewedIdentity','officialChannelPost']}
 candidates={k:c for k,c in unique.items() if k not in confirmed}
 direct=lambda c: c['url'].startswith(('mailto:','tel:')) or 'whatsapp' in c['url'] or 'wa.me/' in c['url']
 stats={'documents':len(r['documents']),'fullText':sum(d['quality']=='fullText' for d in r['documents']),'partialText':sum(d['quality']=='partialText' for d in r['documents']),'snippets':sum(d['quality']=='snippet' for d in r['documents']),'allUniquePublicRoutes':len(unique),'confirmedPublishedRoutes':len(confirmed),'candidateRoutes':len(candidates),'confirmedDirectAddresses':sum(direct(c) for c in confirmed.values()),'candidateDirectAddresses':sum(direct(c) for c in candidates.values()),'bots':sum(c.get('accountType')=='bot' for c in unique.values()),'socialSurfaces':sum(c.get('accountType') in ['channel','community'] for c in unique.values()),'contactEvidenceRows':len(r['contacts'])}
 r['statistics']=stats;save(DEST/path.name,r);rows.append({'companyID':r['companyID'],'name':r['companyName'],**stats})
report={'reviewedAt':now,'importDirectory':str(DEST),'companies':rows,'totals':{k:sum(r[k] for r in rows) for k in rows[0] if k not in ['companyID','name']},'errors':errors,'proofCandidates':['Home Art alternative website identity is quarantined.','PROЧТЕНИЕ has no suitable commercial contact; privacy-only email excluded.','MGIMO phone href/display mismatch is candidate-only.','Indexed-only contact addresses are candidates.','Itlogia channel domainInBio is candidate pending Telegram agent check.'],'observedLinkReceipt':str(OUT/'pages'/(proof_id+'.json'))}
save(OUT/'reviewed/report.json',report)
complete=json.loads((OUT/'complete.json').read_text());complete.update(reviewedDirectory=str(DEST),reviewedAt=now,reviewedRecordSHA256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.glob('*.json'))},reviewedTotals=report['totals'],errors=errors,proofCandidates=report['proofCandidates'],status='reviewedStagedForIndependentQA');save(OUT/'complete.json',complete)
print(json.dumps({'totals':report['totals'],'errors':errors},ensure_ascii=False))
