from copy import deepcopy

from recommendations.engine import UNKNOWN, POLICY, digest


def synthetic_rubric():
    return {"rubric_id": "synthetic", "version": "v1", "parameters": [
        {"parameter_id": name, "title": name, "definition": "Definition " + name,
         "levels": [{"level_id": "L" + str(i), "label": "Level " + str(i),
                     "description": name + " description " + str(i)} for i in range(1, 6)]}
        for name in ("skill_a", "skill_b")]}


def choice(options, selected):
    return {"type": "choice", "choice": selected, "confidence": 0.8,
            "probabilities": {key: 1 if key == selected else 0 for key in options}}


def vacancy_profile(rubric):
    return {"contract_version": "vacancy-level-importance-v1", "profile_id": "a" * 64,
            "run_id": "a" * 64, "vacancy_id": "vacancy-1", "source_revision": "vacancy-revision-1",
            "vacancy_sha256": "b" * 64, "rubric_id": rubric["rubric_id"],
            "rubric_version": rubric["version"], "rubric_sha256": digest(rubric),
            "importance_version": "importance-0-3-v1", "model": "jev-1.14.0",
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "parameters": [{"parameter_id": p["parameter_id"], "title": p["title"],
                            "levels": deepcopy(p["levels"]), "required_level_id": "L3",
                            "importance": 1, "status": "ready", "raw_answers": {
                                "level": choice([l["level_id"] for l in p["levels"]] + [UNKNOWN], "L3"),
                                "importance": choice(["I0", "I1", "I2", "I3", UNKNOWN], "I1")}}
                           for p in rubric["parameters"]]}


def candidate_profile(rubric, cid="candidate-1"):
    return {"run_id": digest(cid), "candidate_id": cid, "contract_version": "full-corpus-level-choice-v1",
            "corpus_sha256": digest({"corpus": cid}),
            "sources": [{"source_id": "cv-1", "version": "v1", "kind": "cv", "sha256": "c" * 64}],
            "chunk_count": 1, "rubric_id": rubric["rubric_id"], "rubric_version": rubric["version"],
            "rubric_sha256": digest(rubric), "model": "jev-1.14.0",
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "parameters": [{"parameter_id": p["parameter_id"], "title": p["title"],
                            "status": "assessed", "selected_level_id": "L3",
                            "levels": [{**deepcopy(l), "probability": prob}
                                       for l, prob in zip(p["levels"], [0, 0.2, 0.5, 0.3, 0])],
                            "insufficient_data_probability": 0, "model_confidence": 0.8}
                           for p in rubric["parameters"]]}


def candidate_fields(cid="candidate-1"):
    return {"candidate_id": cid, "revision": "fields-v1", "work_locations": ["moscow"],
            "employment_types": ["full_time"], "industry_months": {"manufacturing": 24}}


def request_fixture(rubric=None, count=1):
    rubric = synthetic_rubric() if rubric is None else rubric
    ids = ["candidate-" + str(i + 1) for i in range(count)]
    return {"vacancy_profile": vacancy_profile(rubric),
            "candidate_profiles": [candidate_profile(rubric, cid) for cid in ids],
            "candidate_fields": [candidate_fields(cid) for cid in ids],
            "vacancy_fields": {"revision": "vacancy-fields-v1",
                               "location": {"required": True, "allowed_ids": ["moscow"]},
                               "employment": {"required": True, "allowed_ids": ["full_time"]},
                               "industry_experience": {"required": True, "industry_id": "manufacturing", "minimum_months": 12}},
            "policy": dict(POLICY)}


def set_importance(profile, index, importance, required="L3"):
    p = profile["parameters"][index]
    p["importance"], p["required_level_id"] = importance, required
    p["status"] = ("needs_review" if importance == UNKNOWN or importance > 0 and required is None
                   else "irrelevant" if importance == 0 else "ready")
    p["raw_answers"]["importance"] = choice(["I0", "I1", "I2", "I3", UNKNOWN],
                                             UNKNOWN if importance == UNKNOWN else "I" + str(importance))
    p["raw_answers"]["level"] = choice(["L1", "L2", "L3", "L4", "L5", UNKNOWN], required or UNKNOWN)


def set_distribution(parameter, probabilities, unknown=0, selected="L3"):
    for level, probability in zip(parameter["levels"], probabilities):
        level["probability"] = probability
    parameter["insufficient_data_probability"] = unknown
    parameter["selected_level_id"] = selected
    parameter["status"] = UNKNOWN if selected is None else "assessed"
