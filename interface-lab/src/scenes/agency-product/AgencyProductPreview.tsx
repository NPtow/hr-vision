import { useEffect, useState } from 'react';
import { ArrowUpRight, Check, RotateCcw, Settings2, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { CandidateWorkspace } from './CandidateWorkspace';
import { ManagerWorkspace } from './ManagerWorkspace';
import { freshState, type AgencyState } from './model';
import { InterviewMediaProvider, LabMediaControl } from '../interview-review/InterviewMediaContext';
import './theme.css';
import './product.css';

const storageKey = 'hr-vision-agency-preview-v1';
function readState(): AgencyState {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
    if (saved?.version !== 1) return freshState();
    const s = saved.state;
    if (!s || !['unknown','interested','declined'].includes(s.candidateInterest)
      || !['pending','confirmed','reschedule'].includes(s.candidateReply)
      || !['none','hr-requested'].includes(s.candidateInterview)
      || typeof s.meetingSlot !== 'string' || typeof s.candidateQuestion !== 'string' || typeof s.coordinatorQuestion !== 'string'
      || typeof s.candidateTimeRequest !== 'string' || typeof s.candidateDeclineReason !== 'string'
      || typeof s.profileSaved !== 'boolean' || typeof s.shareConfirmed !== 'boolean'
      || !['name','experience','expectations','availability'].every(k => typeof s.profile?.[k] === 'string')
      || !['anna','mikhail','elena'].every(k => ['review','invite','decline'].includes(s.decisions?.[k]?.kind)
        && (s.decisions[k].reason === undefined || typeof s.decisions[k].reason === 'string')
        && (s.decisions[k].question === undefined || typeof s.decisions[k].question === 'string'))) return freshState();
    return s;
  } catch { return freshState(); }
}
export function AgencyProductPreview({ initialRole = 'manager' }: { initialRole?: 'manager' | 'candidate' }) {
  const [role, setRole] = useState(initialRole);
  const [state, setState] = useState<AgencyState>(readState);
  const [resetId, setResetId] = useState(0);
  const [toast, setToast] = useState('');
  const [storageAvailable, setStorageAvailable] = useState(true);
  useEffect(() => { setRole(initialRole); }, [initialRole]);
  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify({ version: 1, state })); }
    catch { setStorageAvailable(false); }
  }, [state]);
  useEffect(() => {
    const sync = (event: StorageEvent) => { if (event.key === storageKey) setState(readState()); };
    window.addEventListener('storage', sync); return () => window.removeEventListener('storage', sync);
  }, []);
  useEffect(() => { if (!toast) return; const id = setTimeout(() => setToast(''), 6500); return () => clearTimeout(id); }, [toast]);
  const update = (patch: Partial<AgencyState>) => setState(value => ({ ...value, ...patch }));
  function reset() { window.dispatchEvent(new Event('hr-vision-pipeline-reset')); setState(freshState()); setResetId(v => v + 1); setToast('Пример сброшен. Можно пройти сценарий заново.'); }
  return <InterviewMediaProvider key={resetId}><div className={`agency-product agency-product-theme ${role === 'manager' ? 'ag-employer-shell' : ''}`}>
    {role === 'manager' ? <div className="ag-compact-shell"><a href="?id=hr-vision-product--manager&viewMode=story" className="ag-compact-brand">HR Vision<span>.</span></a><span className="ag-compact-boundary">UI · данные и действия в этом браузере</span><details className="ag-view-settings"><summary aria-label="Настройки просмотра"><Settings2 size={16}/><span>Просмотр</span></summary><div className="ag-view-settings-content"><p>Проверка полного пути работодателя. Звонок, отправка оффера и ответы кандидата моделируются; внешние сервисы не подключены.</p>{!storageAvailable && <p role="status">Хранилище браузера недоступно. Изменения сохраняются только до закрытия страницы.</p>}<LabMediaControl/><nav aria-label="Другие материалы"><a href="?id=hr-vision-panel-alternatives--gallery&viewMode=story&variant=1">10 вариантов подборки<ArrowUpRight size={13}/></a><a href="?id=hr-vision-xpm--overview&viewMode=story">Карта процесса<ArrowUpRight size={13}/></a><a href="?id=hr-vision-product--candidate&viewMode=story">Прежний интерфейс кандидата<ArrowUpRight size={13}/></a></nav><button className="ag-shell-reset" onClick={reset}><RotateCcw size={14}/>Сбросить пример</button></div></details></div> : <>
    <div className="ag-preview-bar"><span><i/>UI-пример <span className="ag-preview-note">· вымышленные данные · {storageAvailable ? 'действия в этом браузере' : 'без сохранения после закрытия'}</span></span><div className="ag-role-switch" role="group" aria-label="Посмотреть интерфейс"><button onClick={() => { setRole('manager'); setToast(''); }} aria-pressed={false}>Нанимающий</button><button onClick={() => { setRole('candidate'); setToast(''); }} aria-pressed={true}>Кандидат</button></div><button className="ag-reset" onClick={reset} aria-label="Сбросить пример" title="Сбросить пример"><RotateCcw size={14}/></button></div>
    <header className="ag-product-header"><a className="ag-wordmark" href="?id=hr-vision-product--manager&viewMode=story" aria-label="HR Vision, панель нанимающего"><span className="ag-brand-mark"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 1.5 14.5 8l7-2.5-4 6.5 5 5-7-.5-3.5 6-2.5-7-7 1.5L7 11 2.5 5.5 9.5 7 12 1.5Z" fill="currentColor"/></svg></span>HR Vision<span className="ag-wordmark-dot">.</span></a><div className="ag-header-context">Следующий шаг в карьере</div><div className="ag-header-person"><span className="ag-avatar tiny neutral">АМ</span><span>{state.profile.name}</span></div></header>
    </>}
    <div key={`${role}-${resetId}`} className="ag-workspace">{role === 'manager' ? <ManagerWorkspace state={state} update={update} notify={setToast}/> : <CandidateWorkspace state={state} update={update} notify={setToast}/>}</div>
    {role !== 'manager' && <footer className="ag-lab-footer"><span>HR Vision · Агентский подбор</span><a href="?id=hr-vision-xpm--overview&viewMode=story">Карта процесса<ArrowUpRight size={13}/></a><a href="?id=hr-vision-agency--overview&viewMode=story">Предыдущие экраны<ArrowUpRight size={13}/></a></footer>}
    {toast && <div className="ag-toast" role="status"><Check size={18}/><span>{toast}</span><Button variant="ghost" size="icon-sm" aria-label="Закрыть уведомление" onClick={() => setToast('')}><X size={14}/></Button></div>}
  </div></InterviewMediaProvider>;
}
