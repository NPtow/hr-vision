import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronDown, ExternalLink } from 'lucide-react';
import { AgencyProduct } from '../agency-product/AgencyProduct';
import './interview-feedback-lab.css';

type Variant = 'tiles' | 'strip' | 'dialogue';
type LabData = { vacancyId: string; candidates: { intakeId: string; name: string }[] };
const variants = [
  { id: 'tiles', name: 'Плитки', reference: 'Maze', url: 'https://help.maze.co/articles/6985718114-survey-participants-with-an-opinion-scale' },
  { id: 'strip', name: 'Полоса', reference: 'Apple', url: 'https://developer.apple.com/design/human-interface-guidelines/segmented-controls' },
  { id: 'dialogue', name: 'Диалог', reference: 'Typeform', url: 'https://www.typeform.com/templates/yes-no-questions-survey' },
] as const;

/** Local comparison harness. Candidate data and sessions are supplied by the
 * loopback preview server, never embedded in a Storybook bundle. */
export function InterviewFeedbackLab({ preferred = false, controls }: { preferred?: boolean; controls?: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<LabData>();
  const [error, setError] = useState('');
  const [variant, setVariant] = useState<Variant>(() => {
    if (preferred) return 'tiles';
    const query = new URLSearchParams(location.search).get('panel');
    return variants.find(item => item.id === query)?.id ?? 'tiles';
  });
  const [references, setReferences] = useState(false);
  const active = variants.find(item => item.id === variant)!;
  const selected = new URLSearchParams(location.search).get('intakeId') ?? '';

  useEffect(() => {
    const controller = new AbortController();
    void fetch('/api/feedback-lab/candidates', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Откройте этот макет через локальный сервер предпросмотра.');
      return await response.json() as LabData;
    }).then(value => {
      const url = new URL(location.href);
      url.searchParams.set('screen', 'shortlist');
      url.searchParams.set('pool', 'proposed');
      url.searchParams.set('vacancyId', value.vacancyId);
      if (preferred) url.searchParams.set('panel', 'tiles');
      if (!value.candidates.some(item => item.intakeId === url.searchParams.get('intakeId'))) {
        url.searchParams.set('intakeId', value.candidates[0].intakeId);
      }
      history.replaceState(null, '', url);
      setData(value);
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Не удалось открыть макет.'); });
    return () => controller.abort();
  }, [preferred]);

  useEffect(() => {
    if (!data || !root.current) return;
    // Open the existing source control, exercising the real player and question
    // binding. No synthetic questions or production feedback writes are used.
    const observer = new MutationObserver(() => {
      const source = root.current?.querySelector<HTMLButtonElement>('.ia-orbit__source');
      if (source) { observer.disconnect(); source.click(); }
    });
    observer.observe(root.current, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [data]);

  function chooseVariant(next: Variant) {
    setVariant(next);
    const url = new URL(location.href);
    url.searchParams.set('panel', next);
    history.replaceState(null, '', url);
  }

  return <div ref={root} className="feedback-lab" data-feedback-variant={variant}>
    <header className="feedback-lab__toolbar" aria-label="Управление макетом">
      {controls ?? <span className="feedback-lab__title">{preferred ? 'Макет · Плитки' : 'Под видео'}</span>}
      {!controls && !preferred && <nav className="feedback-lab__variants" aria-label="Вариант панели">
        {variants.map(item => <button key={item.id} type="button" aria-pressed={variant === item.id} onClick={() => chooseVariant(item.id)}>{item.name}</button>)}
      </nav>}
      {data && <label className="feedback-lab__candidate"><span className="iv-sr-only">Кандидат</span>
        <select value={selected || data.candidates[0].intakeId} onChange={event => {
          const url = new URL(location.href); url.searchParams.set('intakeId', event.target.value); location.assign(url);
        }}>{data.candidates.map(person => <option key={person.intakeId} value={person.intakeId}>{person.name}</option>)}</select><ChevronDown size={13}/>
      </label>}
      {!controls && <button className="feedback-lab__reference-toggle" type="button" aria-expanded={references} onClick={() => setReferences(value => !value)}>Референсы</button>}
      <span className="feedback-lab__local">Локальный макет</span>
    </header>
    {references && <aside className="feedback-lab__references">
      <strong>{active.name}</strong><p>{variant === 'tiles' ? 'Три одинаковые компактные кнопки: нет, не знаю, да. Крупный текст, видео занимает основную часть карточки.' : variant === 'strip' ? 'Один блок выбора. Подложка перемещается между ответами, у всей панели общая геометрия.' : 'Вопрос и ответы читаются рядом. Вертикальный список даёт каждой кнопке полную строку.'}</p>
      <a href={active.url} target="_blank" rel="noreferrer">Референс: {active.reference}<ExternalLink size={13}/></a>
      <p>Ответы записываются в локальную копию. Боевые данные не меняются.</p>
    </aside>}
    {error ? <p className="feedback-lab__error">{error}</p> : data ? <AgencyProduct initialRole="manager"/> : <p className="feedback-lab__loading">Открываем кабинет…</p>}
  </div>;
}
