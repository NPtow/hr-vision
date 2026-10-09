import { useEffect, useId, useState, type ReactNode } from 'react';
import { CalendarDays, ChevronDown, ClipboardList, FileText, Menu, Plus, Users, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import logo from './assets/hr-vision-logo.svg';

export type EmployerPage = 'dsa' | 'tasks' | 'shortlist' | 'intake' | 'book' | 'meetings' | 'meeting' | 'review' | 'pool' | 'offer' | 'chat' | 'vacancies' | 'schedule';
export type Scope = 'sfera' | 'dsa';
export function EmployerShell({ enabled = true, page, scope, vacancy, name, hasOffers, onNavigate, onAccount, children }: {
  enabled?: boolean; page: EmployerPage; scope: Scope; vacancy: string; name: string; hasOffers: boolean;
  onNavigate: (page: EmployerPage) => void; onAccount: () => void; children: ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuId = useId();
  useEffect(() => { if (enabled) window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); setMenuOpen(false); }, [enabled, page, scope]);
  if (!enabled) return <>{children}</>;
  const panel = scope === 'dsa' ? 'dsa' : 'shortlist';
  const active = page === 'intake' ? panel : ['meeting', 'book', 'review'].includes(page) ? 'meetings' : page === 'offer' ? 'pool' : page;
  const navigate = (next: EmployerPage) => { setMenuOpen(false); onNavigate(next); };
  return <div className="ew-shell">
    <header className="ew-topbar">
      <button className="ew-menu-toggle" aria-label={menuOpen ? 'Закрыть меню' : 'Открыть меню'} aria-expanded={menuOpen} aria-controls={menuId} onClick={() => setMenuOpen(open => !open)}>{menuOpen ? <X size={20}/> : <Menu size={20}/>}</button>
      <button className="ew-logo" onClick={() => onNavigate('tasks')} aria-label="HR Vision · задачи найма"><img src={logo} alt="HR Vision"/></button>
      <button className="ew-account" onClick={onAccount}>{name}<ChevronDown size={16}/></button>
    </header>
    <div className="ew-body">
      <aside id={menuId} className="ag-sidebar ew-sidebar" data-open={menuOpen} aria-label="Разделы работодателя">
        <nav aria-label="Кабинет нанимающего">
          <button className={active === 'tasks' ? 'active' : ''} aria-current={active === 'tasks' ? 'page' : undefined} onClick={() => navigate('tasks')}><ClipboardList size={18}/>Задачи</button>
          <button className={active === panel ? 'active' : ''} aria-current={active === panel ? 'page' : undefined} onClick={() => navigate(panel)}><Users size={18}/>Кандидаты</button>
          <>
            <button className={active === 'meetings' ? 'active' : ''} aria-current={active === 'meetings' ? 'page' : undefined} onClick={() => navigate('meetings')}><CalendarDays size={18}/>Встречи</button>
            {hasOffers && <button className={active === 'pool' ? 'active' : ''} aria-current={active === 'pool' ? 'page' : undefined} onClick={() => navigate('pool')}><FileText size={18}/>Офферы</button>}
          </>
        </nav>
      </aside>
      <div className="ew-workspace">
        {page !== 'tasks' && <section className="ew-vacancy-header" aria-label="Текущая вакансия">
          <div className="ew-vacancy-title"><h1>{vacancy}</h1>{page !== 'intake' && <Button variant="secondary" className="ew-add-candidates" onClick={() => navigate('intake')}><Plus size={16}/>Добавить кандидатов</Button>}</div>
        </section>}
        <div className="ew-content">{children}</div>
      </div>
    </div>
  </div>;
}
