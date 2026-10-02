"""Convert the reviewed demo data into a fully editable Excalidraw scene."""
from pathlib import Path
import json
import random
import math
import time

ROOT=Path(__file__).resolve().parent
graph=json.loads((ROOT/'demo-00.json').read_text())
S=1.5
OY=220
PAPER='#f4f1e8'
INK='#20211f'
MUTED='#696b65'
RULE='#d6d4cc'
rng=random.Random(20261001)
now=int(time.time()*1000)
els=[]

def base(id,kind,x,y,w,h,**kw):
    return dict(id=id,type=kind,x=x,y=y,width=w,height=h,angle=0,
        strokeColor=INK,backgroundColor='transparent',fillStyle='solid',
        strokeWidth=1.5,strokeStyle='solid',roughness=0,opacity=100,
        groupIds=[],frameId=None,index=None,roundness=None,
        seed=rng.randrange(1,2**30),version=1,versionNonce=rng.randrange(1,2**30),
        isDeleted=False,boundElements=[],updated=now,link=None,locked=False,**kw)

def text(id,x,y,value,size=20,width=None,color=INK,group=None,container=None,align='left'):
    lines=value.split('\n')
    width=width or max(len(t) for t in lines)*size*.57
    e=base(id,'text',x,y,width,len(lines)*size*1.25)
    e.update(strokeColor=color,text=value,originalText=value,fontSize=size,fontFamily=2,
        textAlign=align,verticalAlign='middle',containerId=container,autoResize=True,lineHeight=1.25)
    if group:e['groupIds']=[group]
    els.append(e)
    return e

def line(id,points,color=RULE,dashed=False):
    x,y=points[0]
    p=[[a-x,b-y] for a,b in points]
    e=base(id,'line',x,y,max(a for a,b in p)-min(a for a,b in p),max(b for a,b in p)-min(b for a,b in p))
    e.update(points=p,strokeColor=color,strokeStyle='dashed' if dashed else 'solid',lastCommittedPoint=None,startBinding=None,endBinding=None,startArrowhead=None,endArrowhead=None)
    els.append(e)
    return e

text('title',36,20,'HR Vision / граф работ участников',42,width=1500)
text('subtitle',36,88,'Демо 00 · основа для обсуждения · 4 участника / 9 шагов / 3 стратегии',21,width=1900,color=MUTED)
text('strategies',36,140,'База контактов: готовый профиль     /     Агентство: подбор до выхода     /     ATS: свой поток кандидатов',21,width=2520)
line('header-rule',[(36,196),(2556,196)])

# Quiet optional interface zones from the user's sketch, independently editable.
for id,x,y,w,h,label in [
    ('vacancies',368,272,288,376,'Лист вакансий'),
    ('candidates',1064,112,588,576,'Панель кандидатов')]:
    z=base('zone-'+id,'rectangle',x*S,y*S+OY,w*S,h*S)
    z.update(strokeColor=RULE,strokeStyle='dashed',roundness={'type':3},groupIds=['zone-'+id])
    els.append(z)
    t=text('zone-'+id+'-label',(x+12)*S,(y-16)*S+OY,label,17,width=320,color=MUTED,group='zone-'+id)

for i,(name,y) in enumerate([('Нанимающий менеджер',104),('HR',296),('HR-агентство',504),('Кандидат',712)],1):
    g='lane-'+str(i)
    l=line(g+'-rule',[(24*S,(y-28)*S+OY),(1704*S,(y-28)*S+OY)])
    l['groupIds']=[g]
    text(g+'-title',24*S,(y-14)*S+OY,f'0{i}  {name}',23,width=650,group=g)

points={
 'e1':[(288,160),(344,160),(344,360),(392,360)],
 'e2':[(176,224),(176,576),(392,576)],
 'e3':[(632,344),(1088,344)],
 'e4':[(568,416),(568,448),(684,448),(684,764),(752,764)],
 'e5':[(632,576),(752,576)],
 'e6':[(872,624),(872,736)],
 'e7':[(176,832),(176,880),(800,880),(800,832)],
 'e8':[(992,784),(1040,784),(1040,376),(1088,376)],
 'e9':[(1328,360),(1352,360),(1352,176),(1376,176)],
 'e10':[(1632,176),(1680,176),(1680,784),(1632,784)],
 'e11':[(1488,128),(1488,72),(1200,72),(1200,320)],
 'e12':[(1504,736),(1504,656),(1248,656),(1248,416)],
}
node_lookup={n['id']:n for n in graph['nodes']}

def binding(node_id,point):
    n=node_lookup[node_id]
    u=(point[0]-n['x'])/n['w'];v=(point[1]-n['y'])/n['h']
    focus=(v-.5)*2 if min(abs(u),abs(u-1))<.01 else (u-.5)*2
    return dict(elementId='node-'+node_id,focus=focus,gap=0,fixedPoint=[round(u,6),round(v,6)])

arrows={}
for ed in graph['edges']:
    pts=points[ed['id']]
    x,y=pts[0][0]*S,pts[0][1]*S+OY
    local=[[(px-pts[0][0])*S,(py-pts[0][1])*S] for px,py in pts]
    e=base(ed['id'],'arrow',x,y,max(a for a,b in local)-min(a for a,b in local),max(b for a,b in local)-min(b for a,b in local))
    e.update(points=local,roundness={'type':2},strokeColor=MUTED if ed.get('returning') else INK,
        strokeStyle='dashed' if ed.get('returning') else 'solid',startArrowhead=None,endArrowhead='arrow',
        startBinding=binding(ed['source'],pts[0]),endBinding=binding(ed['target'],pts[-1]),lastCommittedPoint=None,
        customData={'strategies':ed['strategies'],'status':'hypothesis' if ed.get('returning') else 'demo'})
    els.append(e);arrows[ed['id']]=e
    if 'label' in ed:
        value=ed['label']
        if ed['id']=='e3':value='База контактов: кандидат из базы'
        if ed['id']=='e4':value='ATS: свои отклики'
        if ed['id']=='e2':value='Агентство: передать подбор'
        w=len(value)*9
        tx=ed['lx']*S-w/2;ty=(ed['ly']-12)*S+OY
        text(ed['id']+'-label',tx,ty,value,16,width=w,color=MUTED,group=ed['id']+'-annotation')

# A tiny paper gap on the crossing keeps both paths traceable.
cross=base('crossing-mask','rectangle',684*S-6,576*S+OY-8,12,16)
cross.update(strokeColor=PAPER,backgroundColor=PAPER,strokeWidth=0)
els.append(cross)
line('crossing-bridge',[(684*S,568*S+OY),(694*S,572*S+OY),(694*S,580*S+OY),(684*S,584*S+OY)],INK)['roundness']={'type':2}

for n in graph['nodes']:
    id='node-'+n['id'];g='step-'+n['id']
    x=n['x']*S;y=n['y']*S+OY;w=n['w']*S;h=n['h']*S
    r=base(id,'rectangle',x,y,w,h)
    r.update(roundness={'type':3},backgroundColor=PAPER,groupIds=[g],customData=n)
    for edge in graph['edges']:
        if n['id'] in [edge['source'],edge['target']]:r['boundElements'].append({'id':edge['id'],'type':'arrow'})
    r['boundElements'].append({'id':id+'-title','type':'text'})
    els.append(r)
    text(id+'-code',x+24,y+18,n['code'],16,width=80,color=MUTED,group=g)
    text(id+'-title',x+20,y+(h-28)/2,n['title'][0],22,width=w-40,group=g,container=id,align='center')
    text(id+'-sub',x+20,y+h-40,n['sub'],16,width=w-40,color=MUTED,group=g,align='center')

line('legend-rule',[(36,1640),(2556,1640)])
text('legend',36,1660,'Сплошная стрелка: основной переход.    Пунктир: возврат, точка возврата пока гипотеза.',20,width=2520,color=MUTED)
text('review',36,1704,'На согласование: роли HR и агентства, правила выбора HR / AI, действия при отказах и границы каждой стратегии.',20,width=2520)
text('notes',36,1744,'Все шаги укрупнены. Вопросы и подробности сохранены в данных демо. Следующий шаг: правки Никиты, затем разбор записей.',18,width=2520,color=MUTED)

scene=dict(type='excalidraw',version=2,source='https://excalidraw.com',elements=els,
    appState=dict(name='HR Vision — CJM демо 00',viewBackgroundColor=PAPER,theme='light',
        currentItemStrokeColor=INK,currentItemBackgroundColor='transparent',currentItemFillStyle='solid',
        currentItemStrokeWidth=1.5,currentItemRoughness=0,currentItemFontFamily=2,currentItemFontSize=22,
        currentItemEndArrowhead='arrow',exportBackground=True,exportWithDarkMode=False,gridSize=20,gridModeEnabled=False),files={})
by_id={e['id']:e for e in els}
assert len(by_id)==len(els)
for e in els:
    assert all(math.isfinite(e[k]) for k in ['x','y','width','height'])
    if e.get('containerId'):
        assert {'id':e['id'],'type':'text'} in by_id[e['containerId']]['boundElements']
    for end in ['startBinding','endBinding']:
        if e.get(end):assert e[end]['elementId'] in by_id
out=ROOT/'HR Vision — CJM демо 00.excalidraw'
out.write_text(json.dumps(scene,ensure_ascii=False,indent=2)+'\n')
print(f'{out}\n{len(els)} editable elements; 9 steps; 12 bound arrows.')
