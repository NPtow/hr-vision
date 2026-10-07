import { useEffect, type ReactNode } from 'react';
import { ChevronDown, Plus } from 'lucide-react';
import { Button } from '../../components/ui/button';
import logo from './assets/hr-vision-logo.svg';

export type EmployerPage = 'dsa' | 'tasks' | 'shortlist' | 'intake' | 'book' | 'meetings' | 'meeting' | 'review' | 'pool' | 'offer' | 'chat' | 'vacancies' | 'schedule';
export type Scope = 'sfera' | 'dsa';
export function EmployerShell({ enabled = true, page, scope, company, vacancy, name, count, meetings, pool, onNavigate, onAccount, children }: {
  enabled?: boolean; page: EmployerPage; scope: Scope; company: string; vacancy: string; name: string; count: number; meetings: number; pool: number;
  onNavigate: (page: EmployerPage) => void; onAccount: () => void; children: ReactNode;
}) {
  useEffect(() => { if (enabled) window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); }, [enabled, page, scope]);
  if (!enabled) return <>{children}</>;
  const panel = scope === 'dsa' ? 'dsa' : 'shortlist';
  const active = page === 'intake' ? panel : ['meeting', 'book', 'review'].includes(page) ? 'meetings' : page === 'offer' ? 'pool' : page;
  return <div className="ew-shell">
    <header className="ew-topbar">
      <button className="ew-logo" onClick={() => onNavigate('tasks')} aria-label="HR Vision · задачи найма"><img src={logo} alt="HR Vision"/></button>
      <button className="ew-company" onClick={() => onNavigate('tasks')} aria-label="Выбрать задачу найма">{company}</button>
      <button className="ew-account" onClick={onAccount}>{name}<ChevronDown size={16}/></button>
    </header>
    {page !== 'tasks' && <section className="ew-vacancy-header" aria-label="Текущая вакансия">
      <div className="ew-vacancy-title"><div><button className="ew-context-back" onClick={() => onNavigate('tasks')}>Задачи найма</button><h1>{vacancy}</h1></div><Button variant="secondary" className="ew-add-candidates" onClick={() => onNavigate('intake')}><Plus size={16}/>Добавить кандидатов</Button></div>
      <nav className="ew-vacancy-nav" aria-label="Этапы найма">
        <button aria-current={active === panel ? 'page' : undefined} onClick={() => onNavigate(panel)}>Кандидаты{count > 0 ? ` · ${count}` : ''}</button>
        {scope === 'sfera' && <>
          <button aria-current={active === 'meetings' ? 'page' : undefined} onClick={() => onNavigate('meetings')}>Встречи{meetings > 0 ? ` · ${meetings}` : ''}</button>
          {pool > 0 && <button aria-current={active === 'pool' ? 'page' : undefined} onClick={() => onNavigate('pool')}>Офферы · {pool}</button>}
          {page === 'chat' && <button aria-current="page" onClick={() => onNavigate('chat')}>Чат</button>}
        </>}
      </nav>
    </section>}
    <div className="ew-content">{children}</div>
  </div>;
}
