import { useState } from 'react';
import { Info, X } from 'lucide-react';
import { InterviewFeedbackLab } from '../interview-feedback-lab/InterviewFeedbackLab';
import './review-design-lab.css';
import './variants/system.css';
import './variants/soft.css';
import './variants/editorial.css';
import './variants/compact.css';
import './variants/continuous.css';
import './variants/focus.css';

const designs = [
  { id: 'system', name: 'Инспектор', idea: 'Общая линия начала и спокойные текстовые вкладки. Три режима работают внутри одной панели.' },
  { id: 'soft', name: 'Поверхности', idea: 'Мягкие поверхности связывают поля, выбор навыка, ответы и действия в одну систему.' },
  { id: 'editorial', name: 'Типографика', idea: 'Выбранный вариант: исходный переключатель с иконками, единая типографика и интервалы расшифровки, помощника и объяснений.' },
  { id: 'compact', name: 'Компактный', idea: 'Точная геометрия настольных контролов и сдержанные выбранные состояния.' },
  { id: 'continuous', name: 'Сегменты', idea: 'Одна геометрия выбора для вкладок, навыков и ответов под видео.' },
  { id: 'focus', name: 'Контекст', idea: 'Внимание на текущем фрагменте: объяснение, источник и вопрос образуют связанный путь.' },
] as const;

export type ReviewDesign = typeof designs[number]['id'];

/** Six independent visual refinements of the real candidate review.
 * Changing a design never remounts the player or alters assessment data. */
export function ReviewDesignLab({ initialDesign = 'editorial' }: { initialDesign?: ReviewDesign }) {
  const [design, setDesign] = useState<ReviewDesign>(() => {
    const query = new URLSearchParams(location.search).get('design');
    return designs.find(item => item.id === query)?.id ?? initialDesign;
  });
  const [about, setAbout] = useState(false);
  const active = designs.find(item => item.id === design)!;

  function choose(next: ReviewDesign) {
    setDesign(next);
    const url = new URL(location.href);
    url.searchParams.set('design', next);
    history.replaceState(null, '', url);
  }

  return <div className="review-design-lab" data-review-design={design}>
    <InterviewFeedbackLab preferred controls={<>
      <nav className="review-design-lab__choices" aria-label="Шесть вариантов интерфейса">
        {designs.map(item => <button type="button" key={item.id} aria-pressed={design === item.id} onClick={() => choose(item.id)}>{item.name}</button>)}
      </nav>
      <label className="review-design-lab__select"><span>Вариант</span><select aria-label="Вариант интерфейса" value={design} onChange={event => choose(event.target.value as ReviewDesign)}>{designs.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <button type="button" className="review-design-lab__info" aria-label="О варианте" aria-expanded={about} onClick={() => setAbout(value => !value)}><Info size={16}/></button>
    </>}/>
    {about && <aside className="review-design-lab__about" aria-label="Описание варианта">
      <button type="button" aria-label="Закрыть описание" onClick={() => setAbout(false)}><X size={16}/></button>
      <h2>{active.name}</h2><p>{active.idea}</p>
      <p>Шесть независимых работ на одном кандидате. Вкладки, видео и ответы остаются на текущем месте при переключении дизайна.</p>
      <div><a href="https://github.com/pbakaus/impeccable" target="_blank" rel="noreferrer">Impeccable</a><a href="https://github.com/buidangminh23/spacing-skill" target="_blank" rel="noreferrer">Spacing & Rhythm</a><a href="https://developer.apple.com/design/human-interface-guidelines/layout" target="_blank" rel="noreferrer">Apple Design</a></div>
    </aside>}
  </div>;
}
