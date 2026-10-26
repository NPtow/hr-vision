"""Validate immutable profiles and calculate an auditable deterministic ranking.

All input probabilities remain unchanged. Conditional numeric probabilities are
derived only for scoring, after validating the original complete distribution.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from . import __version__

VERSION = "recommendations-v1"
UNKNOWN = "insufficient_data"
MAX_POOL = 1000
POLICY = {"version": "tanh-v1", "k": 1, "probability_mode": "conditional_levels"}
# Same rounding tolerance as the producing JEV transport; never renormalize p.
PROBABILITY_TOLERANCE = 1e-5


class InvalidInput(ValueError):
    def __init__(self, code="invalid_input"):
        super().__init__(code)
        self.code = code


def canonical_json(value):
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False)
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise InvalidInput() from None


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def obj(value, required, optional=()):
    if (type(value) is not dict or set(required) - set(value)
            or set(value) - set(required) - set(optional)):
        raise InvalidInput()
    return value


def text(value, *, identifier=False, sha=False):
    if (type(value) is not str or not value.strip() or len(value) > 4096
            or any(ord(c) < 32 for c in value)):
        raise InvalidInput()
    if identifier and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value):
        raise InvalidInput()
    if sha and not re.fullmatch(r"[a-f0-9]{64}", value):
        raise InvalidInput()
    return value


def integer(value, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or maximum is not None and value > maximum:
        raise InvalidInput()
    return value


def probability(value):
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        raise InvalidInput()
    return value


def distribution(values):
    for value in values:
        probability(value)
    if not math.isclose(math.fsum(values), 1, rel_tol=0, abs_tol=PROBABILITY_TOLERANCE):
        raise InvalidInput()


def default_rubric():
    return json.loads((Path(__file__).resolve().parents[1] / "rubrics/sales-v1.json").read_text())


def rubric_parameters(rubric):
    obj(rubric, ("rubric_id", "version", "parameters"))
    text(rubric["rubric_id"], identifier=True)
    text(rubric["version"])
    if type(rubric["parameters"]) is not list or not 1 <= len(rubric["parameters"]) <= 200:
        raise InvalidInput()
    ids = set()
    for parameter in rubric["parameters"]:
        obj(parameter, ("parameter_id", "title", "definition", "levels"))
        pid = text(parameter["parameter_id"], identifier=True)
        if pid in ids:
            raise InvalidInput()
        ids.add(pid)
        text(parameter["title"])
        text(parameter["definition"])
        levels = parameter["levels"]
        if type(levels) is not list or not 2 <= len(levels) <= 10:
            raise InvalidInput()
        lids, descriptions = set(), set()
        for level in levels:
            obj(level, ("level_id", "label", "description"))
            lid = text(level["level_id"], identifier=True)
            text(level["label"])
            text(level["description"])
            if lid == UNKNOWN or lid in lids or level["description"].strip() in descriptions:
                raise InvalidInput()
            lids.add(lid)
            descriptions.add(level["description"].strip())
    return rubric["parameters"]


def metadata(profile, rubric):
    if (profile["rubric_id"] != rubric["rubric_id"]
            or profile["rubric_version"] != rubric["version"]
            or profile["rubric_sha256"] != digest(rubric)):
        raise InvalidInput("incompatible_profiles")
    text(profile["run_id"], sha=True)
    if not re.fullmatch(r"jev-\d+\.\d+\.\d+", text(profile["model"])):
        raise InvalidInput()
    obj(profile["usage"], ("input_tokens", "output_tokens"))
    for value in profile["usage"].values():
        integer(value)


def parameter_map(profile, rubric):
    values = profile["parameters"]
    if type(values) is not list or len(values) != len(rubric["parameters"]):
        raise InvalidInput("incompatible_profiles")
    result = {}
    for parameter in values:
        if type(parameter) is not dict:
            raise InvalidInput()
        pid = text(parameter.get("parameter_id"), identifier=True)
        if pid in result:
            raise InvalidInput()
        result[pid] = parameter
    if set(result) != {p["parameter_id"] for p in rubric["parameters"]}:
        raise InvalidInput("incompatible_profiles")
    return result


def levels_equal(levels, expected, *, probabilities=False):
    if type(levels) is not list or len(levels) != len(expected):
        raise InvalidInput("incompatible_profiles")
    for level, target in zip(levels, expected):
        obj(level, ("level_id", "label", "description", "probability") if probabilities
            else ("level_id", "label", "description"))
        if {k: level[k] for k in target} != target:
            raise InvalidInput("incompatible_profiles")
        if probabilities:
            probability(level["probability"])


def choice(answer, options):
    obj(answer, ("type", "choice", "confidence", "probabilities"))
    if answer["type"] != "choice" or type(answer["choice"]) is not str or answer["choice"] not in options:
        raise InvalidInput()
    probabilities = answer["probabilities"]
    obj(probabilities, options)
    distribution(list(probabilities.values()))
    probability(answer["confidence"])
    if probabilities[answer["choice"]] != max(probabilities.values()):
        raise InvalidInput()


def validate_vacancy(profile, rubric):
    obj(profile, ("contract_version", "profile_id", "run_id", "vacancy_id", "source_revision",
                  "vacancy_sha256", "rubric_id", "rubric_version", "rubric_sha256",
                  "importance_version", "model", "parameters", "usage"), ("revision",))
    if profile["contract_version"] != "vacancy-level-importance-v1":
        raise InvalidInput("incompatible_profiles")
    metadata(profile, rubric)
    text(profile["profile_id"], sha=True)
    text(profile["vacancy_sha256"], sha=True)
    text(profile["vacancy_id"])
    text(profile["source_revision"])
    if profile["importance_version"] != "importance-0-3-v1":
        raise InvalidInput("incompatible_profiles")
    if "revision" in profile:
        obj(profile["revision"], ("author", "parent_profile_id"))
        text(profile["revision"]["author"])
        text(profile["revision"]["parent_profile_id"], sha=True)
    elif profile["profile_id"] != profile["run_id"]:
        raise InvalidInput()
    parameters = parameter_map(profile, rubric)
    needs_clarification = False
    for target in rubric["parameters"]:
        parameter = parameters[target["parameter_id"]]
        obj(parameter, ("parameter_id", "title", "levels", "required_level_id", "importance",
                        "status", "raw_answers"))
        if parameter["title"] != target["title"]:
            raise InvalidInput("incompatible_profiles")
        levels_equal(parameter["levels"], target["levels"])
        level_ids = [level["level_id"] for level in target["levels"]]
        requirement = parameter["required_level_id"]
        if requirement is not None and (type(requirement) is not str or requirement not in level_ids):
            raise InvalidInput()
        importance = parameter["importance"]
        if importance != UNKNOWN:
            integer(importance, maximum=3)
        status = ("needs_review" if importance == UNKNOWN or importance > 0 and requirement is None
                  else "irrelevant" if importance == 0 else "ready")
        if parameter["status"] != status:
            raise InvalidInput()
        obj(parameter["raw_answers"], ("level", "importance"))
        choice(parameter["raw_answers"]["level"], level_ids + [UNKNOWN])
        choice(parameter["raw_answers"]["importance"], ["I0", "I1", "I2", "I3", UNKNOWN])
        if "revision" not in profile:
            raw_level = parameter["raw_answers"]["level"]["choice"]
            raw_importance = parameter["raw_answers"]["importance"]["choice"]
            if (requirement != (None if raw_level == UNKNOWN else raw_level)
                    or importance != (UNKNOWN if raw_importance == UNKNOWN else int(raw_importance[1:]))):
                raise InvalidInput()
        needs_clarification |= status == "needs_review"
    if needs_clarification:
        raise InvalidInput("clarification_required")
    return parameters


def validate_candidate(profile, rubric):
    obj(profile, ("run_id", "candidate_id", "contract_version", "corpus_sha256", "sources",
                  "chunk_count", "rubric_id", "rubric_version", "rubric_sha256", "model", "usage", "parameters"))
    if profile["contract_version"] != "full-corpus-level-choice-v1":
        raise InvalidInput("incompatible_profiles")
    metadata(profile, rubric)
    text(profile["candidate_id"])
    text(profile["corpus_sha256"], sha=True)
    integer(profile["chunk_count"], minimum=1)
    if type(profile["sources"]) is not list or not profile["sources"]:
        raise InvalidInput()
    source_ids = set()
    for source in profile["sources"]:
        obj(source, ("source_id", "version", "kind", "sha256"))
        source_id = text(source["source_id"])
        text(source["version"])
        text(source["sha256"], sha=True)
        if source["kind"] not in ("cv", "interview") or source_id in source_ids:
            raise InvalidInput()
        source_ids.add(source_id)
    parameters = parameter_map(profile, rubric)
    for target in rubric["parameters"]:
        parameter = parameters[target["parameter_id"]]
        obj(parameter, ("parameter_id", "title", "status", "selected_level_id", "levels",
                        "insufficient_data_probability", "model_confidence"))
        if parameter["title"] != target["title"]:
            raise InvalidInput("incompatible_profiles")
        levels_equal(parameter["levels"], target["levels"], probabilities=True)
        probabilities = {level["level_id"]: level["probability"] for level in parameter["levels"]}
        probabilities[UNKNOWN] = parameter["insufficient_data_probability"]
        distribution(list(probabilities.values()))
        probability(parameter["model_confidence"])
        chosen = parameter["selected_level_id"]
        if parameter["status"] == UNKNOWN and chosen is None:
            chosen = UNKNOWN
        elif (parameter["status"] != "assessed" or type(chosen) is not str
              or chosen not in probabilities or chosen == UNKNOWN):
            raise InvalidInput()
        if probabilities[chosen] != max(probabilities.values()):
            raise InvalidInput()
        if chosen != UNKNOWN and math.fsum(probabilities[level["level_id"]] for level in target["levels"]) <= 0:
            raise InvalidInput()
    return parameters


def canonical_ids(value):
    if value is None:
        return
    if type(value) is not list or len(value) > 1000:
        raise InvalidInput()
    for item in value:
        text(item, identifier=True)
    if len(set(value)) != len(value):
        raise InvalidInput()


def validate_fields(vacancy, candidates, expected_ids):
    obj(vacancy, ("revision", "location", "employment", "industry_experience"))
    text(vacancy["revision"])
    for name in ("location", "employment", "industry_experience"):
        requirement = vacancy[name]
        obj(requirement, ("required", "industry_id", "minimum_months") if name == "industry_experience"
            else ("required", "allowed_ids"))
        if requirement["required"] is not None and type(requirement["required"]) is not bool:
            raise InvalidInput()
        if name == "industry_experience":
            if requirement["industry_id"] is not None:
                text(requirement["industry_id"], identifier=True)
            if requirement["minimum_months"] is not None:
                integer(requirement["minimum_months"])
        else:
            canonical_ids(requirement["allowed_ids"])
    if type(candidates) is not list or len(candidates) != len(expected_ids):
        raise InvalidInput()
    result = {}
    for candidate in candidates:
        obj(candidate, ("candidate_id", "revision", "work_locations", "employment_types", "industry_months"))
        cid = text(candidate["candidate_id"])
        text(candidate["revision"])
        if cid in result:
            raise InvalidInput()
        canonical_ids(candidate["work_locations"])
        canonical_ids(candidate["employment_types"])
        months = candidate["industry_months"]
        if months is not None:
            if type(months) is not dict or len(months) > 1000:
                raise InvalidInput()
            for industry, value in months.items():
                text(industry, identifier=True)
                integer(value)
        result[cid] = candidate
    if set(result) != set(expected_ids):
        raise InvalidInput()
    return result


def required_checks(vacancy, candidate):
    checks = []
    for name, candidate_key in (("location", "work_locations"), ("employment", "employment_types"),
                                 ("industry_experience", "industry_months")):
        requirement = vacancy[name]
        state, reason = "unknown", "requirement_unknown"
        if requirement["required"] is False:
            state, reason = "pass", "not_required"
        elif requirement["required"] is True:
            values = candidate[candidate_key]
            if name == "industry_experience":
                industry, minimum = requirement["industry_id"], requirement["minimum_months"]
                if industry is not None and minimum is not None:
                    if values is None or industry not in values:
                        reason = "candidate_unknown"
                    else:
                        state = "pass" if values[industry] >= minimum else "fail"
                        reason = "minimum_met" if state == "pass" else "below_minimum"
            elif requirement["allowed_ids"]:
                if values is None:
                    reason = "candidate_unknown"
                else:
                    state = "pass" if set(values) & set(requirement["allowed_ids"]) else "fail"
                    reason = "allowed_id_match" if state == "pass" else "no_allowed_id_match"
        checks.append({"field": name, "status": state, "reason": reason,
                       "vacancy_fields_revision": vacancy["revision"],
                       "candidate_fields_revision": candidate["revision"]})
    return checks


def recommend(value, *, rubric=None):
    """Pure operation; validation precedes any calculation or durable mutation."""
    canonical_json(value)
    obj(value, ("vacancy_profile", "candidate_profiles", "candidate_fields", "vacancy_fields", "policy"))
    policy = value["policy"]
    obj(policy, POLICY)
    if (policy["version"] != POLICY["version"] or policy["probability_mode"] != POLICY["probability_mode"]
            or type(policy["k"]) not in (int, float) or policy["k"] != 1 or not math.isfinite(policy["k"])):
        raise InvalidInput("unsupported_policy")
    rubric = default_rubric() if rubric is None else rubric
    rubric_parameters(rubric)
    vacancy = value["vacancy_profile"]
    vacancy_parameters = validate_vacancy(vacancy, rubric)
    profiles = value["candidate_profiles"]
    if type(profiles) is not list or not 1 <= len(profiles) <= MAX_POOL:
        raise InvalidInput("invalid_pool_size")
    candidate_parameters, candidate_profiles = {}, {}
    for profile in profiles:
        parameters = validate_candidate(profile, rubric)
        cid = profile["candidate_id"]
        if cid in candidate_profiles:
            raise InvalidInput("duplicate_candidate")
        candidate_profiles[cid] = profile
        candidate_parameters[cid] = parameters
    fields = validate_fields(value["vacancy_fields"], value["candidate_fields"], candidate_profiles)
    recommendation_id = digest({"service_version": __version__, "input": value})
    items = []
    for cid, profile in candidate_profiles.items():
        parameters, included_weights, weighted_matches, excluded_weights = [], [], [], []
        for target in rubric["parameters"]:
            pid = target["parameter_id"]
            vp, cp = vacancy_parameters[pid], candidate_parameters[cid][pid]
            weight = vp["importance"]
            requirement = vp["required_level_id"]
            excluded = "zero_importance" if weight == 0 else UNKNOWN if cp["status"] == UNKNOWN else None
            parameter = {"parameter_id": pid, "title": target["title"], "weight": weight,
                         "required_level_id": requirement, "excluded": excluded is not None,
                         "exclusion_reason": excluded, "m": None, "weighted_contribution": None,
                         "numeric_probability_mass": math.fsum(l["probability"] for l in cp["levels"]),
                         "insufficient_data_probability": cp["insufficient_data_probability"],
                         "model_confidence": cp["model_confidence"], "levels": []}
            if excluded:
                excluded_weights.append(weight)
                for position, level in enumerate(cp["levels"], 1):
                    parameter["levels"].append({"level_id": level["level_id"], "position": position,
                                                "p_raw": level["probability"], "q": None,
                                                "u": None, "contribution": None})
            else:
                required_position = next(i for i, l in enumerate(target["levels"], 1)
                                         if l["level_id"] == requirement)
                for position, level in enumerate(cp["levels"], 1):
                    q = level["probability"] / parameter["numeric_probability_mass"]
                    u = math.tanh(policy["k"] * (position - required_position))
                    parameter["levels"].append({"level_id": level["level_id"], "position": position,
                                                "p_raw": level["probability"], "q": q,
                                                "u": u, "contribution": q * u})
                parameter["required_position"] = required_position
                parameter["m"] = math.fsum(l["contribution"] for l in parameter["levels"])
                parameter["weighted_contribution"] = weight * parameter["m"]
                included_weights.append(weight)
                weighted_matches.append(parameter["weighted_contribution"])
            parameters.append(parameter)
        denominator = sum(included_weights)
        score = math.fsum(weighted_matches) / denominator if denominator else None
        checks = required_checks(value["vacancy_fields"], fields[cid])
        eligibility = ("fail" if any(c["status"] == "fail" for c in checks) else
                       "unknown" if any(c["status"] == "unknown" for c in checks) else "pass")
        items.append({"candidate_id": cid, "candidate_profile_id": profile["run_id"],
                      "candidate_corpus_sha256": profile["corpus_sha256"],
                      "candidate_fields_revision": fields[cid]["revision"],
                      "eligibility": eligibility, "requires_human_approval": eligibility == "unknown",
                      "required_checks": checks, "score": score,
                      "status": "filtered" if eligibility == "fail" else "scored" if score is not None else "unscored",
                      "rank": None, "parameters": parameters,
                      "coverage": {"total_parameters": len(parameters),
                                   "included_parameters": len(included_weights),
                                   "excluded_parameters": len(excluded_weights),
                                   "included_weight": denominator, "excluded_weight": sum(excluded_weights),
                                   "total_weight": denominator + sum(excluded_weights)}})
    items.sort(key=lambda item: (item["status"] == "filtered", item["score"] is None,
                                 -item["score"] if item["score"] is not None else 0, item["candidate_id"]))
    for rank, item in enumerate((i for i in items if i["status"] == "scored"), 1):
        item["rank"] = rank
    return {"contract_version": VERSION, "service_version": __version__,
            "recommendation_id": recommendation_id, "vacancy_id": vacancy["vacancy_id"],
            "vacancy_profile_id": vacancy["profile_id"], "vacancy_revision": vacancy["source_revision"],
            "vacancy_fields_revision": value["vacancy_fields"]["revision"],
            "rubric_id": rubric["rubric_id"], "rubric_version": rubric["version"],
            "rubric_sha256": digest(rubric), "policy": dict(policy),
            "pool": {"size": len(profiles), "candidate_ids": sorted(candidate_profiles)}, "items": items}
