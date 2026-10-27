"""Local, isolated Zoom Meeting SDK proof of concept; no production imports.

Only explicit authenticated HTTP actions create meetings or fetch recordings.
Zoom API bodies, credentials, ZAKs and signed URLs are never logged or persisted.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.cookies
import http.server
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
HOST_COOKIE = "zoom_poc_host"
SDK_KEYS = ("ZOOM_MEETING_SDK_CLIENT_ID", "ZOOM_MEETING_SDK_CLIENT_SECRET")
REST_KEYS = ("ZOOM_S2S_ACCOUNT_ID", "ZOOM_S2S_CLIENT_ID", "ZOOM_S2S_CLIENT_SECRET", "ZOOM_HOST_USER_ID")
MAX_JSON = 2 * 1024 * 1024
MAX_DOWNLOAD = 200 * 1024 * 1024
ID_RE = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")


class ApiError(Exception):
    def __init__(self, message, status=400, code="invalid_request"):
        super().__init__(message)
        self.status, self.code = status, code


def require(condition, message, status=400, code="invalid_request"):
    if not condition:
        raise ApiError(message, status, code)


def timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fields(body, allowed):
    require(isinstance(body, dict) and not set(body) - set(allowed), "Проверьте поля запроса.")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def sdk_signature(client_id, client_secret, meeting_number, role, now=None):
    """Current Client-ID JWT contract; role must come from authenticated route."""
    require(role in (0, 1) and type(role) is int, "Неверная роль.")
    issued = int(time.time() if now is None else now) - 30
    expires = issued + 7200
    claims = {"appKey": client_id, "mn": str(meeting_number), "role": role,
              "iat": issued, "exp": expires, "tokenExp": expires}
    encoded = ".".join(b64(json.dumps(value, separators=(",", ":")).encode())
                       for value in ({"alg": "HS256", "typ": "JWT"}, claims))
    return encoded + "." + b64(hmac.new(client_secret.encode(), encoded.encode(), hashlib.sha256).digest())


def read_env(path):
    """Deliberately small .env reader: no shell evaluation or variable expansion."""
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()),
                "Неверный формат .env.local.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key.strip()] = value
    return result


class Config:
    def __init__(self, values=None, origin="http://127.0.0.1:4321"):
        self.values = dict(values or {})
        parsed = urllib.parse.urlsplit(origin)
        require(parsed.scheme in ("http", "https") and parsed.hostname
                and not parsed.username and not parsed.password
                and parsed.path in ("", "/") and not parsed.query and not parsed.fragment,
                "Нужен origin без пути и параметров.")
        require(parsed.scheme == "https" or parsed.hostname in ("localhost", "127.0.0.1", "::1"),
                "HTTP допускается только на loopback.")
        self.origin = origin.rstrip("/")
        self.host = parsed.netloc

    def get(self, name):
        return str(self.values.get(name, "")).strip()

    def need(self, names):
        require(all(self.get(name) for name in names), "Zoom ещё не настроен.", 503, "not_configured")

    def status(self):
        missing = [name for name in SDK_KEYS + REST_KEYS if not self.get(name)]
        return {"configured": not missing, "sdkConfigured": all(self.get(k) for k in SDK_KEYS),
                "restConfigured": all(self.get(k) for k in REST_KEYS), "missing": missing,
                "meetingSdkClientId": self.get(SDK_KEYS[0]), "version": "2026-10-25.1"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ZoomProvider:
    def __init__(self, config, opener=None):
        self.config = config
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self._token, self._expiry = "", 0
        self._token_lock = threading.Lock()

    def _json(self, request):
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(MAX_JSON + 1)
            require(len(raw) <= MAX_JSON, "Ответ Zoom слишком большой.", 502, "provider_response")
            value = json.loads(raw)
            require(isinstance(value, dict), "Неверный ответ Zoom.", 502, "provider_response")
            return value
        except urllib.error.HTTPError as error:
            # Do not read/echo upstream bodies: they can contain account data.
            raise ApiError(f"Zoom вернул HTTP {error.code}.", 502, f"zoom_http_{error.code}") from None
        except (OSError, ValueError):
            raise ApiError("Zoom не ответил корректно. Повторите чтение вручную.", 502, "provider_unavailable") from None

    def token(self):
        self.config.need(REST_KEYS)
        with self._token_lock:
            if self._token and time.monotonic() < self._expiry:
                return self._token
            basic = base64.b64encode((self.config.get("ZOOM_S2S_CLIENT_ID") + ":" +
                                      self.config.get("ZOOM_S2S_CLIENT_SECRET")).encode()).decode()
            data = urllib.parse.urlencode({"grant_type": "account_credentials",
                                          "account_id": self.config.get("ZOOM_S2S_ACCOUNT_ID")}).encode()
            result = self._json(urllib.request.Request("https://zoom.us/oauth/token", data=data,
                headers={"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"},
                method="POST"))
            token, lifetime = result.get("access_token"), result.get("expires_in")
            require(isinstance(token, str) and token and type(lifetime) in (int, float)
                    and 60 < lifetime <= 86400, "Zoom не выдал OAuth token.", 502, "provider_response")
            self._token, self._expiry = token, time.monotonic() + lifetime - 60
            return token

    def request(self, path, body=None):
        require(path.startswith("/") and not path.startswith("//"), "Неверный путь Zoom.")
        return self._json(urllib.request.Request("https://api.zoom.us/v2" + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.token(), "Content-Type": "application/json"},
            method="GET" if body is None else "POST"))

    def create(self, topic):
        host = urllib.parse.quote(self.config.get("ZOOM_HOST_USER_ID"), safe="")
        result = self.request(f"/users/{host}/meetings", {
            "topic": topic, "type": 2, "duration": 45,
            "settings": {"host_video": False, "participant_video": False,
                         "join_before_host": False, "waiting_room": True, "auto_recording": "none"}})
        number = str(result.get("id", ""))
        require(re.fullmatch(r"\d{9,11}", number) and isinstance(result.get("password", ""), str)
                and result.get("settings", {}).get("auto_recording") == "none",
                "Zoom не подтвердил встречу без автоматической записи.", 502, "provider_response")
        return {"number": number, "password": result.get("password", ""), "uuid": result.get("uuid", "")}

    def zak(self):
        host = urllib.parse.quote(self.config.get("ZOOM_HOST_USER_ID"), safe="")
        result = self.request(f"/users/{host}/token?type=zak")
        require(isinstance(result.get("token"), str) and result["token"],
                "Zoom не выдал ZAK ведущего.", 502, "provider_response")
        return result["token"]

    def recordings(self, meeting):
        identity = meeting.get("recording_uuid") or meeting["number"]
        # Zoom requires double encoding UUIDs beginning with / or containing //.
        encoded = urllib.parse.quote(identity, safe="")
        if identity.startswith("/") or "//" in identity:
            encoded = urllib.parse.quote(encoded, safe="")
        try:
            result = self.request(f"/meetings/{encoded}/recordings")
        except ApiError as error:
            if error.code == "zoom_http_404":
                return {"uuid": "", "recording_files": []}
            raise
        require(str(result.get("id")) == meeting["number"]
                and isinstance(result.get("recording_files"), list),
                "Zoom вернул записи другой встречи или неверный ответ.", 502, "provider_response")
        if meeting.get("recording_uuid"):
            require(result.get("uuid") == meeting["recording_uuid"],
                    "Zoom вернул другую сессию встречи.", 502, "provider_response")
        return result

    def download(self, url, target, expected_size=0):
        """Bounded private download; bearer only follows explicitly trusted Zoom hosts."""
        deadline, transferred = time.monotonic() + 120, 0
        token = self.token()
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            for _ in range(4):
                parsed = urllib.parse.urlsplit(url)
                host = (parsed.hostname or "").lower()
                require(parsed.scheme == "https" and not parsed.username and not parsed.password
                        and parsed.port in (None, 443) and not parsed.fragment
                        and (host == "zoom.us" or host.endswith(".zoom.us")
                             or host == "zoom.com" or host.endswith(".zoom.com")),
                        "Zoom предложил неподдерживаемый адрес загрузки.", 502, "download_origin")
                request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
                try:
                    response = self.opener.open(request, timeout=15)
                    break
                except urllib.error.HTTPError as error:
                    if error.code in (301, 302, 303, 307, 308) and error.headers.get("Location"):
                        url = urllib.parse.urljoin(url, error.headers["Location"])
                        continue
                    raise ApiError(f"Загрузка Zoom вернула HTTP {error.code}.", 502, "download_http") from None
            else:
                raise ApiError("Слишком много перенаправлений загрузки.", 502, "download_redirects")
            with response:
                declared = response.headers.get("Content-Length")
                require(not declared or declared.isdigit() and int(declared) <= MAX_DOWNLOAD,
                        "Запись превышает лимит 200 MB.", 413, "download_size")
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    while True:
                        require(time.monotonic() < deadline, "Истекло время загрузки.", 504, "download_timeout")
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        transferred += len(chunk)
                        require(transferred <= MAX_DOWNLOAD, "Запись превышает лимит 200 MB.", 413, "download_size")
                        output.write(chunk)
                require(transferred > 0 and (not declared or transferred == int(declared))
                        and (not expected_size or transferred == expected_size),
                        "Zoom вернул неполный файл записи.", 502, "download_incomplete")
            temporary.replace(target)
            return transferred
        except OSError:
            raise ApiError("Не удалось загрузить или сохранить запись.", 502, "download_failed") from None
        finally:
            temporary.unlink(missing_ok=True)


class App:
    def __init__(self, config, state_dir, provider=None):
        self.config, self.state_dir = config, Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        self.recordings_dir = self.state_dir / "recordings"
        self.recordings_dir.mkdir(exist_ok=True, mode=0o700)
        os.chmod(self.recordings_dir, 0o700)
        self.lock = threading.RLock()
        self.provider = provider or ZoomProvider(config)
        token_file = self.state_dir / "host-capability"
        if token_file.exists():
            self.host_capability = token_file.read_text().strip()
            require(re.fullmatch(r"[A-Za-z0-9_-]{40,100}", self.host_capability), "Неверный локальный ключ доступа.")
        else:
            self.host_capability = secrets.token_urlsafe(32)
            descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as file:
                file.write(self.host_capability)
        os.chmod(token_file, 0o600)
        invite = self.state_dir / "host-invite.txt"
        descriptor = os.open(invite, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(config.origin + "/#host=" + self.host_capability + "\n")
        os.chmod(invite, 0o600)
        self.db_path = self.state_dir / "state.sqlite3"
        descriptor = os.open(self.db_path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(descriptor)
        with self.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY, body TEXT NOT NULL, guest_hash TEXT NOT NULL, guest_capability TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, status TEXT NOT NULL, meeting_id TEXT);
                CREATE TABLE IF NOT EXISTS downloads (
                meeting_id TEXT NOT NULL, recording_id TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (meeting_id,recording_id));""")
        os.chmod(self.db_path, 0o600)

    def db(self):
        return sqlite3.connect(self.db_path, timeout=5)

    def is_host(self, cookies):
        return hmac.compare_digest(str(cookies.get(HOST_COOKIE, "")), self.host_capability)

    def meeting(self, local_id):
        require(ID_RE.fullmatch(local_id), "Встреча не найдена.", 404, "not_found")
        with self.db() as db:
            row = db.execute("SELECT body,guest_hash FROM meetings WHERE id=?", (local_id,)).fetchone()
        require(row, "Встреча не найдена.", 404, "not_found")
        return json.loads(row[0]), row[1]

    def authorize(self, cookies, local_id, mode):
        require(mode in ("host", "guest"), "Выберите режим host или guest.")
        meeting, guest_hash = self.meeting(local_id)
        allowed = self.is_host(cookies) if mode == "host" else hmac.compare_digest(
            digest(str(cookies.get("zoom_poc_guest_" + local_id, ""))), guest_hash)
        require(allowed, "Нужна ссылка доступа для выбранной роли.", 401, "unauthorized")
        return meeting

    @staticmethod
    def public_meeting(meeting):
        return {"id": meeting["id"], "topic": meeting["topic"], "meetingNumber": meeting["number"],
                "createdAt": meeting["createdAt"]}

    def creation_result(self, meeting, capability):
        local_id = meeting["id"]
        return {"meeting": self.public_meeting(meeting),
                "guestUrl": self.config.origin + "/#" + urllib.parse.urlencode({"meeting": local_id, "guest": capability}),
                "hostClientUrl": f"/client.html?meeting={local_id}&mode=host",
                "guestClientUrl": f"/client.html?meeting={local_id}&mode=guest"}

    def create(self, body):
        fields(body, ("topic", "requestId"))
        request_id = body.get("requestId", "")
        require(isinstance(request_id, str) and ID_RE.fullmatch(request_id), "Нужен requestId.")
        topic = body.get("topic", "HR Vision · Zoom PoC")
        require(isinstance(topic, str) and 1 <= len(topic.strip()) <= 150, "Название: от 1 до 150 символов.")
        self.config.need(REST_KEYS)
        with self.lock, self.db() as db:
            row = db.execute("SELECT status,meeting_id FROM requests WHERE id=?", (request_id,)).fetchone()
            if row:
                require(row[0] == "complete", "Исход предыдущего создания ещё не подтверждён. Проверьте Zoom.",
                        409, "creation_unconfirmed")
                saved = db.execute("SELECT body,guest_capability FROM meetings WHERE id=?", (row[1],)).fetchone()
                return self.creation_result(json.loads(saved[0]), saved[1])
            db.execute("INSERT INTO requests VALUES (?,'creating',NULL)", (request_id,))
        # A failed/timed out POST is deliberately not repeated automatically.
        created = self.provider.create(topic.strip())
        local_id, capability = "zm_" + secrets.token_hex(12), secrets.token_urlsafe(32)
        meeting = {**created, "id": local_id, "topic": topic.strip(), "createdAt": timestamp()}
        with self.lock, self.db() as db:
            db.execute("INSERT INTO meetings VALUES (?,?,?,?)",
                       (local_id, json.dumps(meeting), digest(capability), capability))
            db.execute("UPDATE requests SET status='complete',meeting_id=? WHERE id=?", (local_id, request_id))
        return self.creation_result(meeting, capability)

    def join(self, meeting, mode, body):
        fields(body, ("displayName",))
        name = body.get("displayName", "")
        require(isinstance(name, str) and 1 <= len(name.strip()) <= 80
                and not any(ord(c) < 32 for c in name), "Укажите тестовое имя участника.")
        self.config.need(SDK_KEYS)
        role = 1 if mode == "host" else 0
        result = {"meetingNumber": meeting["number"], "passWord": meeting["password"],
                  "userName": name.strip(), "role": role, "clientId": self.config.get(SDK_KEYS[0]),
                  "signature": sdk_signature(self.config.get(SDK_KEYS[0]), self.config.get(SDK_KEYS[1]),
                                             meeting["number"], role)}
        if role == 1:
            result["zak"] = self.provider.zak()
        return result

    def recording_metadata(self, meeting):
        result = self.provider.recordings(meeting)
        files = result["recording_files"]
        require(len(files) <= 100, "Слишком много файлов записи.", 502, "provider_response")
        if files and result.get("uuid") and not meeting.get("recording_uuid"):
            meeting = {**meeting, "recording_uuid": result["uuid"]}
            with self.lock, self.db() as db:
                db.execute("UPDATE meetings SET body=? WHERE id=?", (json.dumps(meeting), meeting["id"]))
        with self.db() as db:
            downloaded = {row[0] for row in db.execute("SELECT recording_id FROM downloads WHERE meeting_id=?", (meeting["id"],))}
        metadata = []
        for file in files:
            require(isinstance(file, dict) and isinstance(file.get("id"), str) and ID_RE.fullmatch(file["id"]),
                    "Неверные метаданные записи Zoom.", 502, "provider_response")
            metadata.append({"id": file["id"], "fileType": file.get("file_type", ""),
                "recordingType": file.get("recording_type", ""), "status": file.get("status", "unknown"),
                "fileSize": file.get("file_size", 0), "recordingStart": file.get("recording_start", ""),
                "recordingEnd": file.get("recording_end", ""), "downloaded": file["id"] in downloaded})
        state = "absent" if not metadata else "ready" if any(f["status"] == "completed" for f in metadata) else "processing"
        return {"status": state, "checkedAt": timestamp(), "files": metadata}

    def download(self, meeting, recording_id):
        require(ID_RE.fullmatch(recording_id), "Файл не найден.", 404, "not_found")
        # Serialise local downloads; calls/join never acquire this lock for I/O.
        with self.lock:
            with self.db() as db:
                prior = db.execute("SELECT body FROM downloads WHERE meeting_id=? AND recording_id=?",
                                   (meeting["id"], recording_id)).fetchone()
            if prior:
                prior = json.loads(prior[0])
                if Path(prior["localPath"]).is_file():
                    return prior
            listing = self.provider.recordings(meeting)
            files = [file for file in listing["recording_files"] if file.get("id") == recording_id]
            require(len(files) == 1 and files[0].get("status") == "completed", "Запись пока не готова.", 409, "recording_not_ready")
            file = files[0]
            size = file.get("file_size", 0)
            require(type(size) is int and 0 <= size <= MAX_DOWNLOAD, "Запись превышает лимит 200 MB.", 413, "download_size")
            extension = str(file.get("file_extension") or file.get("file_type") or "bin").lower()
            require(re.fullmatch(r"[a-z0-9]{1,8}", extension), "Неверный тип файла.", 502, "provider_response")
            target = self.recordings_dir / f"{meeting['id']}_{recording_id}.{extension}"
            transferred = self.provider.download(str(file.get("download_url", "")), target, size)
            result = {"id": recording_id, "localPath": str(target), "bytes": transferred,
                      "downloadedAt": timestamp(), "status": "downloaded"}
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO downloads VALUES (?,?,?)", (meeting["id"], recording_id, json.dumps(result)))
            return result


def make_handler(app):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "ZoomPoC/1"

        def log_message(self, *_):
            pass  # Paths/headers can carry capabilities; never log request data.

        def answer(self, status, body, cookie=None):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(raw)

        def cookie(self, name, value, path="/api/"):
            secure = "; Secure" if app.config.origin.startswith("https:") else ""
            return f"{name}={value}; Path={path}; HttpOnly; SameSite=Strict; Max-Age=28800{secure}"

        def handle_api(self, method):
            require(self.headers.get("Host") == app.config.host, "Другой Host.", 403, "origin")
            origin = self.headers.get("Origin")
            require(origin is None or origin == app.config.origin, "Другой Origin.", 403, "origin")
            require(self.headers.get("Sec-Fetch-Site", "same-origin") not in ("cross-site", "same-site"),
                    "Запрос должен идти из этой вкладки.", 403, "origin")
            if method == "POST":
                require(origin == app.config.origin, "Для изменения нужен Origin.", 403, "origin")
            parsed = urllib.parse.urlsplit(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            require(set(params) <= {"mode"} and all(len(values) == 1 for values in params.values()), "Неверные параметры.")
            mode = params.get("mode", [None])[0]
            cookies = http.cookies.SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except http.cookies.CookieError:
                pass
            cookies = {key: value.value for key, value in cookies.items()}
            body = {}
            if method == "POST":
                require(self.headers.get("Content-Type", "").split(";", 1)[0] == "application/json", "Нужен JSON.", 415)
                length = self.headers.get("Content-Length", "")
                require(length.isdigit() and 0 < int(length) <= 16384, "Слишком большой запрос.", 413)
                try:
                    body = json.loads(self.rfile.read(int(length)))
                except (ValueError, UnicodeError):
                    raise ApiError("Неверный JSON.") from None
                require(isinstance(body, dict), "Нужен JSON object.")
            path = parsed.path
            if method == "GET" and path in ("/api/config", "/api/status"):
                return self.answer(200, {**app.config.status(), "hostSession": app.is_host(cookies)})
            if method == "POST" and path == "/api/session":
                fields(body, ("hostCapability",))
                capability = body.get("hostCapability", "")
                require(isinstance(capability, str) and hmac.compare_digest(capability, app.host_capability),
                        "Неверная ссылка ведущего.", 401, "unauthorized")
                return self.answer(200, {"role": "host", "hostSession": True}, self.cookie(HOST_COOKIE, capability))
            if method == "POST" and path == "/api/meetings":
                require(app.is_host(cookies), "Нужен доступ ведущего.", 401, "unauthorized")
                return self.answer(200, app.create(body))
            match = re.fullmatch(r"/api/meetings/([A-Za-z0-9_-]+)(?:/(session|join|recordings)(?:/([A-Za-z0-9_-]+)/download)?)?", path)
            require(match, "Маршрут не найден.", 404, "not_found")
            local_id, action, recording_id = match.groups()
            if method == "POST" and action == "session" and not recording_id:
                fields(body, ("guestCapability",))
                meeting, expected = app.meeting(local_id)
                capability = body.get("guestCapability", "")
                require(isinstance(capability, str) and hmac.compare_digest(digest(capability), expected),
                        "Неверная ссылка гостя.", 401, "unauthorized")
                return self.answer(200, {"role": "guest", "meeting": app.public_meeting(meeting)},
                    self.cookie("zoom_poc_guest_" + local_id, capability, "/api/meetings/" + local_id))
            if action == "recordings":
                require(app.is_host(cookies), "Записи доступны только ведущему.", 401, "unauthorized")
                meeting, _ = app.meeting(local_id)
                if method == "GET" and not recording_id:
                    return self.answer(200, app.recording_metadata(meeting))
                if method == "POST" and recording_id:
                    fields(body, ())
                    return self.answer(200, app.download(meeting, recording_id))
            meeting = app.authorize(cookies, local_id, mode)
            if method == "GET" and action is None:
                result = {"meeting": app.public_meeting(meeting), "role": mode}
                if mode == "host":
                    with app.db() as db:
                        capability = db.execute("SELECT guest_capability FROM meetings WHERE id=?", (local_id,)).fetchone()[0]
                    result["guestUrl"] = app.creation_result(meeting, capability)["guestUrl"]
                return self.answer(200, result)
            if method == "POST" and action == "join" and not recording_id:
                return self.answer(200, app.join(meeting, mode, body))
            raise ApiError("Маршрут не найден.", 404, "not_found")

        def serve_static(self):
            require(self.headers.get("Host") == app.config.host, "Другой Host.", 403, "origin")
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            allowed = {"meeting", "mode", "left"} if path == "/client.html" else (
                {"meeting", "mode"} if path in ("/", "/index.html") else set())
            require(set(params) <= allowed and all(len(values) == 1 for values in params.values())
                    and ("meeting" not in params or ID_RE.fullmatch(params["meeting"][0]))
                    and ("mode" not in params or params["mode"][0] in ("host", "guest"))
                    and ("left" not in params or params["left"][0] == "1"),
                    "Неверные параметры страницы.", 404, "not_found")
            root = (ROOT / "web").resolve()
            target = (root / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
            require(target.is_relative_to(root) and target.is_file() and target.suffix in (".html", ".js", ".css", ".svg", ".ico"),
                    "Файл не найден.", 404, "not_found")
            raw = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(raw)

        def dispatch(self, method):
            try:
                if self.path.startswith("/api/") or method == "POST":
                    return self.handle_api(method)
                return self.serve_static()
            except ApiError as error:
                return self.answer(error.status, {"error": str(error), "code": error.code})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                # No traceback/input body: secrets may be present in locals.
                return self.answer(500, {"error": "Локальный сервер не выполнил запрос.", "code": "internal_error"})

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=4321)
    parser.add_argument("--origin", help="Explicit HTTPS origin if a separately authorised local proxy is used")
    args = parser.parse_args()
    require(1024 <= args.port <= 65535, "Неверный порт.")
    values = {**read_env(ROOT / ".env.local"), **os.environ}
    config = Config(values, args.origin or f"http://{args.host}:{args.port}")
    app = App(config, ROOT / ".local")
    server = http.server.ThreadingHTTPServer((args.host, args.port), make_handler(app))
    print(f"Zoom PoC: {config.origin}; private host invitation: {app.state_dir / 'host-invite.txt'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
