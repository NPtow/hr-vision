#!/usr/bin/env python3
"""Opt-in real API/LLM test of the original five-block career interview.

Only synthetic candidates. Creates one test session, not workflows or providers.
Imports the audited private-credential/API helpers from sibling workflow/.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mashadasha_runtime_common", HERE.parent / "workflow" / "test_runtime.py")
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)
RuntimeTestError = common.RuntimeTestError
SCENARIO = "career_brand_two_cv_cases"


class Candidate:
    def __init__(self):
        self.node_turns = Counter()
        self.company_markers = ("Север-71", "Лист-83")

    def context(self):
        return {
            "candidate_name": "Тестовый Никита",
            "cv_text": (
                "СИНТЕТИЧЕСКИЙ КАНДИДАТ, не реальный человек. "
                "2021–2023: аналитик компании «Север-71», проект «Маяк»: отчётность продаж, "
                "время подготовки отчёта сократилось с двух дней до часа. Личный вклад не описан. "
                "2023–2026: продуктовый менеджер компании «Лист-83», проект «Компас»: личный кабинет клиентов, "
                "доля самостоятельных заказов достигла40%. Решения кандидата и работа с командой не описаны."
            ),
            "candidate_context": "СИНТЕТИЧЕСКИЙ ТЕСТ. Кандидат только изучает варианты следующей роли, обещаний оффера или рассылки нет.",
        }

    def answer(self, node, assistant_text):
        if not assistant_text.strip():
            raise RuntimeTestError(f"No assistant speech at {node}; no fabricated answer")
        self.node_turns[node] += 1
        text = common.normalize(assistant_text)
        if node == "career_welcome":
            answer = "Спасибо, всё спокойно, готов поговорить."
        elif node in {"career_why_now", "career_why_deepen"}:
            answer = "Три месяца назад мы завершили основной запуск и перешли к мелким доработкам. Я лично понял на последнем планировании, что новых задач почти не будет. Поэтому пока спокойно изучаю варианты, с текущей работы не уволился."
        elif node == "career_direction":
            answer = "Хочу дальше работать продуктовым менеджером. Мне интересно разбираться в проблеме клиента и доводить решение до работающего продукта."
        elif node == "career_tasks":
            answer = "Хочу сам разговаривать с клиентами, смотреть на данные использования и вместе с командой проверять, решает ли новая функция их задачу."
        elif node == "career_avoid_tasks":
            answer = "Не хочу постоянно только пересылать поручения между отделами без возможности самому разобраться в задаче и принять решение."
        elif node == "career_market":
            answer = "Смотрю вакансии продуктового менеджера, где один человек отвечает за самостоятельное направление и может принимать решения вместе с небольшой командой. По рынку это обычно middle, но на одно название уровня не ориентируюсь."
        elif node == "career_level_flex":
            answer = "Готов рассмотреть роль чуть ниже по названию, если сохранится самостоятельная зона ответственности. Полностью помощническую роль без своих решений не рассматриваю."
        elif node == "career_company":
            # Deliberately no explanation: the model should enter the brand -> why branch.
            answer = "Мне хотелось бы работать в Яндексе."
        elif node in {"career_company_why", "career_company_criterion"}:
            answer = "Меня привлекает возможность разбирать реальные данные использования вместе с сильными аналитиками. На прошлом проекте я это делал один и часто не мог проверить свою гипотезу с коллегой."
        elif node == "career_company_avoid":
            answer = "Мне не подойдёт компания, где руководитель заранее задаёт каждую функцию и нельзя обсуждать, какую проблему клиента она решает."
        elif node == "career_experience_select":
            # No graph commands and no marker injection: select the real foci just named by the interviewer.
            answer = "Давай разберём оба места. Сначала последнее место с личным кабинетом, потом предыдущее с отчётностью продаж."
        elif node in {"career_case1", "career_depth1", "career_case2", "career_depth2"}:
            is_reporting = any(word in text for word in ["маяк", "север", "отчет", "отчёт", "аналитик"])
            is_portal = any(word in text for word in ["компас", "лист", "кабинет", "заказ"])
            # Ground in the actual question; fall back to the explicitly selected order only when it says 'этот проект'.
            if not is_reporting and not is_portal:
                is_reporting = node in {"career_case2", "career_depth2"}
            if is_reporting:
                answer = "Я лично собрал требования у трёх руководителей продаж и написал запросы к базе. Сам решил сначала автоматизировать один отчёт, потому что источники расходились. Сложнее всего было согласовать определение оплаченного заказа: я провёл встречу с финансами и закрепил одно правило. После этого сбор отчёта занимал час вместо двух дней."
            elif any(word in text for word in ["сложн", "команд", "договар", "решени"]):
                answer = "Самое сложное было договориться с руководителем поддержки: он боялся, что клиенты потеряют привычный канал. Я лично показал ему результаты пяти интервью и предложил оставить помощь рядом с формой заказа. Сам выбрал такой постепенный запуск. Через месяц40% заказов клиенты оформляли сами."
            else:
                answer = "В личном кабинете я отвечал за сценарий повторного заказа. Лично провёл пять интервью, составил карту ошибок и вместе с дизайнером проверил прототип. Сам решил убрать лишнее подтверждение, потому что оно мешало завершать заказ. Согласовал решение с поддержкой и разработчиками. Через месяц40% заказов оформлялись самостоятельно."
        elif node == "career_reflection":
            answer = "В этих историях моя сильная сторона — разобраться в конкретной проблеме, договориться о понятном решении и проверить результат. В следующую роль хочу перенести именно такую самостоятельность, а не только название должности."
        else:
            raise RuntimeTestError(f"Unsupported observed node {node!r}; no synthetic answer generated")
        if common.PROHIBITED_CANDIDATE.search(answer):
            raise RuntimeTestError("Candidate answer contains forbidden graph instructions")
        return answer


def verify_definition(client, workflow_id):
    active = client.request("GET", f"/api/v1/workflow/fetch/{workflow_id}")
    versions = client.request("GET", f"/api/v1/workflow/{workflow_id}/versions?status=published&limit=1")
    if not versions:
        raise RuntimeTestError("Career workflow is not published")
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    hashes = {"local": digest(json.loads((HERE / "workflow.json").read_text())),
              "active": digest(active["workflow_definition"]),
              "published": digest(versions[0]["workflow_json"])}
    if len(set(hashes.values())) != 1:
        raise RuntimeTestError("Local, active and published career definitions differ; no session started")
    return {"hashes": hashes, "published_version": versions[0]["version_number"]}


def assessment(candidate, session, observations, mapping, forced_end):
    turns = common.sanitized_turns(session)
    visited = {o["node_id"] for o in observations if o.get("node_id")}
    visited |= {e["node_id"] for t in turns for e in t["events"] if e["type"] == "node_transition" and e.get("node_id")}
    blocks = {mapping[n]["source_block"] for n in visited if n in mapping and mapping[n]["source_block"]}
    answered = {mapping[n]["source_block"] for n in candidate.node_turns if n in mapping and mapping[n]["source_block"]}
    grounded_markers = set()
    user_markers = set()
    for turn in turns:
        for marker in candidate.company_markers:
            if common.mentions_marker(turn["user"] or "", marker):
                user_markers.add(marker)
            if marker not in user_markers and "?" in (turn["assistant"] or "") and common.mentions_marker(turn["assistant"] or "", marker):
                grounded_markers.add(marker)
    all_assistant = "\n".join(t["assistant"] or "" for t in turns)
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", all_assistant)
    ru_ratio = len(re.findall(r"[А-Яа-яЁё]", all_assistant)) / max(1, len(letters))
    errors = [e for t in turns for e in t["events"] if e["type"] == "execution_error"]
    case_speech = "\n".join(o.get("assistant", "") for o in observations if o.get("node_id") in {"career_case1", "career_depth1", "career_case2", "career_depth2"})
    missing_personal = not re.search(r"лично|тво[яйю]|сам[иа]?|отвечал|решени", case_speech, re.I)
    unsupported_promises = bool(re.search(r"буду искать.*ваканс|пришлю.*email|организу[юем]+.*интро|отправлю.*работодател", all_assistant, re.I))
    gathered = session.get("gathered_context") or {}
    checks = {
        "visited_all_five_blocks": blocks == {"1", "2", "3", "4", "5"},
        "answered_in_all_five_blocks": answered == {"1", "2", "3", "4", "5"},
        "named_company_why_branch_answered": candidate.node_turns["career_company_why"] > 0 and "career_company_criterion" not in visited,
        "two_cv_employers_used_before_candidate_mentions": grounded_markers == set(candidate.company_markers),
        "both_cases_and_depths_answered": all(candidate.node_turns[n] > 0 for n in ["career_case1", "career_depth1", "career_case2", "career_depth2"]),
        "personal_contribution_question_asked": not missing_personal,
        "reflection_answered": candidate.node_turns["career_reflection"] > 0,
        "natural_completion": bool(session.get("is_completed")) and not forced_end and "career_end" in visited and gathered.get("call_status") == "end_call" and bool(turns) and all(t["status"] == "completed" for t in turns) and "интервью завершено" in common.normalize(turns[-1]["assistant"] or ""),
        "no_execution_error": not errors,
        "assistant_russian": bool(letters) and ru_ratio >= 0.80,
        "no_unconnected_delivery_promises": not unsupported_promises,
    }
    return {"checks": checks, "passed": all(checks.values()), "visited_nodes": sorted(visited),
            "visited_blocks": sorted(blocks), "answered_blocks": sorted(answered),
            "cv_grounded_markers": sorted(grounded_markers), "russian_letter_ratio": round(ru_ratio, 3)}


def run_case(client, workflow_id, output, max_turns):
    candidate = Candidate()
    mapping = json.loads((HERE / "provenance.json").read_text())["node_mapping"]
    session_name = "SYNTHETIC-CAREER-" + uuid4().hex[:10]
    prefix = f"/api/v1/workflow/{workflow_id}/text-chat/sessions"
    report_path = output / (SCENARIO + ".json")
    observations, session, error, forced_end = [], {}, None, False
    started = time.monotonic()
    common.write_private(report_path, {"synthetic": True, "session_name": session_name, "status": "creating"})
    try:
        session = client.request("POST", prefix, {"name": session_name, "initial_context": candidate.context(),
                                 "annotations": {"verification": {"synthetic": True, "scenario": SCENARIO, "harness": "career-workflow/test_runtime.py"}}})
        print(json.dumps({"event": "career_test_started", "workflow_run_id": session["workflow_run_id"]}), flush=True)
        for number in range(max_turns + 1):
            turns = common.sanitized_turns(session)
            last = turns[-1] if turns else {}
            node = common.current_node(session)
            observations.append({"turn": number, "node_id": node, "assistant": last.get("assistant") or ""})
            common.write_private(report_path, {"synthetic": True, "session_name": session_name, "status": "running",
                                 "workflow_run_id": session.get("workflow_run_id"), "observations": observations, "turns": turns})
            if number % 4 == 0:
                print(json.dumps({"event": "career_test_progress", "turn": number, "node_id": node}), flush=True)
            if number == 0 and (node != "career_welcome" or any(e["type"] == "node_transition" and e.get("node_id") != "career_welcome" for t in turns for e in t["events"])):
                raise RuntimeTestError("Initial node advanced before any candidate response")
            if session.get("is_completed"):
                break
            if number == max_turns:
                raise RuntimeTestError(f"Interview exceeded {max_turns} candidate answers")
            answer = candidate.answer(node, last.get("assistant") or "")
            session = client.request("POST", f"{prefix}/{session['workflow_run_id']}/messages", {"text": answer, "expected_revision": session["revision"]})
    except RuntimeTestError as exc:
        error = str(exc)
        if not session.get("workflow_run_id"):
            error += "; creation outcome may be unknown, check saved session_name before retrying"
    except KeyboardInterrupt:
        error = "Operator interrupted synthetic career test"
    except (KeyError, ValueError, TypeError) as exc:
        error = "Unexpected response/schema error: " + type(exc).__name__
    finally:
        if session.get("workflow_run_id") and not session.get("is_completed"):
            try:
                session = client.request("GET", f"{prefix}/{session['workflow_run_id']}")
                if not session.get("is_completed"):
                    forced_end = True
                    session = client.request("POST", f"{prefix}/{session['workflow_run_id']}/end", {"expected_revision": session["revision"]})
            except RuntimeTestError:
                error = (error + "; " if error else "") + "Synthetic session cleanup not confirmed"
    result = assessment(candidate, session, observations, mapping, forced_end)
    if error:
        result["passed"] = False
    common.write_private(report_path, {"synthetic": True, "scenario": SCENARIO, "tested_at": datetime.now(timezone.utc).isoformat(),
                         "session_name": session_name, "workflow_id": workflow_id, "workflow_run_id": session.get("workflow_run_id"),
                         "elapsed_seconds": round(time.monotonic() - started, 2), "error": error, "forced_end": forced_end,
                         "observations": observations, "turns": common.sanitized_turns(session), "assessment": result})
    return {"passed": result["passed"], "checks": result["checks"], "error": error,
            "workflow_run_id": session.get("workflow_run_id"), "report_path": str(report_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--bootstrap-path", type=Path, default=Path("/var/lib/dograh/bootstrap.json"))
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("/var/lib/dograh/verification/career"))
    parser.add_argument("--max-turns", type=int, default=35)
    args = parser.parse_args()
    if not 1 <= args.max_turns <= 100:
        parser.error("max-turns must be 1..100")
    try:
        client = common.Client(args.base_url, args.bootstrap_path)
        definition = verify_definition(client, args.workflow_id)
        common.write_private(args.output_dir / "definition-check.json", definition)
        result = run_case(client, args.workflow_id, args.output_dir, args.max_turns)
        common.write_private(args.output_dir / "summary.json", {"synthetic": True, "definition": definition, **result})
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if result["passed"] else 1
    except (RuntimeTestError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"passed": False, "error": str(exc) if isinstance(exc, RuntimeTestError) else "Harness setup failed: " + type(exc).__name__}), flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
