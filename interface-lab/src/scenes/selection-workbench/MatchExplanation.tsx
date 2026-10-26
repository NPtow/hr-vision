import { indexLabel, percentLabel, type RecommendationItem, type RecommendationResult, type VacancyProfile } from './matching-contract';
import { ModelDistribution, SkillDisclosure } from './ModelDistribution';
import { RequirementDistribution } from './RequirementDistribution';
import { activeRequiredLevel, importanceLabel, probability } from './distribution-data';
const reasons: Record<string, string> = { insufficient_data: 'Недостаточно данных', zero_importance: 'Не требуется для вакансии', zero_weight: 'Не требуется для вакансии', irrelevant: 'Не требуется', not_required: 'Не требуется', missing_value: 'Нет подтверждённых данных', requirement_unknown: 'Требование не уточнено', candidate_unknown: 'Данные кандидата не уточнены', minimum_met: 'Стаж подтверждён', below_minimum: 'Недостаточный стаж', allowed_id_match: 'Соответствует', no_allowed_id_match: 'Не соответствует', match: 'Соответствует', mismatch: 'Не соответствует' };
const checkNames: Record<string, string> = { location: 'География работы', employment: 'Формат занятости', industry_experience: 'Опыт в отрасли' };
export function MatchExplanation({ item, result, profile }: { item: RecommendationItem; result: RecommendationResult; profile: VacancyProfile }) {
  return <div className="sl-match-explanation md-match-detail" aria-label="Расчёт соответствия">
    <div className="md-match-overview"><div><h2>Соответствие вакансии</h2><strong>{indexLabel(item.score)}</strong></div><p>Учтено {item.coverage.included_parameters} из {item.coverage.total_parameters} навыков</p></div>
    <h3>Обязательные условия</h3><dl className="sl-check-results">{item.required_checks.map(c => <div key={c.field}><dt>{checkNames[c.field] ?? c.field}</dt><dd>{({ pass: 'Подтверждено', fail: 'Не соответствует', unknown: 'Нужно уточнить' } as const)[c.status]}<small>{reasons[c.reason] ?? c.reason}</small></dd></div>)}</dl>
    <h3>Соответствие по навыкам</h3>
    {item.parameters.map(p => {
      const requirement = profile.parameters.find(v => v.parameter_id === p.parameter_id);
      const requiredLevel = activeRequiredLevel(p, requirement);
      const required = requirement?.levels.find(l => l.level_id === requiredLevel);
      const options = (p.levels ?? []).map(level => ({ id: level.level_id, label: requirement?.levels.find(v => v.level_id === level.level_id)?.label ?? level.level_id, description: requirement?.levels.find(v => v.level_id === level.level_id)?.description, probability: probability(level.p_raw) }));
      return <SkillDisclosure key={p.parameter_id} name={`matching-skills-${item.candidate_id}`} summary={<><span>{requirement?.title ?? p.title ?? p.parameter_id}</span><span>{p.excluded ? 'Не учитывается' : `Соответствие ${indexLabel(p.m ?? null)}`}</span></>}>
        <div className="md-pair md-match-skill"><ModelDistribution headingLevel={4} title="Кандидат" requiredLevel={requiredLevel} data={{ options: [...options, { id: 'insufficient_data', label: 'Недостаточно данных', probability: probability(p.insufficient_data_probability), unknown: true }], choice: null, confidence: probability(p.model_confidence) }}/>
          <div className="md-match-context"><h4>Требование вакансии</h4><p><strong>{required?.label ?? (requirement?.importance === 0 || p.weight === 0 ? 'Не требуется' : 'Не определён')}</strong></p>{required && <p>{required.description}</p>}
            <p>Важность: <strong>{requirement ? importanceLabel(requirement.importance) : p.weight ?? 'Не определена'}</strong></p>
            {p.excluded && <p>{reasons[p.exclusion_reason ?? ''] ?? p.exclusion_reason ?? 'Нет данных для расчёта'}. Навык не входит в итоговый индекс.</p>}
            {requirement && <details className="md-calculation"><summary>Ответ модели о вакансии</summary><RequirementDistribution parameter={requirement}/></details>}
          </div>
        </div>
        {!p.excluded && <details className="md-calculation"><summary>Числа в расчёте</summary><div className="sl-scroll"><table><thead><tr><th>Уровень</th><th>Ответ модели</th><th>В расчёте</th><th>Соответствие</th><th>Вклад</th></tr></thead><tbody>{p.levels?.map(l => <tr key={l.level_id}><td>{requirement?.levels.find(v => v.level_id === l.level_id)?.label ?? l.level_id}</td><td>{percentLabel(l.p_raw)}</td><td>{percentLabel(l.q)}</td><td>{indexLabel(l.u)}</td><td>{indexLabel(l.contribution)}</td></tr>)}</tbody></table></div></details>}
      </SkillDisclosure>;
    })}
    <details className="md-calculation"><summary>Как рассчитан индекс</summary><p>Индекс от −1 до +1 показывает соответствие требованиям, а не вероятность найма.</p><p>Соответствие уровня = tanh(k × (уровень кандидата − требуемый уровень)). Сначала учитываются вероятности уровней каждого навыка, затем — важность навыков для вакансии.</p><p>Вероятности нормируются только для расчёта. На графике показаны исходные ответы. Неизвестные навыки и навыки с важностью 0 не входят в знаменатель.</p><p>Учтённый вес: {item.coverage.included_weight}; исключённый: {item.coverage.excluded_weight}. Политика: {result.policy.version}, k = {result.policy.k}.</p><p className="sl-hash">{result.recommendation_id}</p></details>
  </div>;
}
