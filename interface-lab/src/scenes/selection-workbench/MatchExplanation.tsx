import { indexLabel, percentLabel, pointsLabel, scoreLabel, type RecommendationItem, type RecommendationResult, type VacancyProfile } from './matching-contract';
import { ModelDistribution, SkillDisclosure } from './ModelDistribution';
import { RequirementDistribution } from './RequirementDistribution';
import { activeRequiredLevel, importanceLabel, probability } from './distribution-data';
const reasons: Record<string, string> = { insufficient_data: 'Недостаточно данных', zero_importance: 'Не требуется для вакансии', unknown_importance: 'Важность не определена: эффективный вес 0', zero_weight: 'Не требуется для вакансии', irrelevant: 'Не требуется', not_required: 'Не требуется', missing_value: 'Нет подтверждённых данных', requirement_unknown: 'Требование не уточнено', candidate_unknown: 'Данные кандидата не уточнены', minimum_met: 'Стаж подтверждён', below_minimum: 'Недостаточный стаж', allowed_id_match: 'Соответствует', no_allowed_id_match: 'Не соответствует', match: 'Соответствует', mismatch: 'Не соответствует' };
const checkNames: Record<string, string> = { location: 'География работы', employment: 'Формат занятости', industry_experience: 'Опыт в отрасли' };
export function MatchExplanation({ item, result, profile }: { item: RecommendationItem; result: RecommendationResult; profile: VacancyProfile }) {
  return <div className="sl-match-explanation md-match-detail" aria-label="Расчёт соответствия">
    <div className="md-match-overview"><div><h2>Соответствие вакансии</h2><strong>{scoreLabel(item)}</strong></div><p>Учтено {item.coverage.included_parameters} из {item.coverage.total_parameters} навыков</p></div>
    {item.score === null && <p>Балл не рассчитан: нет сопоставимых навыков с ненулевым весом. Это не нулевой балл.</p>}
    <h3>Обязательные условия</h3><dl className="sl-check-results">{item.required_checks.map(c => <div key={c.field}><dt>{checkNames[c.field] ?? c.field}</dt><dd>{({ pass: 'Подтверждено', fail: 'Не соответствует', unknown: 'Нужно уточнить' } as const)[c.status]}<small>{reasons[c.reason] ?? c.reason}</small></dd></div>)}</dl>
    <h3>Соответствие по навыкам</h3>
    {item.parameters.map(p => {
      const requirement = profile.parameters.find(v => v.parameter_id === p.parameter_id);
      const requiredLevel = activeRequiredLevel(p, requirement);
      const required = requirement?.levels.find(l => l.level_id === requiredLevel);
      const options = (p.levels ?? []).map(level => ({ id: level.level_id, label: requirement?.levels.find(v => v.level_id === level.level_id)?.label ?? level.level_id, description: requirement?.levels.find(v => v.level_id === level.level_id)?.description, probability: probability(level.p_raw) }));
      return <SkillDisclosure key={p.parameter_id} name={`matching-skills-${item.candidate_id}`} summary={<><span>{requirement?.title ?? p.title ?? p.parameter_id}</span><span>{p.excluded ? 'Не учитывается' : p.score_points_contribution !== undefined ? `Вклад ${pointsLabel(p.score_points_contribution)} балла` : `Индекс навыка ${indexLabel(p.m ?? null)}`}</span></>}>
        <div className="md-pair md-match-skill"><ModelDistribution headingLevel={4} title="Кандидат" requiredLevel={requiredLevel} data={{ options: [...options, { id: 'insufficient_data', label: 'Недостаточно данных', probability: probability(p.insufficient_data_probability), unknown: true }], choice: null, confidence: probability(p.model_confidence) }}/>
          <div className="md-match-context"><h4>Требование вакансии</h4><p><strong>{requirement?.importance === 'insufficient_data' || p.exclusion_reason === 'unknown_importance' ? 'Не участвует в расчёте' : required?.label ?? (requirement?.importance === 0 || p.weight === 0 ? 'Не требуется' : 'Не определён')}</strong></p>{required && <p>{required.description}</p>}
            {p.utility_mode === 'higher_level_linear' && <p>Целевой уровень не задан. Чем выше уровень кандидата, тем выше балл; требуемый уровень не подставляется.</p>}
            {p.weight_share != null && <p>Доля в расчёте: {percentLabel(p.weight_share)} · вклад: {pointsLabel(p.score_points_contribution)} балла из 100.</p>}
            <p>Важность: <strong>{requirement ? importanceLabel(requirement.importance) : p.weight ?? 'Не определена'}</strong></p>
            {p.excluded && <p>{reasons[p.exclusion_reason ?? ''] ?? p.exclusion_reason ?? 'Нет данных для расчёта'}. Навык не входит в итоговый балл.</p>}
            {requirement && <details className="md-calculation"><summary>Ответ модели о вакансии</summary><RequirementDistribution parameter={requirement}/></details>}
          </div>
        </div>
        {!p.excluded && <details className="md-calculation"><summary>Числа в расчёте</summary><div className="sl-scroll"><table><thead><tr><th>Уровень</th><th>Ответ модели</th><th>В расчёте</th><th>Соответствие</th><th>Вклад</th></tr></thead><tbody>{p.levels?.map(l => <tr key={l.level_id}><td>{requirement?.levels.find(v => v.level_id === l.level_id)?.label ?? l.level_id}</td><td>{percentLabel(l.p_raw)}</td><td>{percentLabel(l.q)}</td><td>{indexLabel(l.u)}</td><td>{indexLabel(l.contribution)}</td></tr>)}</tbody></table></div></details>}
      </SkillDisclosure>;
    })}
    <details className="md-calculation"><summary>Как рассчитан балл</summary><p>Сервер возвращает балл от 0 до 100. Это соответствие по навыкам, а не вероятность найма. Прежние результаты без баллов показаны как исходный индекс.</p><p>При заданном требуемом уровне используется tanh(k × (уровень кандидата − требуемый уровень)). Если уровень не задан, уровни от первого до последнего дают от 0 до 100: для пяти уровней — 0, 25, 50, 75, 100. Сервер учитывает распределения вероятностей, затем важность навыков.</p><p>Вероятности нормируются только для расчёта. На графике показаны исходные ответы. Навыки с недостаточными данными кандидата, нулевой или неизвестной важностью исключаются из расчёта. Для неизвестной важности применяется эффективный вес 0, исходный ответ сохраняется.</p><p>Учтённый вес: {item.coverage.included_weight}; исключённый: {item.coverage.excluded_weight}. Политика: {result.policy.version}, k = {result.policy.k}.</p><p className="sl-hash">{result.recommendation_id}</p></details>
  </div>;
}
