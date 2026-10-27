#!/usr/bin/env python3
"""Run real, synthetic text interviews against a configured Dograh API.

This is an opt-in integration test, not a mock and not collected by unittest.
Each API message invokes the deployment's configured LLM and may incur cost.
It never changes workflow/configuration, never sends to real candidates, and
never supplies graph-navigation instructions as candidate utterances.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
PROHIBITED_CANDIDATE = re.compile(r"перейди.*уз[её]л|вызови.*переход|transition|бюджет.*реплик|system.prompt", re.I)


class RuntimeTestError(Exception):
    pass


class Client:
    def __init__(self, base_url, bootstrap_path, timeout=90):
        self.base_url = base_url.rstrip("/")
        self.token = None
        self.timeout = timeout
        mode = stat.S_IMODE(bootstrap_path.stat().st_mode)
        if mode & 0o077:
            raise RuntimeTestError("Bootstrap file must be private (mode 600 or stricter).")
        bootstrap = json.loads(bootstrap_path.read_text())
        credentials = {"email": bootstrap["email"], "password": bootstrap["password"]}
        login = self.request("POST", "/api/v1/auth/login", credentials)
        self.token = login["token"]
        # No credentials, provider keys, auth payloads or tokens are retained in reports.
        del credentials, login, bootstrap

    def request(self, method, path, body=None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path,
                          data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
                          headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            # Do not emit arbitrary response bodies that could expose configuration.
            raise RuntimeTestError(f"HTTP {exc.code}: {method} {path}") from None
        except (URLError, TimeoutError, OSError) as exc:
            # POST requests are never blindly retried: the outcome may be unknown.
            raise RuntimeTestError(f"Transport failure ({type(exc).__name__}): {method} {path}; not retried") from None


def normalize(text):
    return re.sub(r"[^а-яa-z0-9]+", " ", text.lower().replace("ё", "е")).strip()


def mentions_marker(text, marker):
    name, number = normalize(marker).split()
    stem = name[:-1] if name.endswith("а") else name
    return bool(re.search(r"\b" + re.escape(stem) + r"[а-я]*\s*" + re.escape(number) + r"\b", normalize(text)))


BLITZ_ANSWERS = {
    "arithmetic-01": "Шесть сделок.", "arithmetic-02": "Десять сделок.",
    "arithmetic-03": "Пятьдесят звонков.", "arithmetic-04": "Сорок процентов.",
    "arithmetic-05": "Три цикла.", "arithmetic-06": "Тридцать тысяч рублей.",
    "arithmetic-07": "Двести тысяч рублей.", "arithmetic-08": "Тридцать процентов.",
    "arithmetic-09": "Пятнадцать тысяч рублей.", "arithmetic-10": "Двести тысяч рублей.",
    "qualification-01": "Не в активной продаже сейчас: поставлю контакт на нужный срок.",
    "qualification-02": "Возьму на квалификацию и выясню, когда появится бюджет.",
    "qualification-03": "Сам по себе адрес почты не определяет качество лида.",
    "qualification-04": "Интерес есть, но сначала выясню задачу и полномочия.",
    "qualification-05": "Уточню, кто принимает решение, и попрошу познакомить с ним.",
    "qualification-06": "Отложу активную продажу и согласую срок следующего контакта.",
    "qualification-07": "Нет, без задачи готовность к покупке не подтверждена.",
    "qualification-08": "Скорее холодный: активного интереса пока не вижу.",
    "qualification-09": "Сначала запрошу контакты и уточню задачу.",
    "objection-01": "С какой альтернативой вы сравниваете стоимость?",
    "objection-02": "Что для руководителя будет главным при выборе?",
    "objection-03": "Какие условия входят в предложение конкурента?",
    "objection-04": "Какой вопрос должно закрыть коммерческое предложение?",
    "objection-05": "Когда у вас появится возможность вернуться к задаче?",
    "objection-06": "Что вам хотелось бы улучшить в работе с нынешним поставщиком?",
    "objection-07": "В какое время вам удобно коротко поговорить?",
    "objection-08": "Когда будете планировать бюджет на эту задачу?",
    "objection-09": "Какая задача у вас сейчас требует решения?",
    "prioritization-01": "Сначала закреплю время звонка горячему лиду, затем подготовлю отчёт до конца дня.",
    "prioritization-02": "Сначала тёплому лиду.",
    "prioritization-03": "Доработка тёплых лидов.",
    "prioritization-04": "Сначала клиенту с ближайшим сроком решения и подтверждённой задачей.",
    "prioritization-05": "Сначала текущая сделка с обязательством на этой неделе.",
    "prioritization-06": "Сначала быстро отвечу новому лиду, затем вернусь к текущей сделке.",
    "prioritization-07": "В конце месяца сначала дополнительный час на тёплые звонки.",
    "prioritization-08": "Сначала уточню запрос текущего клиента.",
    "facts-01": "Тридцать звонков в день.", "facts-02": "Миллион рублей в месяц.",
    "facts-03": "В среднем тридцать дней.", "facts-04": "Сто тысяч рублей.",
    "facts-05": "Десять сделок в месяц.", "facts-06": "Битрикс24.",
    "facts-07": "Пять человек.", "facts-08": "Сто десять процентов.",
    "facts-09": "Около месяца.", "facts-10": "Входящие заявки с сайта.",
    "ethics-01": "Скажу, что пока не уверен, и проверю задачу с техническим специалистом.",
    "ethics-02": "Уточню реальный срок у исполнителей и предложу руководителю его.",
    "ethics-03": "Скажу клиенту, что продукт не подходит, и откажусь от этой сделки.",
    "ethics-04": "Не буду записывать несуществующий звонок.",
    "ethics-05": "Предложу только официальный договор и платёж.",
    "ethics-06": "Да, расскажу о баге до подписания и объясню влияние на задачу.",
    "ethics-07": "Скажу до подписания, что этой функции нет.",
    "ethics-08": "Предложу подходящий клиенту тариф.",
}


class Candidate:
    def __init__(self, scenario):
        self.scenario = scenario
        self.b2b = scenario == "b2b_callback_date"
        self.node_turns = Counter()
        self.blitz_repeats = 0
        self.blitz_answered_ids = []
        bank = json.loads((HERE / "source" / "blitz-bank.json").read_text())
        self.bank = [q for c in bank["categories"] for q in c["questions"]]
        self.marker = "Ладога-47" if self.b2b else "Вектор-62"

    def context(self):
        return {
            "candidate_name": "Тестовый Алексей" if self.b2b else "Тестовая Ирина",
            "cv_text": (
                "СИНТЕТИЧЕСКИЙ КАНДИДАТ ДЛЯ ТЕСТА, не реальный человек. "
                "2023–2026 менеджер продаж СфераCRM. Продавал подписку CRM бизнесу и частным предпринимателям. "
                "Личный план 1 млн рублей в месяц, средний чек 100 тысяч, цикл сделки30 дней, команда5 человек. "
                f"В резюме отдельно указан проект «{self.marker}»: повторные продажи, но личные действия, "
                "участники и результат в описании не раскрыты. Это отдельный проект, не кейс сокращения цикла."
            ),
            "pre_screen_context": (
                "СИНТЕТИЧЕСКИЙ ТЕСТ. В чате согласованы удалённый формат, оформление по ТК, "
                "компьютер и камера есть. Ожидания по оплате обсуждены и подходят вакансии. "
                "Не задавать эти вопросы повторно. Кандидат ознакомился с правилами интервью."
            ),
            "vacancy_context": "СИНТЕТИЧЕСКИЙ ТЕСТ. Менеджер по продажам CRM. Проверяются реальные действия, личный вклад, работа с возражениями.",
        }

    def answer(self, node, assistant_text):
        self.node_turns[node] += 1
        count = self.node_turns[node]
        text = normalize(assistant_text)
        if not assistant_text.strip():
            raise RuntimeTestError(f"No assistant speech at node {node}; no synthetic answer fabricated")

        if node in {"b1_monologue", "b1_continue"}:
            answer = "Да, слышно нормально. Я три года работал в продажах программного обеспечения. Сам находил клиентов, проводил встречи и доводил сделки до оплаты. Сильная сторона — спокойно разбирать задачу клиента и выполнять договорённости. Слабая — раньше слишком долго держал бесперспективные сделки, потом начал честнее их квалифицировать. В последний год стабильно выполнял личный план. На этом всё, это мой ключевой опыт."
        elif node in {"b2_case", "b2_clarify"}:
            answer = "Сначала выясню, какую задачу человек решает техникой и что мешает выбрать сейчас. Покажу две подходящие модели, проверю наличие и сразу назову реальную дату доставки. Уберу ожидание ответа склада, чтобы человек мог принять решение за один визит."
        elif node in {"b2_example", "b2_case_fact", "b2_personal", "b2_number", "b2_return"}:
            answer = "В апреле я продавал CRM компании «Орион», это конкретная сделка. Лично созвонился с руководителем продаж, собрал требования и сам договорился с инженером о демонстрации на следующий день. Сам решил провести её сразу, потому что клиент обычно ждал неделю. Сложность была в занятости инженера: я согласовал с ним короткий слот и подготовил данные сам. В итоге клиент оплатил через14 дней вместо обычных30, сумма100 тысяч рублей."
        elif node in {"b3_deepen", "b3_detail", "b3_cv"}:
            # Answer the actual focus; every branch gives real synthetic facts, not navigation commands.
            if any(word in text for word in ["почему", "важно", "выбор", "решени"]):
                answer = "Я сам выбрал сначала провести проверку задачи с клиентом, а не рассылать всем одно предложение. Причиной были отказы без объяснений. Согласовал подход с руководителем продаж и применил к20 клиентам; шесть продлили подписку."
            elif any(word in text for word in ["цифр", "результат", "план", "факт", "чек"]):
                answer = "В том проекте я лично вернул в работу20 клиентов; шесть оплатили продление на общую сумму600 тысяч рублей за месяц. До этого за сопоставимый месяц продлевались три клиента."
            elif any(word in text for word in ["сложн", "трудн", "мешал"]):
                answer = "Сложность была в недоверии после задержки поддержки. Я лично собрал обращения, договорился с руководителем поддержки о конкретном дне исправления и сообщил клиентам подтверждённый срок. Шесть из20 затем продлили подписку."
            else:
                project = f"В проекте «{self.marker}»" if mentions_marker(text, self.marker) else "В том проекте"
                answer = project + " моя личная роль была вернуть клиентов на продление. Я сам выбрал20 клиентов с понятной задачей, позвонил каждому и согласовал с руководителем поддержки исправление задержек. Было сложно восстановить доверие, поэтому я показывал подтверждённый срок решения. За месяц шесть клиентов продлили подписку на600 тысяч рублей. Это были мои звонки и мои договорённости."
        elif node == "b3_summary":
            answer = "Да, правильно: решение, звонки и договорённости были моими, результат — шесть продлений на600 тысяч рублей."
        elif node == "b4_setup":
            answer = "Выбираю продажу бизнесу." if self.b2b else "Выбираю продажу частному человеку."
        elif node in {"b4_b2b", "b4_b2c"}:
            if count == 1:
                answer = "Здравствуйте, это Алексей из СфераCRM, вы оставляли заявку. Какую задачу хотите решить?" if self.b2b else "Здравствуйте, это Ирина из СфераCRM, вы оставляли заявку. Для какой задачи ищете программу?"
            else:
                answer = "Для этой задачи предлагаю подписку с настройкой воронки и обучением. Стоимость100 тысяч рублей за год, запуск за неделю. Готовы обсудить оформление?"
        elif node == "b4_think":
            answer = "Что именно вам хотелось бы обдумать?" if count == 1 else "Понимаю. Тогда предлагаю сверить условия с вашей задачей: в100 тысяч входит настройка и обучение. Можем двигаться к согласованию?"
        elif node == "b4_other":
            answer = "Кто ещё участвует в решении и что для этого человека важно?" if count == 1 else "Давайте учтём его критерий в предложении. Цена100 тысяч за год с настройкой; какие ещё условия нужно согласовать для решения?"
        elif node == "b4_price":
            answer = "Какую конкретно цену предложил другой поставщик и что туда входит?" if count == 1 else "Спасибо. У нас в цене есть настройка и обучение, поэтому предлагаю сравнить полный состав. Если он вам подходит, готовы выбрать наш вариант?"
        elif node == "b4_refuse":
            alternatives = ["Я услышал ваш отказ. Что сейчас сильнее всего мешает принять решение?", "Понимаю, давить не хочу. Есть ли условие, которое мы ещё не обсудили?", "Спасибо за прямой ответ. Тогда покупку сейчас оформлять не будем."]
            answer = alternatives[min(count - 1, len(alternatives) - 1)]
        elif node in {"b4_next", "b4_window"}:
            answer = "Можно я лично перезвоню вам в четверг15 октября в15 часов, чтобы узнать ваше решение?" if self.b2b else "Я тогда перезвоню вам позже, как-нибудь на днях."
        elif node == "b4_date":
            answer = "В четверг15 октября в15 часов. Разрешите мне вам перезвонить в это время?" if self.b2b else "Пока не могу назвать конкретный день, как-нибудь потом."
        elif node == "b4_confirm":
            answer = "Да, подтверждаю: в четверг15 октября в15 часов я сам вам перезвоню. Спасибо."
        elif node == "b4_exit":
            answer = "Хорошо, я готов."
        elif node.startswith("b5_blitz"):
            answer = self.blitz_answer(assistant_text)
        elif node == "end":
            answer = "Спасибо, до свидания."
        else:
            raise RuntimeTestError(f"Unsupported observed node {node!r}; no answer generated")
        if PROHIBITED_CANDIDATE.search(answer):
            raise RuntimeTestError("Synthetic answer contains forbidden navigation instruction")
        return answer

    def blitz_answer(self, assistant_text):
        # Match the most recent spoken question to the frozen bank, including replacements.
        chunks = assistant_text.split("?")
        question = chunks[-2] if len(chunks) > 1 else assistant_text
        question = normalize(question)
        def score(item):
            candidate = normalize(item["text"])
            words_a, words_b = set(question.split()), set(candidate.split())
            overlap = len(words_a & words_b) / max(1, len(words_b))
            return 0.55 * overlap + 0.45 * SequenceMatcher(None, question, candidate).ratio()
        match = max(self.bank, key=score)
        if score(match) < 0.38:
            self.blitz_repeats += 1
            if self.blitz_repeats > 2:
                raise RuntimeTestError("Cannot ground blitz answer in the assistant's actual question")
            return "Повтори, пожалуйста, вопрос."
        self.blitz_answered_ids.append(match["id"])
        return BLITZ_ANSWERS[match["id"]]


def current_node(session):
    return (session.get("checkpoint") or {}).get("current_node_id")


def sanitized_turns(session):
    turns = []
    for index, turn in enumerate((session.get("session_data") or {}).get("turns") or []):
        events = []
        for event in turn.get("events") or []:
            payload = event.get("payload") or {}
            if event.get("type") == "node_transition":
                events.append({"type": "node_transition", "node_id": payload.get("node_id"),
                               "previous_node_id": payload.get("previous_node_id")})
            elif event.get("type") in {"tool_call_started", "tool_call_result"}:
                events.append({"type": event["type"], "function_name": payload.get("function_name")})
            elif event.get("type") == "execution_error":
                events.append({"type": "execution_error"})
        turns.append({"index": index, "status": turn.get("status"),
                      "user": (turn.get("user_message") or {}).get("text"),
                      "assistant": (turn.get("assistant_message") or {}).get("text"),
                      "events": events})
    return turns


def assertions(candidate, session, observations, mapping, forced_end=False):
    turns = sanitized_turns(session)
    nodes = []
    assistant_by_node = []
    for observation in observations:
        if observation.get("node_id"):
            nodes.append(observation["node_id"])
        assistant_by_node.append((observation.get("node_id"), observation.get("assistant", "")))
    for turn in turns:
        nodes += [e["node_id"] for e in turn["events"] if e["type"] == "node_transition" and e.get("node_id")]
    visited = set(nodes)
    blocks = {mapping[n]["source_block"] for n in visited if n in mapping and mapping[n]["source_block"]}
    answered_blocks = {mapping[n]["source_block"] for n in candidate.node_turns
                       if n in mapping and mapping[n]["source_block"]}
    all_assistant = "\n".join(t["assistant"] or "" for t in turns)
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", all_assistant)
    russian = len(re.findall(r"[А-Яа-яЁё]", all_assistant)) / max(1, len(letters))
    precondition = re.compile(r"компьютер|камер[ауы]|оформлен|формат[а-я ]+работ|удал[её]н|ожидани[а-я ]+оплат|зарплат", re.I)
    repeats = [{"node": node, "text": text} for node, text in assistant_by_node
               if node and not node.startswith("b4_") and "?" in text and precondition.search(text)]
    expected_lane = "b4_b2b" if candidate.b2b else "b4_b2c"
    wrong_lane = "b4_b2c" if candidate.b2b else "b4_b2b"
    user_has_marker = False
    cv_grounded_question = False
    for turn in turns:
        if mentions_marker(turn["user"] or "", candidate.marker):
            user_has_marker = True
        if not user_has_marker and "?" in (turn["assistant"] or "") and mentions_marker(turn["assistant"] or "", candidate.marker):
            cv_grounded_question = True
    gathered = session.get("gathered_context") or {}
    required_blitz_nodes = {"b5_blitz"} | {f"b5_blitz_{i:02d}" for i in range(2, 14)}
    required_profile_fields = {"presentation", "kdp_evidence", "personal_contribution", "roleplay_type",
                               "roleplay_evidence", "callback_agreed", "callback_detail", "blitz_evidence"}
    completed_turns = bool(turns) and all(turn["status"] == "completed" for turn in turns)
    execution_errors = [e for turn in turns for e in turn["events"] if e["type"] == "execution_error"]
    source_closing = json.loads((HERE / "source" / "interview-guide.json").read_text())["closing_message"]
    expected_closing = source_closing.replace(" и передам его работодателю по этой вакансии", "")
    closing_spoken = normalize(turns[-1]["assistant"] or "") == normalize(expected_closing) if turns else False
    profile_saved = all(
        isinstance(gathered.get(field), bool) if field == "callback_agreed"
        else isinstance(gathered.get(field), str) and bool(gathered[field].strip())
        for field in required_profile_fields
    )
    tests = {
        "visited_all_six_source_blocks": blocks == {"1", "2", "3", "4a", "4b", "5"},
        "candidate_answered_in_all_six_blocks": answered_blocks == {"1", "2", "3", "4a", "4b", "5"},
        "correct_role_lane_only": expected_lane in visited and wrong_lane not in visited,
        "candidate_replied_to_all_three_objections": all(candidate.node_turns[n] > 0 for n in ("b4_think", "b4_other", "b4_price")),
        "at_least_ten_distinct_blitz_questions_answered": len(set(candidate.blitz_answered_ids)) >= 10,
        "all_thirteen_blitz_nodes_answered": all(candidate.node_turns[n] > 0 for n in required_blitz_nodes),
        "thirteen_blitz_answers_grounded": len(candidate.blitz_answered_ids) == 13,
        "all_eight_profile_fields_saved": profile_saved,
        "profile_role_lane_matches_candidate": normalize(str(gathered.get("roleplay_type", ""))) == ("b2b" if candidate.b2b else "b2c"),
        "natural_completion_at_end": bool(session.get("is_completed")) and "end" in visited and not forced_end and gathered.get("call_status") == "end_call" and completed_turns and closing_spoken and not execution_errors,
        "cv_fact_used_before_candidate_mentioned_it": cv_grounded_question,
        "preconditions_not_repeated": not repeats,
        "assistant_russian": bool(letters) and russian >= 0.80,
        "no_execution_error": not execution_errors,
    }
    if candidate.b2b:
        tests["specific_callback_branch"] = "b4_confirm" in visited and "b4_date" not in visited
        tests["callback_extracted_true"] = gathered.get("callback_agreed") is True
    else:
        tests["vague_then_no_agreement_branch"] = "b4_date" in visited and "b4_confirm" not in visited
        tests["callback_extracted_false"] = gathered.get("callback_agreed") is False
    return {"checks": tests, "passed": all(tests.values()), "visited_nodes": sorted(visited),
            "visited_blocks": sorted(blocks), "answered_blocks": sorted(answered_blocks), "russian_letter_ratio": round(russian, 3),
            "blitz_answered_ids": candidate.blitz_answered_ids,
            "precondition_repeat_evidence": repeats}


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(path, 0o600)


def verify_deployed_definition(client, workflow_id):
    """Text sessions use a draft when present; require equality with the release."""
    active = client.request("GET", f"/api/v1/workflow/fetch/{workflow_id}")
    versions = client.request("GET", f"/api/v1/workflow/{workflow_id}/versions?status=published&limit=1")
    if not versions:
        raise RuntimeTestError("Workflow has no published definition")
    expected = json.loads((HERE / "workflow.json").read_text())
    def fingerprint(value):
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
    digests = {"source": fingerprint(expected), "active": fingerprint(active["workflow_definition"]),
               "published": fingerprint(versions[0]["workflow_json"])}
    if len(set(digests.values())) != 1:
        raise RuntimeTestError("Local source, active text-chat definition and published definition differ; no interview started")
    return {"definition_hashes": digests, "published_definition_id": versions[0]["id"],
            "published_version": versions[0]["version_number"], "active_version_status": active["version_status"]}


def run_case(client, workflow_id, scenario, output, max_turns, mapping):
    candidate = Candidate(scenario)
    start = time.monotonic()
    prefix = f"/api/v1/workflow/{workflow_id}/text-chat/sessions"
    observations, session, error = [], {}, None
    forced_end = False
    report_path = output / f"{scenario}.json"
    session_name = "SYNTHETIC-MASHADASHA-" + scenario + "-" + uuid4().hex[:8]
    write_private(report_path, {"scenario": scenario, "synthetic": True,
                               "session_name": session_name, "status": "creating"})
    try:
        session = client.request("POST", prefix, {
            "name": session_name,
            "initial_context": candidate.context(),
            "annotations": {"verification": {"synthetic": True, "scenario": scenario, "harness": "test_runtime.py"}},
        })
        print(json.dumps({"event": "synthetic_session_started", "scenario": scenario,
                          "workflow_run_id": session["workflow_run_id"]}), flush=True)
        for turn_number in range(max_turns + 1):
            turns = sanitized_turns(session)
            last = turns[-1] if turns else {}
            node = current_node(session)
            observations.append({"turn": turn_number, "node_id": node,
                                 "revision": session.get("revision"), "assistant": last.get("assistant") or ""})
            if turn_number % 5 == 0:
                print(json.dumps({"event": "synthetic_progress", "scenario": scenario,
                                  "turn": turn_number, "node_id": node}), flush=True)
            # Persist after every turn so interrupted test runs remain inspectable.
            write_private(report_path, {"scenario": scenario, "synthetic": True,
                "session_name": session_name, "workflow_run_id": session.get("workflow_run_id"), "status": "running",
                "observations": observations, "turns": turns})
            if turn_number == 0 and (node != "b1_monologue" or any(
                    e["type"] == "node_transition" and e.get("node_id") != "b1_monologue"
                    for t in turns for e in t["events"])):
                raise RuntimeTestError("Initial assistant turn skipped to another node before any candidate answer")
            if session.get("is_completed"):
                break
            if turn_number == max_turns:
                raise RuntimeTestError(f"Interview exceeded {max_turns} candidate turns")
            answer = candidate.answer(node, last.get("assistant") or "")
            session = client.request("POST", f"{prefix}/{session['workflow_run_id']}/messages", {
                "text": answer, "expected_revision": session["revision"],
            })
    except RuntimeTestError as exc:
        error = str(exc)
        if not session.get("workflow_run_id"):
            error += "; creation outcome may be unknown; look up the saved session_name before retrying"
    except KeyboardInterrupt:
        error = "Synthetic test interrupted by operator"
    except (KeyError, ValueError, TypeError) as exc:
        error = f"Unexpected response/schema error: {type(exc).__name__}"
    finally:
        if session.get("workflow_run_id") and not session.get("is_completed"):
            # Only the harness's own synthetic run is ended. It cannot count as success.
            try:
                session = client.request("GET", f"{prefix}/{session['workflow_run_id']}")
                if not session.get("is_completed"):
                    forced_end = True
                    session = client.request("POST", f"{prefix}/{session['workflow_run_id']}/end", {
                        "expected_revision": session["revision"],
                    })
            except RuntimeTestError:
                error = (error + "; " if error else "") + "Synthetic session cleanup could not be confirmed"
    assessment = assertions(candidate, session, observations, mapping, forced_end)
    if error:
        assessment["passed"] = False
    report = {
        "scenario": scenario, "synthetic": True, "tested_at": datetime.now(timezone.utc).isoformat(),
        "session_name": session_name, "workflow_id": workflow_id, "workflow_run_id": session.get("workflow_run_id"),
        "elapsed_seconds": round(time.monotonic() - start, 2), "error": error,
        "forced_end": forced_end, "observations": observations,
        "turns": sanitized_turns(session), "gathered_context": session.get("gathered_context") or {},
        "assessment": assessment,
    }
    write_private(report_path, report)
    return {"scenario": scenario, "workflow_run_id": session.get("workflow_run_id"),
            "passed": assessment["passed"], "checks": assessment["checks"],
            "error": error, "report_path": str(report_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--bootstrap-path", type=Path, default=Path("/var/lib/dograh/bootstrap.json"))
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("/var/lib/dograh/verification"))
    parser.add_argument("--scenario", choices=["all", "b2b_callback_date", "b2c_vague_no_agreement"], default="all")
    parser.add_argument("--max-turns", type=int, default=65)
    args = parser.parse_args()
    if not 1 <= args.max_turns <= 100:
        parser.error("--max-turns must be between1 and100")
    try:
        client = Client(args.base_url, args.bootstrap_path)
        verified_definition = verify_deployed_definition(client, args.workflow_id)
        write_private(args.output_dir / "definition-check.json", verified_definition)
        mapping = json.loads((HERE / "provenance.json").read_text())["node_mapping"]
        scenarios = ["b2b_callback_date", "b2c_vague_no_agreement"] if args.scenario == "all" else [args.scenario]
        results = []
        for scenario in scenarios:
            result = run_case(client, args.workflow_id, scenario, args.output_dir, args.max_turns, mapping)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if result.get("error"):
                break
        write_private(args.output_dir / "summary.json", {"synthetic": True, "definition": verified_definition, "results": results,
                       "passed": all(result["passed"] for result in results)})
        return 0 if all(result["passed"] for result in results) else 1
    except (RuntimeTestError, OSError, KeyError, ValueError) as exc:
        # Never serialize request objects, bootstrap content or exception locals.
        print(json.dumps({"passed": False, "error": str(exc) if isinstance(exc, RuntimeTestError)
                         else "Bootstrap/harness setup failed: " + type(exc).__name__}), flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
