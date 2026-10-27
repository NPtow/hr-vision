import { useState, type ReactNode } from 'react';
import { ArrowLeft, Users } from 'lucide-react';

export type PanelRow = { id: string; name: string; meta: string; status: string; photo?: string | null; added?: boolean; archived?: boolean };
export function Avatar({ name, photo }: { name: string; photo?: string | null }) {
  const [failed, setFailed] = useState(false);
  return <span className="ew-avatar">{photo && !failed ? <img src={photo} alt="" onError={() => setFailed(true)}/> : name.split(/\s+/).slice(0, 2).map(n => n[0]).join('')}</span>;
}
export function PeoplePanel({ rows, selected, onSelect, children, tools }: {
  rows: PanelRow[]; selected: string; onSelect: (id: string) => void; onAdd: () => void; title: string; children?: ReactNode; tools?: ReactNode;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [detail, setDetail] = useState(false);
  const visible = rows.filter(c => (filter === 'archived' ? c.archived : !c.archived) && (filter !== 'added' || c.added) && `${c.name} ${c.meta}`.toLocaleLowerCase('ru').includes(query.trim().toLocaleLowerCase('ru')));
  return <section className="ew-panel-page"><div className={`ew-panel ${detail ? 'ew-show-detail' : ''}`}>
    <aside className="ew-candidate-list" aria-label="Список кандидатов">
      <div className="ew-list-tools"><label className="ew-search"><input aria-label="Найти кандидата" placeholder="Найти кандидата" value={query} onChange={e => setQuery(e.target.value)}/>{query && <button onClick={() => setQuery('')} aria-label="Очистить поиск">×</button>}</label>
        <div className="ew-list-filter"><select aria-label="Фильтр кандидатов" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">Все кандидаты</option>{rows.some(r => r.added) && <option value="added">Добавленные</option>}{rows.some(r => r.archived) && <option value="archived">Архив</option>}</select><span>{visible.length}</span></div>
      </div>
      <div className="ew-rows">{visible.map(c => <button key={c.id} className="ew-person-row" aria-pressed={selected === c.id} onClick={() => { onSelect(c.id); setDetail(true); }}><strong>{c.name}</strong><span className="ew-row-meta">{c.meta}</span><span className="ew-row-status">{c.status}</span></button>)}{!visible.length && <div className="ew-list-empty"><p>{query ? 'Никого не нашли' : 'Кандидатов пока нет'}</p>{query && <button onClick={() => { setQuery(''); setFilter('all'); }}>Сбросить поиск</button>}</div>}</div>
      {tools && <div className="ew-list-secondary">{tools}</div>}
    </aside>
    <main className="ew-candidate-detail"><button className="ew-back-list" onClick={() => setDetail(false)}><ArrowLeft size={16}/>Все кандидаты</button>{rows.length ? children : <><div className="ew-empty"><Users size={32}/><h2>Ждём первых кандидатов</h2><p>Подтверждённые кандидаты появятся здесь.</p></div>{children}</>}</main>
  </div></section>;
}
