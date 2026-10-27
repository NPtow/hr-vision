import type { Candidate } from '../types';

const text = (value: unknown) => typeof value === 'string' && value.trim() ? value.trim() : 'Не указано';

/** Only structured profile fields supplied by the authorized candidate directory. */
export function ProfileFacts({ candidate }: { candidate: Candidate }) {
  return <>
    <section className="cp-about"><h2>О себе</h2><p>{typeof candidate.about === 'string' && candidate.about.trim() ? candidate.about : 'Информация о себе не указана.'}</p></section>
    <section className="cp-facts"><h2>Условия работы</h2><dl>
      <div><dt>Ожидания по доходу</dt><dd>Не указано</dd></div>
      <div><dt>География</dt><dd>{text(candidate.city)}</dd></div>
      <div><dt>Формат</dt><dd>Не указано</dd></div>
      <div><dt>Занятость</dt><dd>Не указано</dd></div>
      <div><dt>Готовность к выходу</dt><dd>Не указано</dd></div>
    </dl></section>
  </>;
}
