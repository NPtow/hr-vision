# Контракты ядра подбора v1

Владелец реализации и интеграции: тред «Ядро». Потребитель UI: тред «Верстак».
Основа: восстановленная architecture-v1.md бокового обсуждения 26.10.2026.

## Границы

1. `assessment-core`: независимый профиль кандидата по полному CV и всем
   выбранным актуальным интервью. Существующий `full-corpus-level-choice-v1`
   сохраняется. Вакансия не входит в план/ID кандидата.
2. `vacancy-profile`: отдельный HTTP-процесс, собственная SQLite и очередь.
   Каждый параметр получает весь снимок вакансии; два Choice-вопроса — уровень
   и важность. Кандидатские данные не передаются. Ответ JEV сохраняется до
   интерпретации, неизвестный исход не повторяется автоматически.
3. `recommendations`: отдельный HTTP-процесс и SQLite. Получает готовые
   профили и структурированные факты; проверяет обязательные условия, считает
   tanh по распределению и сохраняет воспроизводимую выдачу. Нет модели.
4. Авторизованный HR-адаптер владеет привязками и текущими версиями; UI не
   считает формулу. Чтение не запускает модель или доставку приглашений.

Общий каталог `rubrics/sales-v1.json` имеет то же содержимое и hash, что
`services/assessment-core/rubrics/analytics-baseline.json`. Каталог поставляется
в каждом релизе, без отдельного сервиса и без чтения чужой БД.

## Профиль вакансии

POST `/v1/vacancy-profiles`: `{vacancy, rubric}`.
`vacancy`: `{vacancy_id, revision, data}`; `data` — полный JSON-снимок заполненных
полей, задач, описания и уточнений вакансии. `revision` — hash канонического
снимка владельца. Ограничения размера отклоняют весь вход, не режут текст.

GET `/v1/vacancy-profiles/{run_id}` возвращает
`{run_id,status,profile,error,vacancy_revision,rubric_version,model}`.
Статусы: `queued,sent,response_saved,completed,failed,outcome_unknown`.

Завершённый `profile`:
`{contract_version:"vacancy-level-importance-v1", profile_id, run_id,
vacancy_id, source_revision, vacancy_sha256, rubric_id, rubric_version,
rubric_sha256, importance_version, model, parameters, usage}`.

Параметр: `{parameter_id,title,levels,required_level_id,importance,status,
raw_answers}`. `levels` сохраняет ID, названия и описания каталога.
`raw_answers:{level,importance}` сохраняет оба полных Choice-ответа.
Важность: `0,1,2,3` и отдельное `insufficient_data`; неизвестность не равна 0.
`status`: `ready`, `irrelevant` (известный 0), `needs_review`.
`profile_id` первоначального результата равен `run_id`; ручные изменения
образуют новый ID/ревизию с автором, сохраняя базовый ответ JEV.
Профиль с неизвестным требованием положительно взвешенного параметра или
неизвестной важностью требует уточнения, а не частичного успешного матча.

Начальный транспорт JEV переиспользуется из assessment-core, общая библиотека
собирается в каждый артефакт без runtime-зависимости от процесса кандидата.
Никаких автоматических оплачиваемых повторов POST.

## Обязательные факты

`vacancy_fields`:
```
{revision,
 location:{required:boolean|null,allowed_ids:string[]|null},
 employment:{required:boolean|null,allowed_ids:string[]|null},
 industry_experience:{required:boolean|null,industry_id:string|null,minimum_months:int|null}}
```
`candidate_fields[]`:
```
{candidate_id,revision,work_locations:string[]|null,
 employment_types:string[]|null,industry_months:{industry_id:int}|null}
```

Это подтверждённые структурированные поля, не вывод из текста CV/вакансии.
Город проживания не превращается в предпочтение места работы. Общий стаж не
превращается в стаж нужной отрасли. Неоднозначное значение остаётся null.
`required:false` → pass/not_required; null → unknown/requirement_unknown.
Локации и занятость сопоставляются точными каноническими ID; опыт — месяцами
в той же отрасли. Проверка возвращает pass/fail/unknown, причину и ревизии.
Любой fail исключает только выдачу вакансии. Unknown оставляет строку видимой
для ручного апрува и не превращается в pass при высоком балле.

## Рекомендации

POST `/v1/recommendations`:
`{vacancy_profile,candidate_profiles,candidate_fields,vacancy_fields,policy}`.
`candidate_profiles` — завершённые неизменные результаты существующего
assessment-core. У всех совпадают каталог и его hash. Явный конечный пул,
не более 1000; превышение отклоняется целиком. HR-адаптер отдельно сохраняет
ожидающих оценки людей и сообщает о них UI, не теряя их из запрошенного пула.

`policy:{version:"tanh-v1",k:1,probability_mode:"conditional_levels"}`.
Пользователь подтвердил 26.10.2026: conditional_levels, важность 0–3, k=1.
Режим raw_levels может быть отдельной будущей политикой; текущий путь его не выбирает.

Порядок уровней каталога задаёт позиции 1..N.
`u(l)=tanh(k*(l-r))`, `m=sum(q(l)*u(l))`, `S=sum(w*m)/sum(w)`.
Conditional: q=p/sum(numeric p); raw: q=p. Сырые p не переписываются.
Выбран insufficient_data → исключить навык целиком и пересчитать знаменатель.
Вес 0 → исключить. Все исключены → score=null, status=unscored.
Confidence не является дополнительным множителем. S — индекс [-1,1], не
вероятность успешного найма. Пример p=[0,.2,.5,.3,0],r=3,k=1 даёт .0761594.

GET `/v1/recommendations/{recommendation_id}` возвращает тот же сохранённый
результат. ID зависит от полного канонического входа, политики и версии кода.
Результат: `{contract_version:"recommendations-v1",recommendation_id,
vacancy_id,vacancy_profile_id,rubric_id,rubric_version,rubric_sha256,policy,items}`.
Строка: `{candidate_id,eligibility,required_checks,score,status,rank,
parameters,coverage}`; status=scored/unscored/filtered. Rank только для scored.
Сортировка по score DESC,candidate_id ASC; unscored внизу без фиктивного 0.
Filtered хранится для аудита, обычный список его не показывает.
Каждый параметр показывает исключение/причину либо вес, требование, m и уровни
`{level_id,position,p_raw,q,u,contribution}`. Coverage содержит количество и
сумму весов включённых/исключённых параметров. Ответ дополнительно может
содержать поля происхождения/ревизий без изменения описанной семантики.

## Адаптер Верстака

Существующие candidate assessment/readiness сохраняются. Новые команды:
POST `/api/hr/selection/vacancies/{id}/profile` `{revision}`;
GET `/api/hr/selection/vacancies/{id}/profile`;
POST `/api/hr/selection/vacancies/{id}/recommendations`
`{revision,candidateIds}`;
GET `/api/hr/selection/vacancies/{id}/recommendations`.
Дополнительные endpoints ручных требований/обязательных фактов согласуются
между владельцами адаптера и UI до подключения. В ответах видны текущие версии,
ошибки, pending candidates и устаревание. Перед публикацией повторная проверка
версии рекомендации, полей и обычных правил согласия. Расчёт не отправляет писем.

## Приёмка и выпуск

См. `ACCEPTANCE.md`. Рабочая первая нагрузка: 3 существующих интервью и одна
вакансия; изолированный расчёт 100 готовых профилей. Один consumer у каждого
модельного сервиса, очередь <=100; POST принятия <=2с, локальный матч 100
профилей <=2с, готовый read <=500мс в локальном прогоне; live JEV дедлайн 120с.
Это проверяемые условия первой версии, не обещание долговременного SLO.
Отказы/перезапуск/rollback только в изоляции. На YC — штатный путь, используя
уже выданную авторизацию развёртывания; без аварийных экспериментов и рассылок.

Фильтр «HR-интервью проведено»: пользователь потребовал И запись, И готовую
основную расшифровку HR-интервью, включая все части. Закрытие комнаты не
является доказательством. Неоценённые кандидаты показаны ниже без фиктивного
рейтинга. Публикация работодателю сохраняет обязательное согласие кандидата;
рассылка запускается только выбранным действием менеджера.
