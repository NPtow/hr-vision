import { useRef, useState, type CSSProperties } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight, ChevronDown, RotateCcw, X } from 'lucide-react';
import { JourneyNav } from './JourneyNav';
import type { JobJourneyData, JourneyDetail, JourneyEvent } from './model';
import './job-journey.css';

type Selection = { id: string; title: string; label: string; detail: JourneyDetail };
type BranchState = Record<string, string>;

function availability(event: JourneyEvent, states: BranchState) {
  if (event.requires?.some(requirement => states[requirement.branch] !== undefined && states[requirement.branch] !== requirement.value)) return 'blocked';
  if (event.requires?.some(requirement => states[requirement.branch] === undefined)) return 'pending';
  return 'available';
}

export function JobJourney({ data }: { data: JobJourneyData }) {
  const [states, setStates] = useState<BranchState>({});
  const [selected, setSelected] = useState<Selection | null>(null);
  const mapScroll = useRef<HTMLDivElement>(null);
  const allEvents = data.lanes.flatMap(lane => lane.events);
  const detailId = `journey-detail-${data.id}`;
  const gridStyle = { '--jj-phases': data.phases.length } as CSSProperties;

  function choose(event: JourneyEvent, value: string) {
    if (!event.branch || availability(event, states) !== 'available') return;
    const next = { ...states, [event.branch.id]: value };
    if (states[event.branch.id] !== value) {
      for (const item of allEvents) {
        if (item.branch && item.phase > event.phase) delete next[item.branch.id];
      }
    }
    // A changed upstream choice invalidates every now-unreachable downstream choice.
    let changed = true;
    while (changed) {
      changed = false;
      for (const item of allEvents) {
        if (item.branch && next[item.branch.id] !== undefined && availability(item, next) !== 'available') {
          delete next[item.branch.id];
          changed = true;
        }
      }
    }
    setStates(next);
    setSelected({ id: event.id, title: event.title, label: event.kind, detail: event });
  }

  function reset() {
    setStates({});
    setSelected(null);
    mapScroll.current?.scrollTo({ left: 0 });
  }

  function renderEvent(event: JourneyEvent) {
    const status = availability(event, states);
    if (status === 'blocked') return <div key={event.id} className="jj-event jj-event-blocked" style={{ gridColumn: event.phase + 2 }}>
      <span className="jj-event-kind">Эта ветка не продолжается</span>
      <p>{event.blockedMessage || 'Выбран другой путь. Это событие сейчас не происходит.'}</p>
    </div>;
    const branchChoice = event.branch?.options.find(option => option.id === states[event.branch!.id]);
    return <div key={event.id} className={`jj-event ${status === 'pending' ? 'jj-event-pending' : ''} ${selected?.id === event.id ? 'is-selected' : ''}`} style={{ gridColumn: event.phase + 2 }}>
      <button className="jj-event-body" type="button" aria-expanded={selected?.id === event.id} aria-controls={detailId} onClick={() => setSelected({ id: event.id, title: event.title, label: event.kind, detail: event })}>
        <span className="jj-event-kind">{event.kind}</span>
        {status === 'pending' && <span className="jj-conditional">Если продолжим эту ветку</span>}
        <strong>{event.title}</strong>
        <span className="jj-event-effect">{event.effect}</span>
        <span className="jj-event-open">Разобрать <ArrowUpRight size={12} aria-hidden="true"/></span>
      </button>
      {event.branch && <fieldset className="jj-branch" disabled={status === 'pending'}>
        <legend>{event.branch.question}</legend>
        <div className="jj-options">{event.branch.options.map(option => <button type="button" key={option.id} aria-pressed={states[event.branch!.id] === option.id} onClick={() => choose(event, option.id)}>{option.label}{states[event.branch!.id] === option.id && <ArrowRight size={12} aria-hidden="true"/>}</button>)}</div>
        <p className="jj-branch-result" aria-live="polite">{status === 'pending' ? 'Сначала выбери предыдущую развилку.' : branchChoice?.result || 'Выбери реакцию, чтобы увидеть продолжение.'}</p>
      </fieldset>}
    </div>;
  }

  return <main className="jj-app">
    <header className="jj-header">
      <a className="jj-brand" href="?id=hr-vision-agency--overview&viewMode=story">HR Vision <span>/</span> CJM</a>
      <span className="jj-header-label">Джобы, действия и изменения</span>
      <button className="jj-reset" type="button" onClick={reset}><RotateCcw size={14} aria-hidden="true"/> Сначала</button>
    </header>
    <JourneyNav active={data.id}/>
    <section className="jj-intro">
      <span className="jj-overline">Рабочая карта · проверяем логику пути</span>
      <h1>{data.title}</h1>
      <p className="jj-subtitle">{data.subtitle}</p>
      <div className="jj-context"><div><span>С чем человек приходит</span><p>{data.entry}</p></div><div><span>Граница этой модели</span><p>{data.boundary}</p></div></div>
      <div className="jj-legend"><span><i className="jj-key-job"/>Сверху: чего человек хочет</span><span><i className="jj-key-event"/>Снизу: что происходит</span><span className="jj-legend-tip">Нажми на блок, чтобы увидеть связь</span></div>
    </section>

    <section className="jj-map-section" aria-label="Путь участников и возникновение потребностей">
      <div className="jj-scroll-hint">Карту можно листать вправо <ArrowRight size={14} aria-hidden="true"/></div>
      <div className="jj-map-scroll" ref={mapScroll} tabIndex={0} role="region" aria-label="Горизонтальная карта сценария">
        <div className="jj-map" style={gridStyle}>
          <div className="jj-grid jj-phases"><span className="jj-time">Время <ArrowRight size={14} aria-hidden="true"/></span>{data.phases.map((phase, index) => <div className="jj-phase" key={`${index}-${phase}`}><span>{String(index + 1).padStart(2, '0')}</span>{phase}</div>)}</div>
          {data.lanes.map((lane, index) => <section className="jj-lane" key={lane.id} aria-label={lane.title}>
            <div className="jj-grid jj-goal-row">
              <div className="jj-role"><span>{String(index + 1).padStart(2, '0')}</span><h2>{lane.title}</h2><small>Джоба ↑</small></div>
              {!!lane.goalFrom && <div className="jj-job-gap" style={{gridColumn: `2 / ${lane.goalFrom + 2}`}}><span className="jj-gap-line"/><p>{lane.goalBefore || 'Потребность пока не выяснена.'}</p></div>}
              <button className={`jj-job jj-core-job ${selected?.id === `${lane.id}-goal` ? 'is-selected' : ''}`} type="button" style={{ gridColumn: `${(lane.goalFrom || 0) + 2} / ${data.phases.length + 2}` }} aria-expanded={selected?.id === `${lane.id}-goal`} aria-controls={detailId} onClick={() => setSelected({ id: `${lane.id}-goal`, title: lane.goal, label: `Основная джоба · ${lane.title}`, detail: lane.goalDetail })}>
                <span className="jj-job-label">Основной желаемый результат · продолжается через этапы</span><strong>{lane.goal}</strong><span className="jj-job-note">{lane.goalNote}</span>{lane.quote && <span className="jj-job-quote">{lane.quote}</span>}<ArrowRight className="jj-core-arrow" size={23} aria-hidden="true"/>
              </button>
            </div>
            {!!lane.jobs?.length && <div className="jj-grid jj-focus-row"><span className="jj-row-caption">Как меняется<br/>фокус человека</span>{lane.jobs.map(job => {
              const active = !job.when || states[job.when.branch] === job.when.value;
              if (!active && job.hideUntilActive) return null;
              const span = { gridColumn: `${job.from + 2} / ${job.to + 3}` };
              return active ? <button type="button" key={job.id} className={`jj-job jj-focus-job ${selected?.id === job.id ? 'is-selected' : ''}`} style={span} aria-expanded={selected?.id === job.id} aria-controls={detailId} onClick={() => setSelected({ id: job.id, title: job.title, label: `Потребность · ${lane.title}`, detail: job.detail })}><span className="jj-job-label">{job.when ? 'Появляется в выбранной ветке' : 'Потребность на этом участке'}</span><strong>{job.title}</strong><span className="jj-job-note">{job.note}</span></button> : <div key={job.id} className="jj-job-gap" style={span}><span className="jj-gap-line"/><p>{job.empty || 'Эта новая потребность пока не возникла.'}</p><small>Основная джоба продолжается</small></div>;
            })}</div>}
            <div className="jj-grid jj-events-row"><span className="jj-row-caption">Действия<br/>и события <ArrowDown size={13} aria-hidden="true"/></span>{lane.events.map(renderEvent)}</div>
            {lane.continuation && <p className="jj-continuation"><ArrowRight size={14} aria-hidden="true"/>{lane.continuation}</p>}
          </section>)}
        </div>
      </div>
    </section>

    <section className="jj-detail" id={detailId} aria-label="Разбор выбранного блока">
      {selected ? <>
        <div className="jj-detail-header"><div><span className="jj-overline">{selected.label}</span><h2>{selected.title}</h2></div><button type="button" onClick={() => setSelected(null)} aria-label="Закрыть разбор"><X size={18}/></button></div>
        <div className="jj-detail-sequence"><div><span>До этого</span><p>{selected.detail.before}</p></div><div><span>Что происходит</span><p>{selected.detail.action}</p></div><div><span>Что изменилось</span><p>{selected.detail.after}</p></div></div>
        <div className="jj-detail-bottom"><div><span>Что происходит с джобой</span><p>{selected.detail.job}</p></div><div><span>Что делаем мы</span><p>{selected.detail.ours}</p></div></div>
        <p className="jj-evidence">{selected.detail.evidence}</p>
      </> : <div className="jj-detail-placeholder"><strong>Почему здесь возникает потребность?</strong><p>Выбери джобу или событие. Здесь появятся ситуация до, действие, изменение и роль HR Vision.</p><ArrowUpRight size={20} aria-hidden="true"/></div>}
    </section>

    <section className="jj-hypothesis" aria-label="Гипотеза, риск и проверка">
      <div className="jj-hypothesis-heading"><span className="jj-overline">Что ещё нужно доказать</span><h2>Гипотеза и главный риск</h2></div>
      <div className="jj-hypothesis-grid"><div><span>Гипотеза</span><p>{data.hypothesis}</p></div><div><span>Основной риск</span><p>{data.risk}</p></div><div><span>Как проверим</span><p>{data.test}</p></div></div>
      {!!data.uncertainties.length && <details className="jj-uncertainties"><summary>Открытые вопросы <span>{data.uncertainties.length}</span><ChevronDown size={15} aria-hidden="true"/></summary><ul>{data.uncertainties.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}
    </section>
    <footer className="jj-footer"><span>HR Vision · рабочая CJM</span><span>Плашки описывают желаемый результат. Действия и интерфейсы расположены под ними.</span></footer>
  </main>;
}
