import './journey-nav.css';
export function JourneyNav({ active }: { active: 'ats' | 'contacts' | 'agency' | 'candidate-agency' | 'candidate-other' }) {
  const entries = [
    ['ats','Бесплатная ATS','hr-vision-ats-example--overview'],
    ['contacts','База контактов','hr-vision-cjm--contacts'],
    ['agency','Агентство','hr-vision-cjm--agency'],
    ['candidate-agency','Кандидат · агентство','hr-vision-candidate-cjm--agency'],
    ['candidate-other','Кандидат · остальные модели','hr-vision-candidate-cjm--other'],
  ];
  return <nav className="jnav" aria-label="Карты HR Vision">{entries.map(([id,label,story]) => <a key={id} href={`?id=${story}&viewMode=story`} aria-current={active===id?'page':undefined}>{label}</a>)}</nav>;
}
