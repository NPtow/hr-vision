"""Playback for employer-owned, imported interview materials.

These are source documents, not candidate applications or evidence of consent.
Only stored Yandex Disk shares can be resolved, after intake ownership is checked.
No candidate notifications, account creation or matching happens here.
"""
import json
import re
import urllib.parse
import urllib.request

from domain import Problem, require
import employer_store as store


def source(db, workspace_id, body):
    require(isinstance(body, dict) and set(body) == {'id', 'index'}, 'Выберите запись.', 400)
    cid, index = body.get('id'), body.get('index')
    require(isinstance(cid, str) and re.fullmatch(r'[a-f0-9]{24}', cid)
            and type(index) is int and 0 <= index < 10, 'Выберите запись.', 400)
    row = db.execute('SELECT vacancy_id,body FROM em_intake_candidates WHERE id=? AND workspace_id=?',
                     (cid, workspace_id)).fetchone()
    require(row is not None, 'Запись не найдена.', 404)
    store.vacancy(db, workspace_id, row[0])
    profile = json.loads(row[1]).get('preparedProfile') or {}
    recordings = profile.get('recordings', [])
    require(isinstance(recordings, list) and index < len(recordings), 'Запись не найдена.', 404)
    recording = recordings[index]
    url = recording.get('publicUrl', '')
    require(isinstance(url, str) and re.fullmatch(r'https://disk\.yandex\.ru/[id]/[A-Za-z0-9_-]{8,120}', url),
            'Не удалось открыть исходную запись.', 422)
    mime = recording.get('mime', '')
    require(isinstance(mime, str) and mime.startswith(('video/', 'audio/')), 'Этот файл не является записью.', 422)
    return {'publicUrl': url, 'mime': mime}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


def resolve(recording):
    """Use the fixed public API, never fetch a caller-supplied host or a video file."""
    url = 'https://cloud-api.yandex.net/v1/disk/public/resources/download?' + urllib.parse.urlencode({
        'public_key': recording['publicUrl'],
    })
    try:
        request = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.build_opener(NoRedirect).open(request, timeout=12) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('Response too large')
        href = json.loads(raw)['href']
        target = urllib.parse.urlsplit(href)
        if (target.scheme != 'https' or target.username or target.password or target.port not in (None, 443)
                or not target.hostname or not (target.hostname == 'downloader.disk.yandex.ru'
                    or target.hostname.endswith('.storage.yandex.net'))):
            raise ValueError('Unexpected media origin')
        return {'url': href, 'mime': recording['mime']}
    except Exception:
        raise Problem('Не удалось открыть запись. Попробуйте ещё раз или откройте исходный файл.', 502) from None
