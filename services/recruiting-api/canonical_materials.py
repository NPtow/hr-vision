"""Authorized catalogue read, including verified imports not yet in the catalogue.

An existing, pending or revoked catalogue source always owns its material state.
Only a genuinely empty owner may expose its previously verified DSA import.
"""
import os
import math

from core_source_client import CoreClient, CoreMaterialsCatalog, ServiceHTTP
from domain import require


def read_intake_db(db, workspace_id, vacancy_id, candidate_id):
    import employer_intake
    person, _ = employer_intake._review_person(db, workspace_id, vacancy_id, {'id': candidate_id})
    require(not person.get('archived'), 'Кандидат недоступен.', 404)
    require(os.environ.get('HR_CORE_CATALOG_ENABLED') == '1', 'Каталог материалов ещё не подключён.', 503)
    client = CoreClient(ServiceHTTP(os.environ.get('HR_MATERIALS_CORE_URL', ''),
                                   os.environ.get('HR_MATERIALS_CORE_KEY', ''), timeout=15))
    # No access_check callback: this already-owned connection must never be
    # entered as a context manager and accidentally commit its caller's txn.
    catalog = CoreMaterialsCatalog(client, connect=lambda: db, collect=False)
    result = catalog.legacy_materials('intake', {'id': candidate_id, 'room': {'provider': 'upload'}})
    employer_intake._review_person(db, workspace_id, vacancy_id, {'id': candidate_id})
    profile = person.get('preparedProfile') or {}
    if (result.get('sourceCount') == 0 and not result['recordings'] and not result['transcripts']
            and (profile.get('source') or {}).get('kind') == 'dsa-panel'):
        from dsa_panel import imported_materials
        # Stable source identity permits reuse across vacancies; never infer a
        # candidate match from a name, email or an untrusted browser field.
        source_id = 'dsa-panel:' + str(profile['source'].get('candidateId', ''))
        result = imported_materials({'version': 1, 'candidateId': source_id,
            'interviewId': source_id, 'preparedProfile': profile}, source_id, source_id)
        result['transcripts'][0]['recordingId'] = result['recordings'][0]['id']
        # VTT clocks use whole milliseconds; MP4 duration may use fractions of
        # one millisecond. Compare both clocks at VTT's declared precision.
        result['recordings'][0]['duration'] = math.ceil(result['recordings'][0]['duration'] * 1000) / 1000
        result['materialOrigin'] = 'verified-import'
    return result


def read_intake(portal, workspace_id, vacancy_id, candidate_id):
    with portal.connect() as db:
        return read_intake_db(db, workspace_id, vacancy_id, candidate_id)
