"""Explicit shared-test reset. Only the prepared team fixture may be used."""
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

assert os.environ.get('HR_ALLOW_RESET_TEST') == '1', 'This check resets the shared team scenario.'
BASE = 'https://hr-vision.158-160-179-53.sslip.io/api/hr/'
key = Path('tmp/hr-team.txt').read_text().strip()
checks = []

def check(value, label):
    assert value, label
    checks.append(label)

def http(path, token=None, body=None):
    headers = {'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    req=urllib.request.Request(BASE+path,headers=headers,data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req,timeout=30) as r: return r.status,json.load(r)
    except urllib.error.HTTPError as e: return e.code,json.load(e)

tokens={a:http('session',body={'teamKey':key,'actor':a})[1]['token'] for a in ('manager','anna','mikhail','elena')}
def state(actor='manager'): return http('state',tokens[actor])[1]
def act(actor,action,cid='anna',**values):
    s=state(actor)
    code,data=http('action',tokens[actor],dict(action=action,candidate=cid,generation=s['generation'],**values))
    assert code==200,(action,code,data)
    return data

act('manager','reset')
check(http('state')[0]==401,'Anonymous cannot read team state')
check(http('session',body={'actor':'manager','teamKey':'incorrect'})[0]==403,'Team access key required')
check(state()['candidates']=={},'Manager shortlist starts empty')
act('anna','interest',value='accepted')
check(list(state()['candidates'])==['anna'],'Candidate appears after acceptance across sessions')
act('mikhail','auto','mikhail',enabled=True)
a=state(); slot=a['candidates']['anna']['freeSlots'][0]
bodies=[dict(action='meeting.propose',candidate=c,generation=a['generation'],start=slot) for c in ('anna','mikhail')]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results=list(pool.map(lambda b:http('action',tokens['manager'],b),bodies))
check(sorted(r[0] for r in results)==[200,409],'Concurrent slot reservation has one winner')
a=state(); booked=next(c for c,p in a['candidates'].items() if p['meeting']); other='mikhail' if booked=='anna' else 'anna'
check(a['candidates'][booked]['meeting']['status']=='pending','Even auto mode requires manual confirmation')
check(slot not in a['candidates'][other]['freeSlots'],'Reserved manager slot removed for other candidates')
act('manager','meeting.propose',other,start=a['candidates'][other]['freeSlots'][0])
a=state()
check(sum(bool(p['meeting']) for p in a['candidates'].values())==2,'Two meetings coexist on server')
for cid in ('anna','mikhail'):
    mid=state(cid)['candidates'][cid]['meeting']['id']
    check(http('action',tokens['manager'],dict(action='meeting.confirm',candidate=cid,meetingId=mid,generation=a['generation']))[0]==403,'Manager cannot confirm for '+cid)
    act(cid,'meeting.confirm',cid,meetingId=mid)
check(all(p['meeting']['status']=='confirmed' for p in state()['candidates'].values()),'Both candidates confirm independently')
act('anna','chat',text='Уточним условия в чате')
check(state()['candidates']['anna']['messages'][-1]['text']=='Уточним условия в чате','Candidate message visible to employer')
check('anna' not in state('mikhail')['candidates'],'Candidates cannot read other candidates')
mid=state('anna')['candidates']['anna']['meeting']['id']
code,data=http('room',tokens['anna'],dict(candidate='anna',meetingId=mid,generation=a['generation'],consent=True))
check(code==503,'Unavailable video provider is explicit, never a fake call')
for cid in ('anna','mikhail'):
    mid=state()['candidates'][cid]['meeting']['id']
    act('manager','meeting.skip',cid,meetingId=mid)
    act('manager','feedback',cid,example='Пример работы',doubts='Нет данных',check='Проверить кейс')
    act(cid,'after',cid,value='yes')
    act('manager','decision',cid,value='pool')
    act('manager','offer.save',cid,role='Менеджер',compensation='160000 + бонус',format='Гибрид',expectations='Повторные продажи',startDate=(dt.date.today()+dt.timedelta(days=14)).isoformat())
check(state('anna')['candidates']['anna']['offer'] is None,'Draft offer hidden')
check('feedback' not in state('anna')['candidates']['anna'],'Employer private feedback not exposed')
act('manager','offer.send','anna')
a=state()
check(http('action',tokens['manager'],dict(action='offer.send',candidate='mikhail',generation=a['generation']))[0]==409,'Second active offer prohibited')
act('anna','offer.reply',value='declined',version=1)
act('manager','offer.send','mikhail')
act('mikhail','offer.reply','mikhail',value='accepted',version=1)
check(state()['closedBy']=='mikhail','Acceptance closes hiring for all accounts')
old=state()['generation'];act('manager','reset')
check(http('action',tokens['anna'],dict(action='interest',candidate='anna',value='accepted',generation=old))[0]==409,'Old browser actions rejected after reset')
check(state()['candidates']=={},'Reset returns to empty shortlist')
print(json.dumps({'status':'passed','count':len(checks),'checks':checks},ensure_ascii=False,indent=2))
