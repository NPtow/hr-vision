"""Interview app. Credentials and durable answers stay on the server."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
VERSION = "2026-10-20.3"
FIELDS = json.loads((ROOT / "fields.json").read_text())
FIELD_IDS = [field["id"] for field in FIELDS]
MODEL = os.environ.get("INTAKE_MODEL", "gpt-6-luna")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8317/v1").rstrip("/")
PORT = int(os.environ.get("INTAKE_PORT", "4318"))
PUBLIC_ORIGIN = os.environ.get("INTAKE_PUBLIC_ORIGIN", "").rstrip("/")
PUBLIC_HOST = urllib.parse.urlsplit(PUBLIC_ORIGIN).netloc
PRIVATE = bool(PUBLIC_ORIGIN)
STATE_PATH = Path(os.environ.get("INTAKE_STATE_PATH", str(ROOT / "output" / "state.sqlite3")))
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
LOCK = threading.Lock()
WELCOME = FIELDS[0]["question"]
PROMPTS = {stage: (ROOT / "prompts" / "v3" / (stage + ".md")).read_text() for stage in ("extract", "interview", "judge")}


class Problem(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def db():
    connection = sqlite3.connect(str(STATE_PATH), timeout=20)
    connection.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, body TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS usage (session TEXT PRIMARY KEY, calls INTEGER NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS drafts (session TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS receipts (session TEXT NOT NULL, message TEXT NOT NULL, fingerprint TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(session,message))")
    connection.execute("CREATE TABLE IF NOT EXISTS audio_usage (session TEXT PRIMARY KEY, calls INTEGER NOT NULL)")
    connection.commit()
    return connection


def cached(key):
    with db() as connection:
        row = connection.execute("SELECT body FROM cache WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def cache(key, body):
    with db() as connection:
        connection.execute("INSERT OR REPLACE INTO cache VALUES (?,?)", (key, dumps(body)))


def charge(session):
    with db() as connection:
        connection.execute("INSERT OR IGNORE INTO usage VALUES (?,0)", (session,))
        count = connection.execute("SELECT calls FROM usage WHERE session=?", (session,)).fetchone()[0]
        if count >= 160:
            raise Problem("SESSION_BUDGET", 429)
        connection.execute("UPDATE usage SET calls=calls+1 WHERE session=?", (session,))


def strict_object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


PATCH_SCHEMA = strict_object({"updates": {
    "type": "array", "maxItems": 24,
    "items": strict_object({
        "field": {"type": "string", "enum": FIELD_IDS},
        "status": {"type": "string", "enum": ["captured", "needs_detail", "unknown"]},
        "message_id": {"type": "string", "minLength": 1, "maxLength": 100},
        "quote": {"type": "string", "minLength": 1, "maxLength": 1600},
        "summary": {"type": "string", "minLength": 1, "maxLength": 200},
    })
}})
INTERVIEW_SCHEMA = strict_object({"message": {"type": "string", "minLength": 1, "maxLength": 220}})
JUDGE_SCHEMA = strict_object({"pass": {"type": "boolean"}})
ANSWER_SCHEMA = strict_object({
    "status": {"type": "string", "enum": ["empty", "captured", "needs_detail", "unknown"]},
    "quotes": {"type": "array", "maxItems": 12, "items": strict_object({
        "message_id": {"type": "string", "minLength": 1, "maxLength": 100},
        "quote": {"type": "string", "minLength": 1, "maxLength": 1600},
    })},
    "manual": {"type": ["string", "null"], "maxLength": 5000},
    "summary": {"type": "string", "maxLength": 200},
})
INPUT_SCHEMA = strict_object({
    "session": {"type": "string", "pattern": "^[a-zA-Z0-9-]{20,80}$"},
    "message_id": {"type": "string", "pattern": "^[a-zA-Z0-9-]{12,100}$"},
    "message": {"type": "string", "minLength": 1, "maxLength": 6000},
    "focus": {"type": ["string", "null"], "enum": FIELD_IDS + [None]},
    "question": {"type": "string", "maxLength": 400},
    "history": {"type": "array", "maxItems": 82, "items": strict_object({
        "id": {"type": "string", "minLength": 1, "maxLength": 100},
        "role": {"type": "string", "enum": ["user", "assistant"]},
        "content": {"type": "string", "minLength": 1, "maxLength": 6000},
    })},
    "brief": strict_object({field_id: ANSWER_SCHEMA for field_id in FIELD_IDS}),
})
INJECTION = re.compile(
    r"(ignore (all |the |your )?(previous|system) instructions|"
    r"игнориру[йя].{0,25}(инструкц|правил)|"
    r"(раскрой|покажи|выведи).{0,25}(системн.{0,12}промпт|api.?ключ)|"
    r"заполни все.{0,20}(выдум|придум))", re.I | re.S)


def scan_text(text):
    return "[[REDACTED_INJECTION]]" if INJECTION.search(text) else text


def scan(value):
    if isinstance(value, str):
        return scan_text(value)
    if isinstance(value, list):
        return [scan(item) for item in value]
    if isinstance(value, dict):
        return {key: scan(item) for key, item in value.items()}
    return value


def empty_brief():
    return {key: {"status": "empty", "quotes": [], "manual": None, "summary": ""} for key in FIELD_IDS}


def apply_patch(brief, patch, message_id, text):
    Draft202012Validator(PATCH_SCHEMA).validate(patch)
    result = deepcopy(brief)
    for item in patch["updates"]:
        if item["message_id"] != message_id or item["quote"] not in text:
            raise ValueError("unsupported_quote")
        answer = result[item["field"]]
        if answer["manual"] is not None:
            raise ValueError("manual_field")
        quote = {"message_id": message_id, "quote": item["quote"]}
        if quote not in answer["quotes"]:
            if len(answer["quotes"]) >= 12:
                raise ValueError("quote_limit")
            answer["quotes"].append(quote)
        answer["status"] = item["status"]
        answer["summary"] = item["summary"]
    return result


def next_field(brief):
    # A single targeted clarification, then continue with other gaps.
    for field in FIELDS:
        answer = brief[field["id"]]
        if answer["status"] == "needs_detail" and len(answer["quotes"]) < 2:
            return field
    for field in FIELDS:
        if brief[field["id"]]["status"] == "empty":
            return field
    return None


def model_call(stage, payload, schema, session, extra_validation=None):
    prompt = PROMPTS[stage]
    sanitized = scan(payload)
    fingerprint = dumps({"version": VERSION, "stage": stage, "model": MODEL, "prompt": prompt, "input": sanitized})
    key = "stage:" + sha256(fingerprint.encode()).hexdigest()
    result = cached(key)
    if result is not None:
        return result
    if len(fingerprint.encode()) > 130000:
        raise Problem("CONTEXT_LIMIT", 413)
    if not os.environ.get("OPENAI_API_KEY"):
        raise Problem("MODEL_NOT_CONFIGURED", 503)
    fence = "DATA_" + secrets.token_hex(16)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": fence + "\n" + dumps(sanitized) + "\n" + fence},
    ]
    for attempt in range(2):
        charge(session)
        body = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "max_tokens": 1800 if stage == "extract" else 700,
            "reasoning_effort": "low",
            "response_format": {"type": "json_schema", "json_schema": {"name": "intake_" + stage, "strict": True, "schema": schema}},
        }
        started = time.monotonic()
        request = urllib.request.Request(BASE_URL + "/chat/completions", dumps(body).encode(), headers={
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
            "Content-Type": "application/json",
            "Idempotency-Key": key,
        })
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read(400000)
            envelope = json.loads(raw)
            content = envelope["choices"][0]["message"]["content"]
        except Exception:
            raise Problem("MODEL_UNAVAILABLE", 503)
        print(dumps({"stage": stage, "version": VERSION, "model": MODEL,
                     "prompt_hash": sha256(prompt.encode()).hexdigest()[:16],
                     "input_hash": key, "attempt": attempt, "duration_ms": round((time.monotonic() - started) * 1000),
                     "usage": envelope.get("usage", {})}), flush=True)
        try:
            result = json.loads(content)
            Draft202012Validator(schema).validate(result)
            if extra_validation:
                extra_validation(result)
            cache(key, result)
            return result
        except Exception:
            if attempt == 1:
                raise Problem("INVALID_MODEL_OUTPUT", 502)
            # Retry once with a static error; never echo provider output or user data into system instructions.
            messages.append({"role": "user", "content": "VALIDATION_ERROR: неверная структура, недословная цитата, несуществующий message_id или попытка изменить manual. Верни исправленный JSON по схеме. Не добавляй факты."})
    raise Problem("INVALID_MODEL_OUTPUT", 502)


def turn(body):
    try:
        Draft202012Validator(INPUT_SCHEMA).validate(body)
    except Exception:
        raise Problem("INVALID_INPUT")
    if not body["message"].strip():
        raise Problem("EMPTY_INPUT")
    if sum(message["role"] == "user" for message in body["history"]) >= 40:
        raise Problem("CONVERSATION_LIMIT", 429)
    key = "turn:" + sha256(dumps({"body": body, "version": VERSION, "model": MODEL}).encode()).hexdigest()
    previous = cached(key)
    if previous is not None:
        return previous
    source = scan_text(body["message"])
    if source == "[[REDACTED_INJECTION]]":
        result = {"brief": body["brief"], "message": "Какой результат вы ждёте от нового человека?", "updated": [], "version": VERSION, "focus": body["focus"]}
        cache(key, result)
        return result
    # Previous model text and manual edits are scanned on re-entry too.
    context = scan({
        "fields": FIELDS,
        "brief": body["brief"],
        "history": body["history"],
        "latest_user": {"id": body["message_id"], "content": source},
        "selected_topic": next((field for field in FIELDS if field["id"] == body["focus"]), None),
        "current_question": body["question"],
    })
    extracted = model_call("extract", context, PATCH_SCHEMA, body["session"],
                           lambda patch: apply_patch(body["brief"], patch, body["message_id"], source))
    updated_brief = apply_patch(body["brief"], extracted, body["message_id"], source)
    chosen = next_field(updated_brief)
    focused = next((field for field in FIELDS if field["id"] == body["focus"]), None)
    if focused and updated_brief[focused["id"]]["status"] == "empty":
        chosen = focused
    question = chosen["question"] if chosen else "Основные темы пройдены. Что хотите дополнить или исправить в заявке?"
    # No evidence means no free-form factual generation. Use an authored question.
    all_quotes = [quote for answer in updated_brief.values() for quote in answer["quotes"]]
    manually_known = any(answer["manual"] for answer in updated_brief.values())
    if all_quotes or manually_known:
        generated = model_call("interview", {
            "brief": updated_brief, "fields": FIELDS, "latest_user": source,
            "recent_history": body["history"][-6:], "next_topic": chosen,
        }, INTERVIEW_SCHEMA, body["session"])
        judgment = model_call("judge", {
            "brief": updated_brief, "updates": extracted["updates"], "latest_user": source,
            "current_question": body["question"], "next_topic": chosen, "reply": generated["message"],
        }, JUDGE_SCHEMA, body["session"])
        if judgment["pass"]:
            question = generated["message"]
        else:
            for item in extracted["updates"]:
                updated_brief[item["field"]]["summary"] = ""
    result = {"brief": updated_brief, "message": question,
              "updated": list(dict.fromkeys(item["field"] for item in extracted["updates"])), "version": VERSION,
              "focus": chosen["id"] if chosen else None}
    cache(key, result)
    return result


def fresh_state(session):
    return {"session": session, "version": VERSION, "revision": 0, "brief": empty_brief(),
            "messages": [{"id": "welcome", "role": "assistant", "content": WELCOME}], "pending": None,
            "activeField": FIELD_IDS[0], "activeQuestion": WELCOME, "draft": ""}


def load_state(session):
    with db() as connection:
        row = connection.execute("SELECT body FROM drafts WHERE session=?", (session,)).fetchone()
    return json.loads(row[0]) if row else fresh_state(session)


def write_state(state, connection):
    connection.execute("INSERT OR REPLACE INTO drafts VALUES (?,?,?)",
                       (state["session"], state["revision"], dumps(state)))


def committed_turn(session, body, revision):
    """Only the durable history is sent to the model; stale tabs cannot replace it."""
    state = load_state(session)
    message_id, message = body.get("message_id"), body.get("message")
    if not isinstance(message_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{12,100}", message_id):
        raise Problem("INVALID_INPUT")
    fingerprint = sha256(dumps({"message": message, "id": message_id, "focus": body.get("focus"),
                               "question": body.get("question", "")}).encode()).hexdigest()
    with db() as connection:
        receipt = connection.execute("SELECT fingerprint,body FROM receipts WHERE session=? AND message=?",
                                     (session, message_id)).fetchone()
    if receipt:
        prior = json.loads(receipt[1])
        if receipt[0] != fingerprint or prior["state"]["revision"] != state["revision"]:
            raise Problem("REVISION_CONFLICT", 409)
        return prior
    if revision != state["revision"]:
        raise Problem("REVISION_CONFLICT", 409)
    payload = {"session": session, "message_id": message_id, "message": message,
               "history": state["messages"], "brief": state["brief"],
               "focus": body.get("focus"), "question": body.get("question", "")}
    result = turn(payload)
    state["brief"] = result["brief"]
    if state["messages"][-1]["content"] != payload["question"]:
        state["messages"].append({"id": "question-" + message_id, "role": "assistant", "content": payload["question"]})
    state["messages"].extend([
        {"id": message_id, "role": "user", "content": message},
        {"id": "reply-" + message_id, "role": "assistant", "content": result["message"]},
    ])
    state["revision"] += 1
    state["version"] = VERSION
    state["activeField"], state["activeQuestion"] = result["focus"], result["message"]
    result["state"] = state
    with db() as connection:
        write_state(state, connection)
        connection.execute("INSERT INTO receipts VALUES (?,?,?,?)",
                           (session, message_id, fingerprint, dumps(result)))
    return result


def edit_state(session, body):
    state = load_state(session)
    if body.get("revision") != state["revision"]:
        raise Problem("REVISION_CONFLICT", 409)
    field, value = body.get("field"), body.get("value")
    if field not in FIELD_IDS or (value is not None and (not isinstance(value, str) or len(value) > 5000)):
        raise Problem("INVALID_INPUT")
    answer = state["brief"][field]
    answer["manual"] = value.strip() if isinstance(value, str) else None
    answer["status"] = ("captured" if answer["manual"] else "empty") if value is not None else (
        "needs_detail" if answer["quotes"] else "empty")
    state["revision"] += 1
    with db() as connection:
        write_state(state, connection)
    return state


def authorized_session(token):
    if not token.isascii() or len(token) > 100:
        raise Problem("INVITE_REQUIRED", 401)
    for name in ("INVITE", "TEST"):
        expected = os.environ.get("INTAKE_" + name + "_KEY", "")
        if expected and secrets.compare_digest(token, expected):
            return os.environ["INTAKE_" + name + "_SESSION"]
    raise Problem("INVITE_REQUIRED", 401)


AUDIO_TYPES = {"audio/webm", "audio/mp4", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mpeg"}


def transcribe(audio, mime, session):
    if mime.split(";")[0] not in AUDIO_TYPES:
        raise Problem("AUDIO_TYPE", 415)
    if len(audio) < 100 or len(audio) > 5_000_000:
        raise Problem("AUDIO_SIZE", 413)
    if not (audio.startswith((b"\x1a\x45\xdf\xa3", b"OggS", b"RIFF", b"ID3", b"\xff")) or audio[4:8] == b"ftyp"):
        raise Problem("AUDIO_TYPE", 415)
    key = "audio:" + sha256(session.encode() + audio).hexdigest()
    previous = cached(key)
    if previous is not None:
        return previous
    credential = os.environ.get("DEEPGRAM_API_KEY")
    if not credential:
        raise Problem("VOICE_NOT_CONFIGURED", 503)
    with db() as connection:
        connection.execute("INSERT OR IGNORE INTO audio_usage VALUES (?,0)", (session,))
        count = connection.execute("SELECT calls FROM audio_usage WHERE session=?", (session,)).fetchone()[0]
        if count >= 60:
            raise Problem("VOICE_BUDGET", 429)
        connection.execute("UPDATE audio_usage SET calls=calls+1 WHERE session=?", (session,))
    request = urllib.request.Request(
        "https://api.deepgram.com/v1/listen?model=nova-3&language=ru&smart_format=true&punctuate=true&mip_opt_out=true",
        audio, headers={"Authorization": "Token " + credential, "Content-Type": mime})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read(1_000_000))
        text = payload["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        duration = float(payload["metadata"]["duration"])
    except Exception:
        raise Problem("TRANSCRIPTION_FAILED", 503)
    if duration > 125:
        raise Problem("AUDIO_TOO_LONG", 413)
    if not text:
        raise Problem("NO_SPEECH", 422)
    if len(text) > 6000:
        raise Problem("TRANSCRIPT_TOO_LONG", 413)
    result = {"text": text}
    cache(key, result)
    print(dumps({"stage": "transcribe", "duration_seconds": round(duration, 1),
                 "input_hash": key, "characters": len(text)}), flush=True)
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, code, value, content_type="application/json; charset=utf-8", cookie=None):
        data = value if isinstance(value, bytes) else dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        self.send_header("X-Robots-Tag", "noindex, nofollow, nosnippet")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def local(self):
        host = self.headers.get("Host", "")
        allowed = (PUBLIC_HOST,) if PRIVATE else ("127.0.0.1:" + str(PORT), "localhost:" + str(PORT))
        if host not in allowed:
            raise Problem("HOST_NOT_ALLOWED", 403)
        origin = self.headers.get("Origin")
        expected = PUBLIC_ORIGIN if PRIVATE else "http://" + host
        if origin and origin != expected:
            raise Problem("ORIGIN_NOT_ALLOWED", 403)
        if PRIVATE and self.command == "POST" and origin != expected:
            raise Problem("ORIGIN_REQUIRED", 403)

    def session(self):
        if not PRIVATE:
            session = self.headers.get("X-Intake-Session", "local-preview-session-0001")
            if not re.fullmatch(r"[a-zA-Z0-9-]{20,80}", session):
                raise Problem("INVALID_INPUT")
            return session
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookies["pulse_access"].value if "pulse_access" in cookies else ""
        return authorized_session(token)

    def read_body(self, maximum):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise Problem("INPUT_LIMIT", 413)
        if size < 1 or size > maximum:
            raise Problem("INPUT_LIMIT", 413)
        self.connection.settimeout(20)
        body = self.rfile.read(size)
        if len(body) != size:
            raise Problem("INVALID_INPUT")
        return body

    def do_GET(self):
        try:
            self.local()
            if self.path == "/api/config":
                session = self.session()
                return self.respond(200, {"fields": FIELDS, "welcome": WELCOME, "version": VERSION,
                    "configured": bool(os.environ.get("OPENAI_API_KEY")), "voice": bool(os.environ.get("DEEPGRAM_API_KEY")),
                    "persistent": PRIVATE, "session": session if PRIVATE else None})
            if self.path == "/api/state":
                return self.respond(200, load_state(self.session()))
            if self.path == "/healthz":
                return self.respond(200, {"status": "ok", "version": VERSION})
            assets = {"/": ("index.html", "text/html; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/voice.js": ("voice.js", "text/javascript; charset=utf-8")}
            if self.path not in assets:
                raise Problem("NOT_FOUND", 404)
            name, content_type = assets[self.path]
            self.respond(200, (ROOT / "web" / name).read_bytes(), content_type)
        except Problem as problem:
            self.respond(problem.status, {"error": problem.code})

    def do_POST(self):
        try:
            self.local()
            if self.path == "/api/unlock" and PRIVATE:
                body = json.loads(self.read_body(1000))
                token = body.get("token", "")
                if not isinstance(token, str):
                    raise Problem("INVALID_INPUT")
                authorized_session(token)
                return self.respond(200, {"ok": True}, cookie="pulse_access=" + token + "; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=7776000")
            if self.path not in ("/api/turn", "/api/edit", "/api/transcribe"):
                raise Problem("NOT_FOUND", 404)
            session = self.session()
            if self.path == "/api/transcribe":
                audio = self.read_body(5_000_000)
                with LOCK:
                    result = transcribe(audio, self.headers.get("Content-Type", ""), session)
                return self.respond(200, result)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise Problem("CONTENT_TYPE", 415)
            body = json.loads(self.read_body(140000))
            with LOCK:
                if self.path == "/api/edit":
                    result = edit_state(session, body)
                elif PRIVATE:
                    if body.get("session") != session:
                        raise Problem("SESSION_MISMATCH", 403)
                    result = committed_turn(session, body, int(self.headers.get("X-Revision", "-1")))
                else:
                    result = turn(body)
            self.respond(200, result)
        except Problem as problem:
            self.respond(problem.status, {"error": problem.code})
        except Exception:
            self.respond(500, {"error": "REQUEST_FAILED"})


if __name__ == "__main__":
    os.umask(0o077)
    if PRIVATE:
        if not PUBLIC_ORIGIN.startswith("https://") or len(os.environ.get("INTAKE_INVITE_KEY", "")) < 32:
            raise RuntimeError("Public mode requires HTTPS and a private invitation")
    with db():
        pass
    os.chmod(STATE_PATH, 0o600)
    print("Pulse intake: http://127.0.0.1:%s" % PORT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
