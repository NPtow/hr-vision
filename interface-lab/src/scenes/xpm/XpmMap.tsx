import { useId, useRef, useState, type CSSProperties } from 'react';
import { ArrowDownRight, ArrowRight, ChevronDown, ExternalLink, X } from 'lucide-react';
import type { XpmActor, XpmChapter, XpmEdge, XpmNode, XpmSource, XpmStrategy, XpmStrategyId } from './model';
import './xpm.css';

const GEOMETRY = { left: 230, column: 244, row: 138, top: 48, right: 125 };

function coordinates(node: XpmNode, actors: XpmActor[]) {
  return { x: GEOMETRY.left + node.column * GEOMETRY.column, y: GEOMETRY.top + actors.findIndex(actor => actor.id === node.actorId) * GEOMETRY.row };
}

function edgeGeometry(edge: XpmEdge, nodes: XpmNode[], actors: XpmActor[]) {
  const from = nodes.find(node => node.id === edge.from);
  const to = nodes.find(node => node.id === edge.to);
  if (!from || !to) return null;
  const start = coordinates(from, actors);
  const end = coordinates(to, actors);
  const vertical = start.x === end.x;
  const horizontal = start.y === end.y;
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const radius = 13;
  if (vertical) {
    const sign = Math.sign(dy) || 1;
    // Shared situations occupy one column; the line runs beside point labels.
    if (edge.kind === 'sync') return { path: `M ${start.x} ${start.y + sign * radius} L ${end.x} ${end.y - sign * radius}`, x: start.x + 18, y: (start.y + end.y) / 2, anchor: 'start' as const };
    return { path: `M ${start.x + radius} ${start.y} C ${start.x + 111} ${start.y}, ${end.x + 111} ${end.y}, ${end.x + radius} ${end.y}`, x: start.x + 87, y: (start.y + end.y) / 2, anchor: 'start' as const };
  }
  const sign = Math.sign(dx);
  const x1 = start.x + sign * radius;
  const x2 = end.x - sign * radius;
  if (horizontal) return { path: `M ${x1} ${start.y} L ${x2} ${end.y}`, x: (x1 + x2) / 2, y: start.y - 12, anchor: 'middle' as const };
  const middle = (x1 + x2) / 2;
  // Adjacent-lane labels sit below the upper point's name and note.
  const labelOffset = Math.abs(dy) === GEOMETRY.row ? 30 : -9;
  return { path: `M ${x1} ${start.y} C ${middle} ${start.y}, ${middle} ${end.y}, ${x2} ${end.y}`, x: middle, y: (start.y + end.y) / 2 + labelOffset, anchor: 'middle' as const };
}

function SourceList({ refs, sources }: { refs?: string[]; sources: XpmSource[] }) {
  const selected = refs?.map(id => sources.find(source => source.id === id)).filter((source): source is XpmSource => !!source) ?? [];
  if (!selected.length) return null;
  return <ul className="xpm-source-list">{selected.map(source => <li key={source.id}>
    {source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.label}<ExternalLink size={12} aria-hidden="true" /></a> : <span>{source.label}</span>}
    {source.note && <p>{source.note}</p>}
  </li>)}</ul>;
}

function Fragment({ chapter, actors, selected, onSelect, diagramId }: { chapter: XpmChapter; actors: XpmActor[]; selected: string | null; onSelect: (id: string) => void; diagramId: string }) {
  const visibleActors = actors.filter(actor => chapter.nodes.some(node => node.actorId === actor.id));
  const columns = Math.max(3, ...chapter.nodes.map(node => node.column));
  const width = GEOMETRY.left + columns * GEOMETRY.column + GEOMETRY.right;
  const height = visibleActors.length * GEOMETRY.row + 3;
  const related = new Set(chapter.edges.filter(edge => edge.from === selected || edge.to === selected).flatMap(edge => [edge.from, edge.to]));
  return <div className="xpm-fragment-scroll" tabIndex={0} aria-label="Фрагмент карты. На узком экране прокручивается по горизонтали.">
    <div className="xpm-fragment" style={{ width, height }}>
      {visibleActors.map((actor, index) => <div className="xpm-lane" style={{ top: index * GEOMETRY.row, height: GEOMETRY.row }} key={actor.id}>
        <div className="xpm-actor"><strong>{actor.name}</strong>{actor.intention && <span title={actor.intention}>{actor.intention}</span>}</div>
      </div>)}
      <svg className="xpm-connections" width={width} height={height} role="img" aria-labelledby={`${diagramId}-title ${diagramId}-description`}>
        <title id={`${diagramId}-title`}>{chapter.title}: связи между участниками</title>
        <desc id={`${diagramId}-description`}>{chapter.edges.map(edge => `${chapter.nodes.find(node => node.id === edge.from)?.label ?? edge.from} → ${chapter.nodes.find(node => node.id === edge.to)?.label ?? edge.to}${edge.label ? `: ${edge.label}` : ''}${edge.delay ? `, ${edge.delay}` : ''}`).join('. ')}</desc>
        <defs><marker id={`${diagramId}-arrow`} viewBox="0 0 6 6" refX="5.1" refY="3" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 6 3 L 0 6" fill="none" stroke="context-stroke" strokeWidth="1.3" /></marker></defs>
        {chapter.edges.map((edge, index) => {
          const geometry = edgeGeometry(edge, chapter.nodes, visibleActors);
          if (!geometry) return null;
          const active = selected === edge.from || selected === edge.to;
          const fullLabel = edge.label ?? '';
          const label = fullLabel.length > 32 ? `${fullLabel.slice(0, 30).trim()}…` : fullLabel;
          return <g key={edge.id ?? `${edge.from}-${edge.to}-${index}`} className={`xpm-edge xpm-edge--${edge.kind ?? 'sequence'}${active ? ' is-active' : ''}${selected && !active ? ' is-muted' : ''}`}>
            <title>{[fullLabel, edge.delay].filter(Boolean).join('. ')}</title>
            <path d={geometry.path} markerEnd={edge.kind === 'sync' ? undefined : `url(#${diagramId}-arrow)`} />
            {label && <text x={geometry.x} y={geometry.y} textAnchor={geometry.anchor}>{label}</text>}
          </g>;
        })}
      </svg>
      {chapter.nodes.map(node => {
        const position = coordinates(node, visibleActors);
        const isSelected = selected === node.id;
        const kind = node.kind === 'condition' ? 'condition' : node.owner === 'external' || node.kind === 'external' ? 'external' : 'key';
        const synchronous = chapter.edges.some(edge => edge.kind === 'sync' && (edge.from === node.id || edge.to === node.id));
        return <button key={node.id} type="button" className={`xpm-point xpm-point--${kind}${synchronous ? ' is-synchronous' : ''}${isSelected ? ' is-selected' : ''}${selected && !isSelected && !related.has(node.id) ? ' is-muted' : ''}`} style={{ left: position.x, top: position.y }} onClick={() => onSelect(node.id)} aria-pressed={isSelected} aria-label={`${node.label}. ${actors.find(actor => actor.id === node.actorId)?.name}. Открыть содержание точки.`}>
          <span className="xpm-point-symbol" aria-hidden="true" />
          <span className="xpm-point-name">{node.label}</span>
          {node.note && <span className="xpm-point-note" title={node.note}>{node.note}</span>}
        </button>;
      })}
    </div>
  </div>;
}

function PointDetail({ node, actor, sources, onClose }: { node: XpmNode; actor?: XpmActor; sources: XpmSource[]; onClose: () => void }) {
  const evidence = { confirmed: 'Из звонка', decision: 'Решение', hypothesis: 'Гипотеза' };
  const owner = { ours: 'Наша зона влияния', shared: 'Совместное решение', external: 'Вне нашего контроля' };
  return <section className="xpm-detail" aria-label={`Содержание точки ${node.label}`}>
    <div className="xpm-detail-heading"><div><p>{actor?.name} <span> / </span> {evidence[node.evidence ?? 'hypothesis']}</p><h3>{node.label}</h3></div><button type="button" aria-label="Закрыть содержание точки" onClick={onClose}><X size={18} /></button></div>
    {actor?.intention && <p className="xpm-detail-intention"><span>Намерение человека</span>{actor.intention}</p>}
    {node.note && <p className="xpm-detail-note">{node.note}</p>}
    <div className="xpm-detail-flow">
      <div><span>Вход</span><p>{node.input}</p></div>
      <div><span>Что происходит</span><ul>{node.actions.map(action => <li key={action}>{action}</li>)}</ul></div>
      <div><span>Выход</span><p>{node.output}</p></div>
    </div>
    {(node.channel || node.tool || node.owner) && <div className="xpm-detail-means">{node.channel && <p><span>Канал</span>{node.channel}</p>}{node.tool && <p><span>Средство</span>{node.tool}</p>}{node.owner && <p><span>Влияние</span>{owner[node.owner]}</p>}</div>}
    {(node.barrier || node.remedy) && <div className="xpm-detail-obstacle">{node.barrier && <div><span>Что может помешать</span><p>{node.barrier}</p></div>}{node.remedy && <div><span>Как предполагаем помочь</span><p>{node.remedy}</p></div>}</div>}
    {!!node.sourceRefs?.length && <details className="xpm-inline-sources"><summary>Основания этой точки<ChevronDown size={14} aria-hidden="true" /></summary><SourceList refs={node.sourceRefs} sources={sources} /></details>}
  </section>;
}

export function XpmMap({ strategies, initialStrategy }: { strategies: XpmStrategy[]; initialStrategy?: XpmStrategyId }) {
  const [strategyId, setStrategyId] = useState(initialStrategy ?? strategies[0]?.id);
  const [chapterIndex, setChapterIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const diagramId = `xpm-${useId().replace(/:/g, '')}`;
  const strategy = strategies.find(item => item.id === strategyId) ?? strategies[0];
  const chapter = strategy?.chapters[chapterIndex] ?? strategy?.chapters[0];
  if (!strategy || !chapter) return <div className="xpm-app"><p>Карта пока не заполнена.</p></div>;
  const node = chapter.nodes.find(item => item.id === selected);
  const cjmStory = strategy.id === 'ats' ? 'hr-vision-ats-example--overview' : `hr-vision-cjm--${strategy.id}`;
  function changeStrategy(id: XpmStrategyId) { setStrategyId(id); setChapterIndex(0); setSelected(null); if (scrollRef.current) scrollRef.current.scrollLeft = 0; }
  function changeChapter(index: number) { setChapterIndex(index); setSelected(null); }
  return <main className="xpm-app">
    <header className="xpm-header"><a className="xpm-brand" href="?id=hr-vision-agency--overview&viewMode=story">HR Vision<span>/</span><span>Карта опыта и процесса</span></a><nav className="xpm-header-links" aria-label="Материалы HR Vision"><a className="xpm-return" href="?id=hr-vision-agency--overview&viewMode=story">Экраны</a><a className="xpm-return" href={`?id=${cjmStory}&viewMode=story`}>Предыдущая CJM<ArrowRight size={14} aria-hidden="true" /></a></nav></header>
    <div className="xpm-shell">
      <div className="xpm-topline"><span className="xpm-eyebrow">XPM · Целевой процесс · рабочая модель</span><span className="xpm-date">03 октября 2026</span></div>
      <div className="xpm-intro"><h1>{strategy.title}</h1><p>{strategy.subtitle}</p></div>
      <div className="xpm-strategy-row"><nav className="xpm-strategies" aria-label="GTM-стратегия">{strategies.map(item => <button type="button" key={item.id} aria-pressed={item.id === strategy.id} onClick={() => changeStrategy(item.id)}>{item.label}</button>)}</nav><span className="xpm-scope">{strategy.scope}</span></div>
      <section className="xpm-overview-section" aria-label="Общий путь">
        <div className="xpm-section-caption"><span>Весь путь</span><span>Выберите ситуацию, чтобы увидеть участников</span></div>
        <div className="xpm-overview-scroll" ref={scrollRef}><nav className="xpm-overview" aria-label="Значимые ситуации" style={{ '--xpm-chapters': strategy.chapters.length } as CSSProperties}>{strategy.chapters.map((item, index) => <button type="button" key={item.id} className={index === chapterIndex ? 'is-current' : ''} aria-pressed={index === chapterIndex} onClick={() => changeChapter(index)}><span className="xpm-step-track"><span className="xpm-step-number">{String(index + 1).padStart(2, '0')}</span><i aria-hidden="true" /></span><span className="xpm-step-title">{item.title}</span></button>)}</nav></div>
      </section>
      <section className="xpm-local" aria-label="Выбранная ситуация">
        <div className="xpm-local-heading"><div><span className="xpm-section-number">{String(chapterIndex + 1).padStart(2, '0')} / {String(strategy.chapters.length).padStart(2, '0')}</span><h2>{chapter.title}</h2></div><p><ArrowDownRight size={15} aria-hidden="true" />{chapter.result}</p></div>
        <Fragment key={`${strategy.id}-${chapter.id}`} chapter={chapter} actors={strategy.actors} selected={selected} onSelect={id => setSelected(current => current === id ? null : id)} diagramId={diagramId} />
        <div className="xpm-fragment-footer"><span>Точки раскрываются нажатием</span><span className="xpm-scroll-note">Карта прокручивается вправо →</span><span className="xpm-time-direction">Последовательность ситуаций →</span></div>
        {chapter.keyBranch && <p className="xpm-key-branch"><span aria-hidden="true">◇</span>{chapter.keyBranch}</p>}
        {node && <PointDetail key={node.id} node={node} actor={strategy.actors.find(actor => actor.id === node.actorId)} sources={strategy.sources} onClose={() => setSelected(null)} />}
      </section>
      <div className="xpm-support">
        <details className="xpm-disclosure"><summary><span>Главный риск и гипотеза</span><ChevronDown size={16} aria-hidden="true" /></summary><div className="xpm-research"><div><span>Гипотеза</span><p>{strategy.hypothesis}</p></div><div><span>Основной риск</span><p>{strategy.risk}</p></div>{strategy.validation && <div><span>Как проверить</span><p>{strategy.validation}</p></div>}{chapter.barrier && <div><span>Барьер в этой ситуации</span><p>{chapter.barrier}</p></div>}</div></details>
        <details className="xpm-disclosure"><summary><span>Как читать карту</span><ChevronDown size={16} aria-hidden="true" /></summary><div className="xpm-legend"><span><i className="xpm-legend-dot" />Ключевая ситуация</span><span><i className="xpm-legend-dot is-external" />Вне нашего контроля</span><span><i className="xpm-legend-diamond" />Событие или условие</span><span><i className="xpm-legend-line" />Устойчивое следование</span><span><i className="xpm-legend-line is-waiting" />Ожидание или риск разрыва</span><span><i className="xpm-legend-line is-sync" />Общая синхронная ситуация</span></div><p className="xpm-reading-note">Каждая дорожка принадлежит человеку. Платформа, интервью и экраны раскрываются внутри точек как средства. Линии показывают связи; расположение по горизонтали показывает порядок, а не длительность.</p></details>
        <details className="xpm-disclosure"><summary><span>Границы и основания карты</span><ChevronDown size={16} aria-hidden="true" /></summary><div className="xpm-sources-context"><p><strong>Граница процесса.</strong> {strategy.scope}</p><p><strong>Позиция.</strong> HR Vision проектирует услугу; карта показывает предполагаемый процесс. Цель: найти передачи между людьми, от которых зависит результат, и проверить, где наша помощь нужна.</p><p><strong>Статус.</strong> Формулировки из разговоров обосновывают отдельные решения. Они не подтверждают, что весь показанный процесс уже работает.</p><a href="https://ashapiro.ru/xpm" target="_blank" rel="noreferrer">Метод XPM Андрея Шапиро<ExternalLink size={12} aria-hidden="true" /></a></div><SourceList refs={strategy.sources.map(source => source.id)} sources={strategy.sources} /></details>
      </div>
      <footer className="xpm-footer"><span>HR Vision · три стратегии, отдельный опыт в каждой</span><a href="?id=hr-vision-agency--overview&viewMode=story">Макеты интерфейсов<ArrowRight size={13} aria-hidden="true" /></a></footer>
    </div>
  </main>;
}
