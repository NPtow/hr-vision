from pathlib import Path
import json
from html import escape

ROOT = Path(__file__).parent
ALL = ['base', 'agency', 'ats']
nodes = [
    dict(id='M1', code='НМ1', role='Нанимающий менеджер', x=48, y=128, w=240, h=96, title=['Понять, кого нанять'], sub='Задача бизнеса и требования', strategies=ALL,
         meaning='Понять, какую задачу бизнеса должен решить новый человек, и сформулировать требования к нему.', result='Потребность и требования, с которыми можно начать подбор.',
         branches=['Есть открытая вакансия: можно искать кандидата в базе.', 'Нужна помощь с наймом: можно передать подбор агентству.', 'Есть свой поток кандидатов: можно вести его в ATS.'],
         question='Что запускает потребность: рост, замена, перегрузка или наше холодное обращение?', basis='Три входа из общей деки. Разделение триггеров предстоит уточнить.'),
    dict(id='H1', code='HR1', role='HR', x=392, y=320, w=240, h=96, title=['Открыть подбор'], sub='Вакансия, требования, поток', strategies=['base', 'ats'],
         meaning='Создать или обработать вакансию, уточнить требования и выбрать источник кандидатов.', result='Рабочая вакансия и понятный способ пополнения кандидатов.',
         branches=['База контактов: получить кандидата с готовым профилем и интервью.', 'ATS: принять собственные отклики и отправить кандидатов на интервью.'],
         question='Кто создает вакансию: HR, нанимающий менеджер или агентство? Какие данные обязательны?', basis='Создание и импорт вакансий обсуждались с Мишей. Владелец шага пока предложен для демо.'),
    dict(id='A1', code='А1', role='HR-агентство', x=392, y=528, w=240, h=96, title=['Принять заявку'], sub='Понять задачу работодателя', strategies=['agency'],
         meaning='Поговорить с работодателем, снять требования и согласовать, какой результат дает агентство.', result='Согласованная заявка на подбор.',
         branches=['Заявка понятна: организовать поиск.', 'Требования неясны: вернуться к уточнению. Второй путь пока не раскрыт.'],
         question='Что нужно согласовать до старта: роль, бюджет, сроки, критерии или условия услуги?', basis='Встреча и снятие ТЗ входят в агентскую стратегию общей деки.'),
    dict(id='A2', code='А2', role='HR-агентство', x=752, y=528, w=240, h=96, title=['Организовать подбор'], sub='Найти и пригласить кандидатов', strategies=['agency'],
         meaning='Найти подходящих людей в своей базе или через привлечение и организовать оценку.', result='Кандидат приглашен на подходящий маршрут интервью.',
         branches=['У кандидата уже есть пригодное интервью: использовать профиль. Ветка для v1.', 'Данных не хватает: провести интервью.'],
         question='Когда агентство использует свою базу, а когда запускает новый поиск?', basis='Агентская стратегия и целевая общая база. В демо показан только маршрут с интервью.'),
    dict(id='C1', code='К1', role='Кандидат', x=48, y=736, w=240, h=96, title=['Начать поиск работы'], sub='Отклик или приглашение', strategies=['agency', 'ats'],
         meaning='Найти подходящую возможность или ответить на приглашение рекрутера.', result='Кандидат понимает следующий шаг и решает участвовать.',
         branches=['Кандидат сам откликается.', 'HR или агентство приглашает кандидата.'],
         question='Почему человек соглашается участвовать и что ему обещаем до интервью?', basis='Самостоятельный вход и приглашение укрупнены для демо. Триггеры и отказы еще не разобраны.'),
    dict(id='C2', code='К2', role='Кандидат', x=752, y=736, w=240, h=96, title=['Пройти интервью'], sub='С HR  /  с внешним AI', strategies=['agency', 'ats'],
         meaning='Рассказать об опыте и ответить на вопросы, чтобы появилась информация для оценки.', result='Запись или ответы, из которых можно подготовить профиль и оценку.',
         branches=['Разговор с HR.', 'Интервью в стороннем AI-сервисе.'],
         question='Кто и по какому правилу выбирает маршрут? Что делать, если кандидат отказывается от AI?', basis='Никита 01.10 явно включил в ближайший процесс HR и стороннее AI-интервью.'),
    dict(id='H2', code='HR2', role='HR', x=1088, y=320, w=240, h=96, title=['Проверить и сравнить'], sub='Интервью, оценка, подборка', strategies=ALL,
         meaning='Посмотреть данные кандидата, соотнести их с требованиями и передать работодателю понятную подборку.', result='Кандидаты с объяснением, почему их предлагают на эту роль.',
         branches=['Данных достаточно: передать на решение.', 'Данных не хватает: запросить уточнение. Ветка для v1.'],
         question='В каких стратегиях это делает HR, а в каких сам нанимающий менеджер?', basis='Оценка и подборка есть в материалах. Универсальный владелец HR — гипотеза демо.'),
    dict(id='M2', code='НМ2', role='Нанимающий менеджер', x=1376, y=128, w=256, h=96, title=['Выбрать кандидата'], sub='Посмотреть, встретиться, решить', strategies=ALL,
         meaning='Изучить подборку и доказательства, при необходимости встретиться с кандидатом и решить, делать ли предложение.', result='Предложение кандидату или объясненный отказ.',
         branches=['Подходит: перейти к предложению.', 'Не подходит: вернуть причину отказа в подбор.'],
         question='Возвращаемся к новым кандидатам, уточняем требования или запрашиваем дополнительные сведения?', basis='Решения и причины отказов обсуждались во встрече. Точки возврата на схеме — гипотеза.'),
    dict(id='C3', code='К3', role='Кандидат', x=1376, y=736, w=256, h=96, title=['Решить по предложению'], sub='Договориться → выйти на работу', strategies=ALL,
         meaning='Оценить роль и условия, договориться с работодателем и при согласии перейти к выходу на работу.', result='Выход на работу или отказ с причиной. Принятый оффер сам по себе еще не означает выход.',
         branches=['Согласие: договориться о выходе.', 'Отказ: вернуть причину и продолжить подбор.'],
         question='Где заканчивается работа каждой стратегии: контакт, оффер, выход или прохождение испытательного срока?', basis='Граница контакта и найма различается в стратегии. Отказы и выход пока укрупнены.'),
]

edges = [
    dict(id='e1', source='M1', target='H1', strategies=['base','ats'], path='M 288 160 H 336 Q 344 160 344 168 V 352 Q 344 360 352 360 H 392'),
    dict(id='e2', source='M1', target='A1', strategies=['agency'], path='M 176 224 V 568 Q 176 576 184 576 H 392', label='ПЕРЕДАТЬ ПОДБОР', lx=288, ly=548),
    dict(id='e3', source='H1', target='H2', strategies=['base'], path='M 632 344 H 1088', label='КАНДИДАТ ИЗ БАЗЫ', lx=864, ly=316),
    dict(id='e4', source='H1', target='C2', strategies=['ats'], path='M 568 416 V 440 Q 568 448 576 448 H 676 Q 684 448 684 456 V 568 C 696 568 696 584 684 584 V 756 Q 684 764 692 764 H 752', label='СВОИ ОТКЛИКИ', lx=600, ly=484),
    dict(id='e5', source='A1', target='A2', strategies=['agency'], path='M 632 576 H 752'),
    dict(id='e6', source='A2', target='C2', strategies=['agency'], path='M 872 624 V 736', label='ПРИГЛАШЕНИЕ', lx=956, ly=684),
    dict(id='e7', source='C1', target='C2', strategies=['agency','ats'], path='M 176 832 V 872 Q 176 880 184 880 H 792 Q 800 880 800 872 V 832', label='ОТКЛИК / СОГЛАСИЕ УЧАСТВОВАТЬ', lx=484, ly=856),
    dict(id='e8', source='C2', target='H2', strategies=['agency','ats'], path='M 992 784 H 1032 Q 1040 784 1040 776 V 384 Q 1040 376 1048 376 H 1088', label='ПРОФИЛЬ И ОТВЕТЫ', lx=1136, ly=704),
    dict(id='e9', source='H2', target='M2', strategies=ALL, path='M 1328 360 H 1344 Q 1352 360 1352 352 V 184 Q 1352 176 1360 176 H 1376'),
    dict(id='e10', source='M2', target='C3', strategies=ALL, path='M 1632 176 H 1672 Q 1680 176 1680 184 V 776 Q 1680 784 1672 784 H 1632', label='ПРЕДЛОЖЕНИЕ', lx=1576, ly=480),
    dict(id='e11', source='M2', target='H2', strategies=ALL, returning=True, path='M 1488 128 V 80 Q 1488 72 1480 72 H 1208 Q 1200 72 1200 80 V 320', label='НЕ ПОДХОДИТ', lx=1344, ly=48),
    dict(id='e12', source='C3', target='H2', strategies=ALL, returning=True, path='M 1504 736 V 664 Q 1504 656 1496 656 H 1256 Q 1248 656 1248 648 V 416', label='ОТКАЗ КАНДИДАТА', lx=1380, ly=632),
]

graph = dict(version='demo-00', status='awaiting-user-review', nodes=nodes, edges=edges,
             assumptions=['HR is a specialist who may work for employer or agency.', 'HR2 ownership across all strategies is provisional.', 'Return points are hypotheses.', 'Strategy filters show only the current visible portion, not every stage of the full journey.'])
ROOT.joinpath('demo-00.json').write_text(json.dumps(graph, ensure_ascii=False, indent=2)+'\n')

def svg_label(e):
    if 'label' not in e: return ''
    width=len(e['label'])*7.3+16
    return f'<g class="edge-label"><rect x="{e["lx"]-width/2}" y="{e["ly"]-15}" width="{width}" height="20" fill="var(--paper)"/><text x="{e["lx"]}" y="{e["ly"]}" text-anchor="middle">{escape(e["label"])}</text></g>'

edge_markup=''.join(f'<g class="edge {"return" if e.get("returning") else ""}" data-strategies="{" ".join(e["strategies"])}" data-source="{e["source"]}" data-target="{e["target"]}"><path class="edge-mask" d="{e["path"]}"/><path class="edge-stroke" d="{e["path"]}" marker-end="url(#arrow)"/>{svg_label(e)}</g>' for e in sorted(edges,key=lambda e:e['id']=='e4'))
node_markup=''
for n in nodes:
    x,y,w,h=n['x'],n['y'],n['w'],n['h']
    node_markup+=f'''<g class="node" id="node-{n['id']}" data-node="{n['id']}" data-strategies="{' '.join(n['strategies'])}" role="button" tabindex="0" aria-label="{n['code']}. {n['role']}. {' '.join(n['title'])}">
      <rect class="node-bg" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>
      <text class="node-code" x="{x+16}" y="{y+24}">{n['code']}</text><text class="node-open" x="{x+w-24}" y="{y+24}">↗</text>
      <text class="node-title" x="{x+16}" y="{y+52}">{escape(n['title'][0])}</text>
      <text class="node-sub" x="{x+16}" y="{y+76}">{escape(n['sub'])}</text>
    </g>'''

lane_markup=''
for num,(title,subtitle,y) in enumerate([('Нанимающий менеджер','Задача бизнеса и решение',104),('HR','Специалист по подбору',296),('HR-агентство','Организация и результат услуги',504),('Кандидат','Выбор работы и участие',712)],1):
    lane_markup+=f'<g class="lane"><line x1="24" y1="{y-28}" x2="1704" y2="{y-28}"/><text x="24" y="{y}"><tspan class="lane-number">0{num}</tspan><tspan dx="16">{title}</tspan></text></g>'

html='''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>HR Vision · CJM, демо 00</title>
<style>
:root{--paper:#f4f1e8;--paper2:#edeae2;--ink:#20211f;--muted:#696b65;--rule:#d6d4cc;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px var(--sans)}button{font:inherit;color:inherit;cursor:pointer}button:focus-visible,.node:focus-visible{outline:2px solid var(--ink);outline-offset:4px}button{border:1px solid var(--rule);background:transparent;border-radius:5px;padding:10px 14px}button:hover{background:var(--paper2)}
.shell{height:100dvh;min-height:640px;display:flex;flex-direction:column;padding:28px 36px 16px;overflow:hidden}.top{display:flex;justify-content:space-between;align-items:center;gap:20px}.brand{font-size:12px;font-weight:650;letter-spacing:.14em}.draft{border:1px solid var(--rule);padding:7px 10px;font-size:11px;letter-spacing:.04em}.title-row{display:flex;align-items:flex-end;justify-content:space-between;gap:32px;margin:20px 0 24px}h1{font:400 clamp(32px,3.5vw,48px)/1.08 "Iowan Old Style",Georgia,serif;margin:0;letter-spacing:-.035em}.subtitle{margin:10px 0 0;color:var(--muted);font-size:14px}.counts{display:flex;gap:22px;font-size:11px;color:var(--muted);white-space:nowrap}.counts b{display:block;font-size:22px;line-height:1.35;font-weight:500;color:var(--ink)}
.toolbar{display:flex;justify-content:space-between;gap:16px;align-items:center;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);padding:12px 0}.routes{display:flex;align-items:center;gap:7px}.routes button{border-color:transparent;padding:9px 12px;font-size:12px}.routes button[aria-pressed=true]{background:var(--ink);color:var(--paper)}.toolbar-label{font-size:10px;letter-spacing:.09em;color:var(--muted);margin-right:12px}.zones-toggle{font-size:12px;white-space:nowrap}.zones-toggle[aria-pressed=true]{border-color:var(--ink)}
.workspace{flex:1;min-height:0;position:relative}.viewport{position:absolute;inset:0 0 40px;overflow:hidden;cursor:grab;touch-action:none}.viewport.dragging{cursor:grabbing}.graph{width:100%;height:100%;display:block;overflow:visible;user-select:none}.lane line{stroke:var(--rule);stroke-width:1}.lane text{font-size:16px;font-weight:500;fill:var(--ink)}.lane .lane-number{fill:var(--muted);font-size:12px}.lane text.lane-sub{font-size:12px;font-weight:400;fill:var(--muted)}
.node{cursor:pointer;transition:opacity .18s}.node-bg{fill:var(--paper);stroke:var(--ink);stroke-width:1.35}.node:hover .node-bg,.node.selected .node-bg{fill:var(--paper2);stroke-width:2.2}.node-code{font-size:12px;fill:var(--muted);letter-spacing:.07em}.node-open{font-size:16px;fill:var(--muted)}.node-title{font-size:16px;font-weight:600;fill:var(--ink)}.node-sub{font-size:12px;fill:var(--muted)}.edge{transition:opacity .18s;pointer-events:none}.edge-stroke{stroke:var(--ink);stroke-width:1.4;fill:none}.edge-mask{stroke:var(--paper);stroke-width:7;fill:none}.edge.return .edge-stroke{stroke-dasharray:5 5;stroke:var(--muted)}.edge-label text{font-size:12px;fill:var(--muted);letter-spacing:.03em}.dim{opacity:.11}.zone{fill:none;stroke:var(--muted);stroke-width:1.2;stroke-dasharray:6 5}.zones text{font-size:12px;fill:var(--muted)}.zones{display:none}.show-zones .zones{display:block}.legend{position:absolute;bottom:12px;left:0;display:flex;gap:22px;color:var(--muted);font-size:11px;background:var(--paper);padding:6px 12px 6px 0}.legend span{display:flex;align-items:center;gap:8px}.line-sample{width:23px;border-top:1px solid var(--ink)}.line-sample.dashed{border-top-style:dashed}.controls{position:absolute;bottom:8px;right:0;display:flex;align-items:center;background:var(--paper);border:1px solid var(--rule);border-radius:5px}.controls button{border:0;border-radius:0;padding:9px 12px;font-size:12px}.controls .zoom-value{font-size:11px;color:var(--muted);min-width:44px;text-align:center}.controls .fit{border-left:1px solid var(--rule)}
.footer{display:flex;align-items:center;justify-content:space-between;gap:20px;border-top:1px solid var(--rule);padding-top:12px;font-size:11px;color:var(--muted)}.footer strong{font-weight:500;color:var(--ink)}.route-caption{max-width:75ch}.drawer{position:absolute;right:0;top:12px;bottom:54px;width:352px;background:var(--paper);border:1px solid var(--rule);border-radius:8px;padding:24px;overflow:auto;z-index:3}.drawer[hidden]{display:none}.drawer-top{display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--muted)}.drawer-top button{border:0;padding:2px 6px;font-size:22px}.drawer h2{font:400 27px/1.15 "Iowan Old Style",Georgia,serif;margin:22px 0 18px;letter-spacing:-.025em}.drawer h3{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:500;margin:24px 0 8px}.drawer p,.drawer li{font-size:13px;line-height:1.6}.drawer ul{padding-left:18px;margin:8px 0}.drawer .question{border-top:1px solid var(--rule);padding-top:16px;margin-top:24px}.drawer .basis{color:var(--muted);font-size:11px}.drawer .tag{display:inline-block;border:1px solid var(--rule);font-size:10px;padding:4px 6px;margin-top:12px}.hint{font-size:11px;color:var(--muted)}
@media(max-width:850px){.shell{padding:20px 16px 12px}.counts{display:none}.title-row{margin:20px 0}.toolbar{align-items:flex-start}.toolbar-label{display:none}.routes{flex-wrap:wrap;gap:3px}.routes button{padding:8px;font-size:11px}.zones-toggle{font-size:11px;padding:8px}.footer{align-items:flex-start}.hint{display:none}.legend{gap:12px;font-size:10px}.drawer{width:min(352px,100%);bottom:58px}.subtitle{font-size:12px}}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
.node:focus{outline:none}.node:focus-visible .node-bg{stroke:var(--ink);stroke-width:2.8}
</style></head><body>
<main class="shell">
  <div class="top"><div class="brand">HR VISION <span style="font-weight:400;color:var(--muted)">/ КАРТА РАБОТ</span></div><div class="draft">ДЕМО 00 · НА СОГЛАСОВАНИЕ</div></div>
  <div class="title-row"><div><h1>От потребности до найма.</h1><p class="subtitle">Четыре участника. Три входа. Первая основа для обсуждения.</p></div><div class="counts"><span><b>04</b>участника</span><span><b>09</b>крупных шагов</span><span><b>03</b>стратегии</span></div></div>
  <nav class="toolbar" aria-label="Настройки графа"><div class="routes"><span class="toolbar-label">ПОКАЗАТЬ ПУТЬ</span><button data-route="all" aria-pressed="true">Весь граф</button><button data-route="base" aria-pressed="false">База контактов</button><button data-route="agency" aria-pressed="false">Агентство</button><button data-route="ats" aria-pressed="false">Бесплатная ATS</button></div><button id="zones-toggle" class="zones-toggle" aria-pressed="false">Зоны экранов</button></nav>
  <section class="workspace" aria-label="Интерактивная карта работ">
    <div class="viewport" id="viewport"><svg class="graph" id="graph" viewBox="0 0 1728 920" role="img" aria-labelledby="demo-00-title demo-00-desc"><title id="demo-00-title">Работы участников HR Vision, демо 00</title><desc id="demo-00-desc">Нанимающий менеджер, HR, агентство и кандидат проходят путь от потребности и поиска до выбора и предложения, с возвратами при отказах и тремя стратегиями входа.</desc><defs><marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0 0L8 3L0 6" fill="#20211f"/></marker><marker id="arrow-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0 0L8 3L0 6" fill="#20211f"/></marker><marker id="arrow-link" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0 0L8 3L0 6" fill="#696b65"/></marker></defs><g id="world">
    <g class="zones"><rect class="zone" x="368" y="272" width="288" height="376" rx="8"/><rect x="384" y="260" width="180" height="20" fill="var(--paper)"/><text x="392" y="276">ЛИСТ ВАКАНСИЙ</text><rect class="zone" x="1064" y="112" width="588" height="576" rx="8"/><rect x="1104" y="100" width="196" height="20" fill="var(--paper)"/><text x="1112" y="116">ПАНЕЛЬ КАНДИДАТОВ</text></g>
    __LANES____EDGES____NODES__
    </g></svg></div>
    <aside class="drawer" id="drawer" hidden aria-label="Описание шага"><div class="drawer-top"><span id="detail-role"></span><button id="close-detail" aria-label="Закрыть описание">×</button></div><h2 id="detail-title"></h2><p id="detail-meaning"></p><h3>Что получает следующий участник</h3><p id="detail-result"></p><h3>Развилки</h3><ul id="detail-branches"></ul><div class="question"><h3>Вопрос к этому шагу</h3><p id="detail-question"></p></div><p class="basis" id="detail-basis"></p><span class="tag">УКРУПНЕНО ДЛЯ ДЕМО</span></aside>
    <div class="legend"><span><i class="line-sample"></i>основной переход</span><span><i class="line-sample dashed"></i>возврат · гипотеза</span></div>
    <div class="controls" aria-label="Масштаб"><button id="zoom-out" aria-label="Уменьшить масштаб">−</button><span class="zoom-value" id="zoom-value">100%</span><button id="zoom-in" aria-label="Увеличить масштаб">+</button><button id="fit" class="fit">Весь граф</button></div>
  </section>
  <footer class="footer"><span class="route-caption" id="route-caption"><strong>Сначала согласуем основу.</strong> Затем раскроем развилки и гипотезы по записям.</span><span class="hint">Клик по шагу · перетаскивание поля · + / −</span></footer>
</main>
<script id="graph-data" type="application/json">__DATA__</script>
<script>
const data=JSON.parse(document.getElementById('graph-data').textContent);
const descriptions={all:'Сначала согласуем основу. Затем раскроем развилки и гипотезы по записям.',base:'База контактов: используем готовое интервью и профиль. Клиент продолжает найм сам.',agency:'Агентство: принимаем задачу, организуем подбор и сопровождаем найм. Возвраты требуют уточнения.',ats:'Бесплатная ATS: работодатель ведёт свой поток. Внешний кандидат и платный переход раскроем в v1.'};
let activeRoute='all',selected=null,scale=1,dx=0,dy=0,drag=null,moved=false;
const svg=document.getElementById('graph'),world=document.getElementById('world'),viewport=document.getElementById('viewport');
function setRoute(route){activeRoute=route;document.querySelectorAll('[data-route]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.route===route)));document.querySelectorAll('[data-strategies]').forEach(g=>g.classList.toggle('dim',route!=='all'&&!g.dataset.strategies.split(' ').includes(route)));document.getElementById('route-caption').textContent=descriptions[route];closeDetail();}
document.querySelectorAll('[data-route]').forEach(b=>b.addEventListener('click',()=>setRoute(b.dataset.route)));
document.getElementById('zones-toggle').addEventListener('click',function(){const on=this.getAttribute('aria-pressed')!=='true';this.setAttribute('aria-pressed',String(on));svg.classList.toggle('show-zones',on);});
function openDetail(id){const n=data.nodes.find(n=>n.id===id);selected=id;document.querySelectorAll('.node').forEach(g=>g.classList.toggle('selected',g.dataset.node===id));document.getElementById('detail-role').textContent=n.code+' / '+n.role;for(const [field,value]of Object.entries({title:n.title.join(' '),meaning:n.meaning,result:n.result,question:n.question,basis:n.basis})){document.getElementById('detail-'+field).textContent=value;}document.getElementById('detail-branches').replaceChildren(...n.branches.map(t=>{const li=document.createElement('li');li.textContent=t;return li;}));document.getElementById('drawer').hidden=false;}
function closeDetail(){document.getElementById('drawer').hidden=true;document.querySelectorAll('.node').forEach(g=>g.classList.remove('selected'));selected=null;}
document.querySelectorAll('.node').forEach(g=>{g.addEventListener('click',()=>{if(!moved)openDetail(g.dataset.node)});g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openDetail(g.dataset.node);}})});
document.getElementById('close-detail').addEventListener('click',closeDetail);
function updateView(){world.setAttribute('transform',`translate(${dx} ${dy}) scale(${scale})`);document.getElementById('zoom-value').textContent=Math.round(scale*100)+'%';}
function zoom(f){const old=scale;scale=Math.max(.6,Math.min(2.6,scale*f));dx=864-(864-dx)*scale/old;dy=460-(460-dy)*scale/old;updateView();}
document.getElementById('zoom-in').addEventListener('click',()=>zoom(1.2));document.getElementById('zoom-out').addEventListener('click',()=>zoom(1/1.2));document.getElementById('fit').addEventListener('click',()=>{scale=1;dx=dy=0;updateView();closeDetail();});
viewport.addEventListener('pointerdown',e=>{if(e.button!==0)return;moved=false;if(e.target.closest('.node'))return;drag={x:e.clientX,y:e.clientY,dx,dy};viewport.setPointerCapture(e.pointerId);viewport.classList.add('dragging');});
viewport.addEventListener('pointermove',e=>{if(!drag)return;const factor=1/svg.getScreenCTM().a;dx=drag.dx+(e.clientX-drag.x)*factor;dy=drag.dy+(e.clientY-drag.y)*factor;if(Math.abs(e.clientX-drag.x)+Math.abs(e.clientY-drag.y)>4)moved=true;updateView();});
viewport.addEventListener('pointerup',()=>{drag=null;viewport.classList.remove('dragging');});viewport.addEventListener('pointercancel',()=>{drag=null;viewport.classList.remove('dragging');});
viewport.addEventListener('wheel',e=>{if(e.ctrlKey||e.metaKey){e.preventDefault();zoom(e.deltaY>0?1/1.08:1.08)}},{passive:false});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDetail();if(e.key==='+'||e.key==='=')zoom(1.2);if(e.key==='-')zoom(1/1.2);if(e.key==='0'){scale=1;dx=dy=0;updateView();}});
</script></body></html>
'''
html=html.replace('__LANES__',lane_markup).replace('__EDGES__',edge_markup).replace('__NODES__',node_markup).replace('__DATA__',json.dumps(graph,ensure_ascii=False).replace('</','<\\/'))
ROOT.joinpath('demo-00.html').write_text(html)
print(ROOT/'demo-00.html')
