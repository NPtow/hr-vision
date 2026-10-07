import { useState, type ReactNode } from 'react';
import { ArrowLeft, ChevronRight, Plus, Search, Users } from 'lucide-react';
import { Button } from '../../components/ui/button';

export type PanelRow = { id: string; name: string; meta: string; status: string; photo?: string | null; added?: boolean; archived?: boolean };
export function Avatar({ name, photo }: { name: string; photo?: string | null }) {
  const [failed, setFailed] = useState(false);
  return <span className="ew-avatar">{photo && !failed ? <img src={photo} alt="" onError={() => setFailed(true)}/> : name.split(/\s+/).slice(0, 2).map(n => n[0]).join('')}</span>;
}
export function PeoplePanel({ rows, selected, onSelect, onAdd, title, children, tools }: {
  rows: PanelRow[]; selected: string; onSelect: (id: string) => void; onAdd: () => void; title: string; children?: ReactNode; tools?: ReactNode;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [detail, setDetail] = useState(false);
  const visible = rows.filter(c => (filter === 'archived' ? c.archived : !c.archived) && (filter !== 'added' || c.added) && `${c.name} ${c.meta}`.toLocaleLowerCase('ru').includes(query.trim().toLocaleLowerCase('ru')));
  const hasAdded = rows.some(r => r.added);
  const hasArchived = rows.some(r => r.archived);
  return <section className="ew-panel-page">
    <header className="ew-page-heading"><div><p>{title}</p><h1>Кандидаты <span>{rows.filter(r => !r.archived).length}</span></h1></div><div className="ew-heading-actions">{tools}<Button onClick={onAdd}><Plus size={17}/>Добавить кандидатов</Button></div></header>
    <div className={`ew-panel ${detail ? 'ew-show-detail' : ''}`}>
      <aside className="ew-candidate-list" aria-label="Список кандидатов"><div className="ew-list-tools"><label className="ew-search"><Search size={17}/><input aria-label="Найти кандидата" placeholder="Поиск по имени" value={query} onChange={e => setQuery(e.target.value)}/>{query && <button onClick={() => setQuery('')} aria-label="Очистить поиск">×</button>}</label>{(hasAdded || hasArchived) && <div className="ew-filter" aria-label="Источник кандидатов"><button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>Все</button><button aria-pressed={filter === 'added'} onClick={() => setFilter('added')}>Добавленные</button>{hasArchived && <button aria-pressed={filter === 'archived'} onClick={() => setFilter('archived')}>Архив</button>}</div>}</div>
        <div className="ew-rows">{visible.map(c => <button key={c.id} className="ew-person-row" aria-pressed={selected === c.id} onClick={() => { onSelect(c.id); setDetail(true); }}><Avatar name={c.name} photo={c.photo}/><span><strong>{c.name}</strong><span className="ew-row-meta">{c.meta}</span><span className="ew-row-status">{c.status}</span></span><ChevronRight size={15}/></button>)}{!visible.length && <div className="ew-list-empty"><Search size={22}/><p>{query ? 'Никого не нашли' : 'Кандидатов пока нет'}</p>{query && <button onClick={() => { setQuery(''); setFilter('all'); }}>Сбросить поиск</button>}</div>}</div>
      </aside>
      <main className="ew-candidate-detail"><button className="ew-back-list" onClick={() => setDetail(false)}><ArrowLeft size={16}/>Все кандидаты</button>{children || <div className="ew-empty"><Users size={32}/><h2>Ждём первых кандидатов</h2><p>Подтверждённые кандидаты появятся здесь.</p></div>}</main>
    </div>
  </section>;
}
