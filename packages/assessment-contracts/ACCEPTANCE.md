# Приёмка ядра подбора: кандидат, вакансия, рекомендации

Версия: codex/assessment-services; регрессия после слияния — commit
`0606a805c7b9e4f3b060887ff0e66227970b0007` (26.10.2026). Ответственный: «Ядро»; UI: «Верстак».
Целевые среды: isolated integration и YC production (штатный smoke).
Нагрузка/пороги: см. README; время свидетельств — Europe/Moscow.

| ID | Обязателен / сценарий | Среда | Ожидаемый конечный результат | Статус | Свидетельство |
|---|---|---|---|---|---|
| C1 | Полный корпус кандидата | integration + YC | CV и все актуальные части интервью сохранены без потерь, одинаковый state у всех параметров; вакансия отсутствует | UNVERIFIED | Изолированная часть PASS: H — полный CV + 3 части, одинаковый state/16 параметров, без вакансии. YC остаётся непроверенным. |
| C2 | Материалы стали готовы/изменились | integration | durable команда автоматической оценки; одинаковая ревизия не оплачивается повторно; GET ничего не запускает | PASS | L: 13/13 — ready-transition, durable queued, рестарт, одинаковая ревизия/unknown не повторяются; GET без dispatch. H: чтения не создают jobs/outbox. |
| V1 | Профиль вакансии | integration + live JEV | только поля вакансии, 2 Choice на каждый навык, сырые ответы и конкретные требования сохранены | UNVERIFIED | Изолированная часть PASS: H/V — полный снимок, 32 Choice, сохранение сырых ответов. Live JEV остаётся непроверенным. |
| V2 | Неизвестное/ручная коррекция | integration | needs_review виден; коррекция — новая ревизия с автором, сырой ответ неизменен | PASS | V: unknown не равен 0, needs_review; H: ручная коррекция через HTTP создаёт новый profile_id/author, raw_answers сохраняются. |
| R1 | Точная формула | isolated service | tanh до усреднения, исключения меняют знаменатель, confidence не множитель, все исключены дают null | PASS | H/R: tanh по каждому уровню, q=p/sum(p) только в производном результате, confidence не множитель; all-unknown → null/unscored. |
| R2 | Обязательные факты | isolated product | fail не виден в выдаче вакансии, unknown виден на ручном апруве; подтверждения из модели не возникают | PASS | H/R: pass/unknown/fail раздельны; fail не разрешает добавление/публикацию, unknown требует нового явного апрува, факты не выводятся из текста. |
| R3 | Совместимость и полнота пула | integration | несовместимые рубрики/версии отклонены, нет молчаливого обрезания пула/частичного успеха | PASS | H/R: весь явный пул, pending сохранены; несовместимая рубрика отклонена без записи; 1000 принимается, превышение отклоняется целиком. |
| I1 | Реальный потребитель | integration + YC | HR API читает сервисы, UI показывает сохранённую текущую версию, legacy не управляет новым путём | UNVERIFIED | Изолированная часть PASS: H — реальные HR bridges/ServiceHTTP/SQLite и три HTTP-процесса, current canInvite, без legacy FK. UI/YC остаются непроверенными. |
| I2 | Изменение исходных данных | integration | stale виден отдельно у кандидата/вакансии/сравнения, старый результат не разрешает действие | PASS | H/L: stale при изменении источника/фактов/профиля; delayed-consent повторно читает источник; гонка фактов перед финальным lock даёт 409. |
| I3 | Приглашение/согласие | integration | только явная команда менеджера, штатный outbox/согласие/дедупликация; пересчёт ничего не отправляет | PASS | H: только явная команда, точный unknown-апрув; согласие сохраняет durable pending_verification, worker публикует один раз; пересчёт/GET не создают outbox. Внешние доставки не запускались. |
| F1 | Доступ/tenant/неверный вход | isolated product | чужие права отклонены; нет исходных текстов/ключей в ошибках/логах | PASS | H/C/V/R: 401 без/с неверным bearer, HR resource denial 403, неверные envelope/JSON/рубрики отклонены; тексты и токены отсутствуют в проверенных ошибках/логах. |
| F2 | Provider timeout/ошибка/некорректный ответ | isolated processes | ограниченный срок, записанная ошибка/unknown, нет слепого повторного POST | PASS | F: реальные OS-процессы кандидата/вакансии через закрытый локальный SOCKS → network_error за 1,05с; non-listening SOCKS → timeout за 8,83–8,93с. Повтор HTTP POST/рестарт не меняют terminal row. Malformed typed response_saved → failed/invalid_saved_response, raw/hash сохранены, очередь/readiness доступны. |
| P1 | Перезапуск | isolated processes | queued продолжается, response_saved воспроизводится, sent становится явным unknown, результаты читаются | PASS | L/H/V/R: queued переживает рестарт, response_saved воспроизводится, sent → outcome_unknown; SIGKILL/restart сохраняет идентичные результаты и не повторяет модель. |
| P2 | Ресурсы и наблюдение | isolated + YC smoke | зафиксированы операции, длительности, очередь, память и health/readiness, без утечки payload | UNVERIFIED | Изолированная часть PASS: R — 100 профилей POST 0,245с / GET 0,081с; H/C/V/R — protected readiness/operator, очередь и приватность. YC, память и окно штатного наблюдения остаются непроверенными. |
| D1 | Обновление/откат | isolated processes + YC smoke | совместимые данные и потребители, возврат проверен, release воспроизводим | UNVERIFIED | Изолированная часть PASS: D — реальные процессы install/update/failed-health return/rollback и воспроизводимые артефакты. YC systemd/release/rollback остаются непроверенными. |
| D2 | Backup/restore | isolated databases | восстановлены профиль/исходный ответ/результат, идентичность подтверждена | PASS | V/R: backup/restore сохраняет исходный ответ, ручную ревизию и идентичный результат; R — фактические CLI backup/restore + переключение HTTP-процесса. D — live WAL, hash/integrity и isolated restore. |
| D3 | Диагностика/эксплуатация | integration | по ID различимы принятие/работа/ошибка/unknown; команды запуска, восстановления и отката выполнены | PASS | C/V/R/D: проверены startup, protected HTTP operator/readiness, API-чтение job states по ID, replay, backup/restore и изолированный rollback; команды — service README и delivery README. Delivery CLI inspect --id отдельно не запускался; production-процедуры остаются в D1/P2. |

Изолированные свидетельства (macOS; H/L/V/C/R — Python 3.14.7, D — 3.9.6; synthetic typed provider
fixtures и явно обозначенный synthetic source collector в H; live JEV здесь
не подтверждается):

- H — [HTTP/HR/SQLite, 10 PASS после слияния](../../output/assessment-services-20261026/acceptance/after-merge-http-flow.log), [тесты](../../services/tests/assessment-flow/test_http_flow.py).
- L — [автоматический lifecycle, 13 PASS после слияния](../../output/assessment-services-20261026/acceptance/after-merge-lifecycle.log).
- V — [vacancy, 17 PASS после слияния](../../output/assessment-services-20261026/acceptance/after-merge-vacancy.log).
- C — [candidate core, 110 PASS](../../output/assessment-services-20261026/acceptance/candidate-core.log); R — [recommendations, 34 PASS](../../output/assessment-services-20261026/acceptance/recommendations.log). Их runtime-исходники после предыдущего прогона не менялись.
- D — [13 PASS: команда, полный лог, время и SHA256](../../services/deploy/assessment-services/test-evidence.json), [delivery-сценарии](../../services/deploy/assessment-services/tests/test_delivery.py), [команды и ограничения](../../services/deploy/assessment-services/README.md). Из них 8 проверяют изолированные артефакты/процессы/WAL; 5 проверяют orchestration receiver без реального systemd.
- F — [3 process-failure + 10 regression PASS, команды/время/SHA256](../../output/assessment-services-20261026/acceptance/provider-fault-evidence.json), [полный лог отказов](../../output/assessment-services-20261026/acceptance/provider-fault-process.log). SOCKS принудительно локальный, ключи фиктивные; интернет/provider не вызывались. Реально пройден connect timeout 8с; полный deadline обработки JEV не ожидался, его предел проверен конфигурацией/transport unit tests.
- [Исходные SHA256 и предыдущий полный прогон 171 PASS](../../output/assessment-services-20261026/acceptance/independent-evidence.json); [регрессия после слияния](../../output/assessment-services-20261026/acceptance/after-merge-evidence.json).

PASS относится только к среде строки; simulated provider failures не подтверждают
live-provider или production. Состояния C1/V1/I1/P2/D1 сохраняют UNVERIFIED до
проверки всех требуемых зависимостей/сред.

Вердикт: NOT READY до закрытия обязательных строк. Изолированная проверка и
production smoke отмечаются отдельно. Полный реальный прогон не подразумевает
отправку сообщений кандидатам/работодателю или инъекцию отказов на production.
