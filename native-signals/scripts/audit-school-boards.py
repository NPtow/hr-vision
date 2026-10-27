#!/usr/bin/env python3
"""Read-only audit of school board proposals against exported research packets.

The script never changes packets, drafts, reviews, or live boards. Only audit-*
reports are written. Semantic warnings are a review queue, not proof of a defect.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / "output/school-boards-2026-10-12"
KINDS = {"source", "observation", "hypothesis", "offer", "contact", "note"}
EDGE_KINDS = {"supports", "contradicts", "requiresVerification", "contactRationale"}
AGENT_STATES = {"proposed", "requiresVerification"}
WIDTH, HEIGHT = 248, 220
PREFIX = "school-review-20261012-"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def blank_seed(node: dict) -> bool:
    expected = {
        "initial-offer": ("offer", "Оффер", {"x": 80, "y": 100}),
        "initial-recipient": ("note", "Получатель", {"x": 470, "y": 100}),
    }.get(node.get("id"))
    return bool(expected and node.get("kind") == expected[0]
                and node.get("title") == expected[1] and node.get("body", "") == ""
                and node.get("status") == "proposed" and not node.get("referenceId")
                and node.get("position") == expected[2])


def board_document(value) -> dict:
    if not isinstance(value, dict):
        return {"nodes": [], "edges": []}
    return value.get("document", value)


def index(items):
    return {item.get("id"): item for item in items if isinstance(item, dict)}


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def finite_position(node):
    p = node.get("position", {})
    return all(isinstance(p.get(k), (int, float)) and not isinstance(p.get(k), bool)
               and math.isfinite(p[k]) and abs(p[k]) <= 1_000_000 for k in ("x", "y"))


def overlap(a, b):
    p, q = a["position"], b["position"]
    return p["x"] < q["x"] + WIDTH and p["x"] + WIDTH > q["x"] and p["y"] < q["y"] + HEIGHT and p["y"] + HEIGHT > q["y"]


def audit_company(packet: dict, draft: dict, review: dict, live: dict | None = None):
    company = packet.get("company", {})
    cid = company.get("id", packet.get("companyId"))
    name = company.get("name", cid)
    result = {"companyId": cid, "companyName": name, "errors": [], "warnings": [], "checks": {}, "selectedMaterials": []}

    def issue(level, code, message, **extra):
        result[level].append({"code": code, "message": message, **extra})

    error = lambda code, msg, **extra: issue("errors", code, msg, **extra)
    warn = lambda code, msg, **extra: issue("warnings", code, msg, **extra)
    materials = index(packet.get("materials", []))
    contacts = index(packet.get("contacts", []))
    existing = board_document(packet.get("existingBoard"))
    old_nodes = [n for n in existing.get("nodes", []) if not blank_seed(n)]
    old_edges = existing.get("edges", [])
    nodes = draft.get("nodes", [])
    edges = draft.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        error("draft_shape", "nodes и edges должны быть массивами")
        return result
    if draft.get("companyId") != cid:
        error("draft_company", "companyId драфта не совпадает с пакетом")
    if review.get("companyId") != cid:
        error("review_company", "companyId review не совпадает с пакетом")
    if review.get("evidenceStrength") not in {"strong", "conditional", "insufficient"}:
        error("strength", "Не указан допустимый evidenceStrength")
    for key in ("summary", "selectedContactReason"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            error("review_field", f"Не заполнено поле review.{key}")
    if not isinstance(review.get("limitations"), list) or not review.get("limitations"):
        error("limitations", "Не указаны ограничения анализа")

    combined = old_nodes + nodes
    combined_edges = old_edges + edges
    by_id = index(combined)
    old_ids = {n["id"] for n in old_nodes}
    for what, values in (("node", combined), ("edge", combined_edges)):
        for ident, count in Counter(v.get("id") for v in values).items():
            if count > 1:
                error("duplicate_" + what, f"Повтор ID: {ident}")
    for n in nodes:
        nid = n.get("id")
        if not isinstance(nid, str) or not nid or len(nid) > 250:
            error("node_id", "Недопустимый ID карточки", nodeId=nid)
        elif not nid.startswith(PREFIX):
            error("id_prefix", "Новая карточка не имеет префикса партии", nodeId=nid)
        if n.get("kind") not in KINDS:
            error("node_kind", "Недопустимый тип карточки", nodeId=nid)
        if n.get("status") not in AGENT_STATES:
            error("agent_status", "Агенту нельзя принимать или отклонять карточки", nodeId=nid)
        if not isinstance(n.get("title"), str) or not n["title"].strip() or len(n["title"]) > 4000:
            error("node_title", "Пустой или недопустимый заголовок", nodeId=nid)
        if not isinstance(n.get("body"), str) or len(n["body"]) > 200_000:
            error("node_body", "Недопустимый текст карточки", nodeId=nid)
        if not finite_position(n):
            error("position", "Недопустимые координаты", nodeId=nid)
        ref = n.get("referenceId")
        if n.get("kind") == "source":
            m = materials.get(ref)
            if m is None:
                error("source_ref", "Источник отсутствует в пакете компании", nodeId=nid, referenceId=ref)
            else:
                if m.get("boardEligible") is False:
                    error("source_ineligible", "Источник не допускается на доску", nodeId=nid, referenceId=ref)
                if m.get("companyID", m.get("companyId", cid)) != cid:
                    error("source_company", "Источник принадлежит другой компании", nodeId=nid)
                relation = str(m.get("relationship", ""))
                if any(t in relation.lower() for t in ("другая компания", "чужая компания", "не относится", "исключен", "исключён")):
                    error("source_affiliation", "Источник помечен как чужой/исключённый", nodeId=nid, relationship=relation)
                if any(t in relation.lower() for t in ("связь не проверена", "требует проверки", "по запросу о компании")):
                    warn("source_affiliation_review", "Принадлежность источника требует содержательной проверки", nodeId=nid, referenceId=ref, relationship=relation)
                result["selectedMaterials"].append({"nodeId": nid, "id": ref, "title": m.get("title"), "url": m.get("url"), "hasFullText": m.get("hasFullText"), "relationship": relation})
        elif n.get("kind") == "contact":
            c = contacts.get(ref)
            if c is None:
                error("contact_ref", "Контакт отсутствует в пакете компании", nodeId=nid, referenceId=ref)
            elif c.get("boardEligible") is not True:
                error("contact_ineligible", "Контакт не имеет boardEligible=true", nodeId=nid, referenceId=ref)
            elif c.get("purpose") in {"channel", "chat", "bot", "unknown", "advertising"}:
                warn("contact_route_review", "Канал/бот/неустановленное назначение: проверить, что не назван ЛПР", nodeId=nid, purpose=c.get("purpose"), address=c.get("address"))
        elif ref is not None:
            error("unexpected_ref", "referenceId допустим только для source/contact", nodeId=nid)

    valid = [n for n in combined if finite_position(n)]
    for i, a in enumerate(valid):
        for b in valid[i + 1:]:
            if overlap(a, b):
                if a.get("id") in old_ids and b.get("id") in old_ids:
                    warn("existing_overlap", "Наложение уже было в исходной доске", nodeIds=[a.get("id"), b.get("id")])
                else:
                    error("overlap", "Карточки 248×220 пересекаются", nodeIds=[a.get("id"), b.get("id")])

    adjacency = defaultdict(set)
    for e in edges:
        eid = e.get("id")
        if not isinstance(eid, str) or not eid or len(eid) > 250 or not eid.startswith(PREFIX):
            error("edge_id", "Недопустимый ID новой связи", edgeId=eid)
        if e.get("kind") not in EDGE_KINDS:
            error("edge_kind", "Недопустимый тип связи", edgeId=eid)
        a, b = by_id.get(e.get("source")), by_id.get(e.get("target"))
        if not a or not b or e.get("source") == e.get("target"):
            error("dangling_edge", "Связь с отсутствующей карточкой или самой собой", edgeId=eid)
            continue
        adjacency[e["source"]].add(e["target"])
        if (a.get("kind"), b.get("kind")) in {("observation", "hypothesis"), ("hypothesis", "offer")} and e.get("kind") != "requiresVerification":
            error("causal_edge", "Гипотеза/оффер должны быть связаны через requiresVerification", edgeId=eid)
        if a.get("kind") == "contact" and e.get("kind") != "contactRationale":
            error("contact_edge", "Обоснование контакта требует contactRationale", edgeId=eid)

    claims = review.get("claims", [])
    if not isinstance(claims, list):
        error("claims_shape", "claims должен быть массивом")
        claims = []
    covered = set()
    for claim in claims:
        nid = claim.get("nodeId")
        n = by_id.get(nid)
        quote = claim.get("quote")
        if not n:
            error("claim_node", "Claim ссылается на отсутствующую карточку", nodeId=nid)
            continue
        if not isinstance(quote, str) or not quote.strip():
            error("claim_quote", "Claim не содержит цитату", nodeId=nid)
            continue
        if claim.get("materialId"):
            mid = claim["materialId"]
            m = materials.get(mid)
            if m is None:
                error("claim_material", "Материал claim отсутствует в пакете", nodeId=nid, materialId=mid)
                continue
            text = m.get("text", "")
            if quote not in text:
                if normalized(quote) in normalized(text):
                    error("quote_not_exact", "Цитата отличается пробелами/переносами от оригинала; требуется точная подстрока", nodeId=nid, materialId=mid)
                else:
                    error("quote_not_found", "Цитата отсутствует в тексте материала", nodeId=nid, materialId=mid, quote=quote)
                continue
            source_nodes = {x["id"] for x in combined if x.get("kind") == "source" and x.get("referenceId") == mid}
            if not source_nodes:
                error("claim_source_card", "Для claim нет source-карточки на доске", nodeId=nid, materialId=mid)
            elif n.get("kind") == "observation" and not any(e.get("source") in source_nodes and e.get("target") == nid and e.get("kind") == "supports" for e in combined_edges):
                error("claim_support_edge", "Наблюдение не связано supports с подтверждающим source", nodeId=nid, materialId=mid)
            covered.add(nid)
        elif claim.get("contactId"):
            contact_id = claim["contactId"]
            c = contacts.get(contact_id)
            if c is None or quote not in c.get("sourceQuote", ""):
                error("contact_quote", "Цитата не найдена в sourceQuote контакта", nodeId=nid, contactId=contact_id)
            if n.get("kind") == "contact" and n.get("referenceId") != contact_id:
                error("claim_contact_mismatch", "Claim подтверждает иной контакт", nodeId=nid)
        else:
            error("claim_reference", "Claim не содержит materialId/contactId", nodeId=nid)
    for n in nodes:
        if n.get("kind") == "observation" and n.get("id") not in covered:
            error("uncovered_observation", "Наблюдение не имеет валидного claim", nodeId=n.get("id"))
        if n.get("kind") in {"hypothesis", "offer"}:
            body = (n.get("title", "") + " " + n.get("body", "")).lower()
            if not any(t in body for t in ("если", "провер", "уточн", "вопрос", "?", "при подтверж", "может", "услов")):
                warn("unconditional_proposal", "Проверить условность гипотезы/оффера", nodeId=n.get("id"))
        if n.get("kind") == "observation":
            body = (n.get("title", "") + " " + n.get("body", "")).lower()
            if any(t in body for t in ("текучест", "падает выруч", "не хватает продав", "дефицит продав", "нехватка продав", "проблема конверсии", "перегружен", "потеря лид", "теряют лид")):
                warn("pain_in_observation", "Проверить, что боль не выведена из рекламы/вакансии как факт", nodeId=n.get("id"))
    if not nodes:
        error("empty_draft", "Нет новых содержательных карточек")
    if len(nodes) > 14:
        warn("board_density", "Более 14 новых карточек; проверить компактность", count=len(nodes))
    if not any(n.get("kind") == "offer" for n in combined):
        warn("no_offer", "Нет offer-карточки: должен быть явный вывод о недостатке фактуры")
    if live is not None:
        live_doc = board_document(live)
        live_nodes, live_edges = index(live_doc.get("nodes", [])), index(live_doc.get("edges", []))
        for n in combined:
            if live_nodes.get(n.get("id")) != n:
                error("live_node_mismatch", "Узел не сохранён в live в ожидаемом виде", nodeId=n.get("id"), existing=n.get("id") in old_ids)
        for e in combined_edges:
            if live_edges.get(e.get("id")) != e:
                error("live_edge_mismatch", "Связь не сохранена в live в ожидаемом виде", edgeId=e.get("id"))
        result["checks"]["liveCompared"] = True
    result["checks"].update({"newNodes": len(nodes), "newEdges": len(edges), "existingNodes": len(old_nodes), "existingEdges": len(old_edges), "observations": sum(n.get("kind") == "observation" for n in nodes), "claims": len(claims), "selectedContacts": sum(n.get("kind") == "contact" for n in nodes)})
    return result


def similar_offers(drafts, packets):
    values = []
    for cid, d in drafts.items():
        name = packets[cid].get("company", {}).get("name", "")
        for n in d.get("nodes", []):
            if n.get("kind") != "offer":
                continue
            text = normalized(n.get("body", "").lower().replace(name.lower(), "КОМПАНИЯ"))
            if len(text) > 90:
                values.append((cid, n.get("id"), text))
    pairs = []
    for i, (a, aid, at) in enumerate(values):
        for b, bid, bt in values[i + 1:]:
            if a == b:
                continue
            score = SequenceMatcher(None, at, bt, autojunk=False).ratio()
            if score >= .84:
                pairs.append({"companies": [a, b], "nodeIds": [aid, bid], "similarity": round(score, 3)})
    return sorted(pairs, key=lambda p: p["similarity"], reverse=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--live-dir", type=Path, help="Optional read-only comparison with canonical board directory")
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--company", action="append", default=[])
    parser.add_argument("--output-prefix", default="audit-report", help="Must begin audit-; written inside run directory")
    args = parser.parse_args()
    if not args.output_prefix.startswith("audit-") or "/" in args.output_prefix:
        parser.error("output prefix must begin audit- and must not contain paths")
    packet_paths = sorted((args.run_dir / "io-export/packets").glob("*.json"))
    packets, drafts, results, pending, file_errors = {}, {}, [], [], []
    manifest_path = args.run_dir / "io-export/manifest.json"
    if manifest_path.exists():
        manifest = read(manifest_path)
        expected_ids = {x["companyId"] for x in manifest.get("companies", [])}
        if args.company:
            expected_ids &= set(args.company)
        present_ids = {x.stem for x in packet_paths}
        for cid in sorted(expected_ids - present_ids):
            file_errors.append({"companyId": cid, "error": "Пакет из manifest отсутствует"})
        if not args.company:
            for path in (args.run_dir / "drafts").glob("*.json"):
                if path.stem not in expected_ids:
                    file_errors.append({"companyId": path.stem, "error": "Драфт не относится к компаниям manifest"})
    for path in packet_paths:
        cid = path.stem
        if args.company and cid not in args.company:
            continue
        try:
            packets[cid] = read(path)
            if packets[cid].get("company", {}).get("id") != cid:
                file_errors.append({"companyId": cid, "error": "Имя файла пакета не соответствует company.id"})
            draft_path, review_path = args.run_dir / "drafts" / path.name, args.run_dir / "reviews" / path.name
            if not draft_path.exists() or not review_path.exists():
                pending.append(cid)
                continue
            drafts[cid] = read(draft_path)
            review = read(review_path)
            live = None
            if args.live_dir:
                live_path = args.live_dir / (hashlib.sha256(cid.encode()).hexdigest() + ".json")
                live = read(live_path) if live_path.exists() else {"nodes": [], "edges": []}
            results.append(audit_company(packets[cid], drafts[cid], review, live))
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            file_errors.append({"companyId": cid, "error": str(exc)})
    similarities = similar_offers(drafts, packets)
    report = {"generatedAt": datetime.now(timezone.utc).isoformat(), "runDirectory": str(args.run_dir), "readOnly": True,
              "packetCount": len(packets), "auditedCount": len(results), "pending": pending, "fileErrors": file_errors,
              "errorCount": sum(len(r["errors"]) for r in results) + len(file_errors), "warningCount": sum(len(r["warnings"]) for r in results),
              "similarOfferReview": similarities, "companies": results,
              "limitations": ["Точные цитаты проверены синтаксически; логическая достаточность требует содержательной проверки.", "boardEligible не доказывает актуальность контакта, роль ЛПР или пригодность для HR-оффера.", "Принадлежность материалов проверяется по сохранённым флагам; незамеченные ошибки исходного корпуса требуют ручного ревью."]}
    args.run_dir.mkdir(parents=True, exist_ok=True)
    (args.run_dir / (args.output_prefix + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Аудит разборов школ", "", f"Пакетов: {len(packets)}. Проверено: {len(results)}. Ожидают: {len(pending)}.",
             f"Ошибок: {report['errorCount']}. Предупреждений для ревью: {report['warningCount']}. Похожих офферов: {len(similarities)}.", ""]
    for r in results:
        if not r["errors"] and not r["warnings"]:
            continue
        lines += [f"## {r['companyName']} · {r['companyId']}", ""]
        for level in ("errors", "warnings"):
            for item in r[level]:
                lines.append(f"- {'ERROR' if level == 'errors' else 'REVIEW'} `{item['code']}`: {item['message']} — {item.get('nodeId', item.get('edgeId', item.get('nodeIds', '')))}")
        lines.append("")
    for item in file_errors:
        lines.append(f"- FILE ERROR {item['companyId']}: {item['error']}")
    if pending:
        lines += ["## Ещё не готовы", "", *[f"- {cid}" for cid in pending], ""]
    if similarities:
        lines += ["## Похожие офферы — проверить вручную", "", *[f"- {', '.join(p['companies'])}: {p['similarity']}" for p in similarities], ""]
    lines += ["## Граница проверки", "", *[f"- {s}" for s in report["limitations"]]]
    (args.run_dir / (args.output_prefix + ".md")).write_text("\n".join(lines) + "\n")
    print(json.dumps({k: report[k] for k in ("packetCount", "auditedCount", "errorCount", "warningCount")}, ensure_ascii=False))
    if report["errorCount"]:
        return 1
    if args.require_complete and (pending or not packets):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
