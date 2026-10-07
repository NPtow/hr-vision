import { useState, type ReactNode } from 'react';
import { BriefcaseBusiness, CalendarDays, ChevronDown, ChevronsLeft, ChevronsRight, Menu, MessageCircle, Users, X, FileCheck2 } from 'lucide-react';
import './employer.css';

export type EmployerPage = 'dsa' | 'tasks' | 'shortlist' | 'intake' | 'book' | 'meetings' | 'meeting' | 'review' | 'pool' | 'offer' | 'chat' | 'vacancies' | 'schedule';
export type Scope = 'sfera' | 'dsa';
export function EmployerShell({ enabled = true, page, scope, company, name, count, meetings, pool, onNavigate, onAccount, children }: {
  enabled?: boolean; page: EmployerPage; scope: Scope; company: string; name: string; count: number; meetings: number; pool: number;
  onNavigate: (page: EmployerPage) => void; onAccount: () => void; children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobile, setMobile] = useState(false);
  if (!enabled) return <>{children}</>;
  const panel = scope === 'dsa' ? 'dsa' : 'shortlist';
  const destinations = [
    { id: 'tasks', label: 'Задачи найма', icon: BriefcaseBusiness, count: 0 },
    { id: panel, label: 'Кандидаты', icon: Users, count: 0 },
    ...(scope === 'sfera' ? [{ id: 'meetings', label: 'Встречи', icon: CalendarDays, count: meetings },
      ...(pool ? [{ id: 'pool', label: 'Офферы', icon: FileCheck2, count: pool }] : []),
      ...(count ? [{ id: 'chat', label: 'Сообщения', icon: MessageCircle, count: 0 }] : [])] : []),
  ];
  const active = ['intake', 'review'].includes(page) ? panel : ['meeting', 'book'].includes(page) ? 'meetings' : page === 'offer' ? 'pool' : page;
  return <div className={`ew-shell ${collapsed ? 'ew-collapsed' : ''} ${mobile ? 'ew-mobile-open' : ''}`}>
    <header className="ew-mobile-bar"><button onClick={() => setMobile(true)} aria-label="Открыть меню"><Menu size={22}/></button><strong>HR Vision<span>.</span></strong><button onClick={onAccount} aria-label="Сменить аккаунт">ИП</button></header>
    {mobile && <button className="ew-scrim" aria-label="Закрыть меню" onClick={() => setMobile(false)}/>}
    <aside className="ew-sidebar" aria-label="Рабочее пространство">
      <div className="ew-sidebar-top"><button className="ew-logo" onClick={() => onNavigate('tasks')} aria-label="HR Vision · задачи найма"><svg width="28" height="26" viewBox="0 0 28 26" fill="none" aria-hidden="true"><path d="M3 7.5 14 2l11 5.5v11L14 24 3 18.5v-11Z" stroke="currentColor" strokeWidth="2"/><path d="m3 7.5 11 5.7 11-5.7M14 13v11M8 5l12 5.5" stroke="currentColor" strokeWidth="2"/></svg><span>HR Vision<span className="ew-dot">.</span></span></button><button className="ew-mobile-close" onClick={() => setMobile(false)} aria-label="Закрыть меню"><X size={20}/></button></div>
      <button className="ew-company" onClick={() => onNavigate('tasks')} title="Выбрать задачу"><span className="ew-company-mark">{company.slice(0, 1)}</span><span>{company}</span><ChevronDown size={15}/></button>
      <nav aria-label="Главное меню">{destinations.map(item => <button key={item.id} aria-current={active === item.id ? 'page' : undefined} title={collapsed ? item.label : undefined} onClick={() => { onNavigate(item.id as EmployerPage); setMobile(false); }}><item.icon size={19}/><span>{item.label}</span>{item.count > 0 && <b>{item.count}</b>}</button>)}</nav>
      <div className="ew-sidebar-bottom"><button className="ew-collapse" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? 'Развернуть меню' : 'Свернуть меню'}>{collapsed ? <ChevronsRight size={18}/> : <><ChevronsLeft size={18}/><span>Свернуть</span></>}</button><button className="ew-user" onClick={onAccount} title="Сменить аккаунт"><span className="ew-avatar">ИП</span><span>{name}</span><ChevronDown size={14}/></button></div>
    </aside>
    <div className="ew-content">{children}</div>
  </div>;
}
