import type { VacancyParameter } from './matching-contract';
import { ModelDistribution } from './ModelDistribution';
import { requirementDistributions, importanceLabel, levelLabel } from './distribution-data';

export function RequirementDistribution({ parameter: p }: { parameter: VacancyParameter }) {
  const raw = requirementDistributions(p);
  const levelChanged = raw.level.choice !== null && (p.required_level_id ?? 'insufficient_data') !== raw.level.choice;
  const importanceChanged = raw.importance.choice !== null && (p.importance === 'insufficient_data' ? p.importance : `I${p.importance}`) !== raw.importance.choice;
  return <div className="md-requirement-detail">
    {(levelChanged || importanceChanged) && <p className="md-manual">Для подбора используются уточнённые требования: {levelLabel(p).toLocaleLowerCase('ru')}, важность — {importanceLabel(p.importance).toLocaleLowerCase('ru')}. Ниже сохранён исходный ответ модели.</p>}
    {p.importance === 0 && <p className="md-context">Навык не участвует в подборе. Уточнять уровень не нужно.</p>}
    {typeof p.importance === 'number' && p.importance > 0 && !p.required_level_id && <p className="md-context">Целевой уровень не задан. Сравнение учитывает навык по правилу «чем выше уровень кандидата, тем выше балл».</p>}
    {p.importance === 'insufficient_data' && <p className="md-context">Важность навыка не определена. Уточните её в требованиях вакансии для расчёта.</p>}
    <div className="md-pair"><ModelDistribution key={p.parameter_id + '-level'} title="Требуемый уровень" data={raw.level}/><ModelDistribution key={p.parameter_id + '-importance'} title="Важность" data={raw.importance}/></div>
  </div>;
}
