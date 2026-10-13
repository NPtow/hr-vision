import { useEffect, useId, useState, type ReactNode } from 'react';
import { CalendarDays, Check, ChevronDown, ClipboardList, FileText, Menu, Plus, Users, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { DropdownMenu, Tooltip } from 'radix-ui';
import logo from './assets/hr-vision-logo.svg';
import { COMPANY_SITE_URL } from '../../lib/employer-entry';
import './candidate-surfaces.css';
import './shell-navigation.css';

export type EmployerPage = 'dsa' | 'tasks' | 'create-vacancy' | 'shortlist' | 'intake' | 'book' | 'meetings' | 'meeting' | 'review' | 'pool' | 'offer' | 'chat' | 'vacancies' | 'schedule';
export type Scope = 'sfera' | 'dsa';
export type EmployerVacancyOption = { id: string; role: string };
export function EmployerShell({ enabled = true, page, scope, vacancy, vacancyId, vacancies = [], onVacancyChange, navigationBusy, name, hasOffers, candidateContext, onNavigate, onAccount, onLogout, accountBusy, children }: {
  enabled?: boolean; page: EmployerPage; scope: Scope; vacancy: string; name: string; hasOffers: boolean;
  vacancyId?: string; vacancies?: EmployerVacancyOption[];
  onVacancyChange?: (id: string) => void; navigationBusy?: boolean;
  candidateContext?: 'vacancy' | 'queue';
  onLogout?: () => void; accountBusy?: boolean;
  onNavigate: (page: EmployerPage) => void; onAccount: () => void; children: ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuId = useId();
  useEffect(() => { if (enabled) window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); setMenuOpen(false); }, [enabled, page, scope, candidateContext]);
  if (!enabled) return <>{children}</>;
  const panel: EmployerPage = scope === 'dsa' ? 'dsa' : 'shortlist';
  const active = page === 'create-vacancy' || (page === panel && candidateContext === 'vacancy') ? 'tasks' : page === 'intake' ? panel : ['meeting', 'book', 'review'].includes(page) ? 'meetings' : page === 'offer' ? 'pool' : page;
  const navigate = (next: EmployerPage) => { setMenuOpen(false); onNavigate(next); };
  const canChooseVacancy = page === panel && candidateContext !== 'vacancy' && !!onVacancyChange && vacancies.length > 0;
  const sections = [
    { page: 'tasks' as const, label: 'Задачи', Icon: ClipboardList },
    { page: panel, label: 'Кандидаты', Icon: Users },
    { page: 'meetings' as const, label: 'Встречи', Icon: CalendarDays },
    ...(hasOffers ? [{ page: 'pool' as const, label: 'Офферы', Icon: FileText }] : []),
  ];
  return <div className={`ew-shell${page === 'shortlist' || page === 'dsa' ? ' ew-candidates-view' : ''}`}>
    <header className="ew-topbar">
      <button className="ew-menu-toggle" aria-label={menuOpen ? 'Закрыть меню' : 'Открыть меню'} aria-expanded={menuOpen} aria-controls={menuId} onClick={() => setMenuOpen(open => !open)}>{menuOpen ? <X size={20}/> : <Menu size={20}/>}</button>
      <button className="ew-logo" onClick={() => onNavigate('tasks')} aria-label="HR Vision · задачи найма"><img src={logo} alt="HR Vision"/></button>
      {onLogout ? <DropdownMenu.Root><DropdownMenu.Trigger asChild><button className="ew-account" disabled={accountBusy} aria-label={`Аккаунт: ${name}`}>{name}<ChevronDown size={16}/></button></DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className="ew-root ew-account-menu" align="end" sideOffset={8}><DropdownMenu.Item asChild><a href={COMPANY_SITE_URL}>На сайт HR Vision</a></DropdownMenu.Item><DropdownMenu.Item disabled={accountBusy} onSelect={onLogout}>Выйти из кабинета</DropdownMenu.Item></DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root> : <button className="ew-account" onClick={onAccount}>{name}<ChevronDown size={16}/></button>}
    </header>
    <div className="ew-body">
      <aside id={menuId} className="ag-sidebar ew-sidebar" data-open={menuOpen} aria-label="Разделы работодателя">
        <Tooltip.Provider delayDuration={200} skipDelayDuration={100}>
          <nav aria-label="Кабинет нанимающего">
            {sections.map(({ page: destination, label, Icon }) => <Tooltip.Root key={destination}>
              <Tooltip.Trigger asChild><button className={active === destination ? 'active' : ''} aria-label={label} aria-current={active === destination ? 'page' : undefined} disabled={navigationBusy} onClick={() => navigate(destination)}><Icon size={21} aria-hidden="true"/><span className="ew-navigation-label">{label}</span></button></Tooltip.Trigger>
              <Tooltip.Portal><Tooltip.Content className="ew-navigation-tooltip" side="right" sideOffset={12} collisionPadding={12}>{label}</Tooltip.Content></Tooltip.Portal>
            </Tooltip.Root>)}
          </nav>
        </Tooltip.Provider>
      </aside>
      <div className="ew-workspace">
        {page !== 'tasks' && page !== 'create-vacancy' && page !== 'meetings' && <section className="ew-vacancy-header" aria-label="Текущая вакансия">
          <div className="ew-vacancy-title"><h1>{canChooseVacancy ? <DropdownMenu.Root><DropdownMenu.Trigger asChild><button className="ew-vacancy-switcher" aria-label={`Выбрать вакансию: ${vacancy}`} disabled={navigationBusy}>{vacancy}</button></DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className="ew-root ew-vacancy-menu" align="start" sideOffset={12} collisionPadding={16}><DropdownMenu.RadioGroup value={vacancyId} onValueChange={id => { if (id !== vacancyId) onVacancyChange?.(id); }}>{vacancies.map(option => <DropdownMenu.RadioItem className="ew-vacancy-option" key={option.id} value={option.id}><span>{option.role}</span><DropdownMenu.ItemIndicator><Check size={17} aria-hidden="true"/></DropdownMenu.ItemIndicator></DropdownMenu.RadioItem>)}</DropdownMenu.RadioGroup></DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root> : vacancy}</h1>{page !== 'intake' && <Button variant="secondary" className="ew-add-candidates" aria-label="Добавить кандидатов" onClick={() => navigate('intake')}><Plus size={16}/><span className="ew-add-candidates-label">Добавить кандидатов</span></Button>}</div>
        </section>}
        <div className="ew-content">{children}</div>
      </div>
    </div>
  </div>;
}
