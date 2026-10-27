"""Bounded, non-blocking OTLP/HTTP telemetry for our stdlib HTTP server.

Only route templates, timings, status, exception types and code locations leave
this process. Never serialize request bodies, headers, locals or error messages.
The collector is optional: missing config or an outage cannot fail a request.
"""
import contextvars
import functools
import json
import os
from pathlib import Path
import queue
import re
import secrets
import threading
import time
import traceback
import urllib.request
from contextlib import contextmanager

_ENDPOINT = os.environ.get('HR_OTEL_ENDPOINT', '').rstrip('/')
_SERVICE = os.environ.get('HR_OTEL_SERVICE', 'mashadasha-api')
_RELEASE = Path(__file__).resolve().parent.name
_QUEUE = queue.Queue(maxsize=256)
_CONTEXT = contextvars.ContextVar('telemetry_context', default=None)
_START_LOCK = threading.Lock()
_STARTED = False
_DROPPED = 0
_TRACEPARENT = re.compile(r'^00-([0-9a-f]{32})-([0-9a-f]{16})-0[01]$')
# Only static route components may appear in telemetry; everything else is an ID.
_ROUTE_WORDS = frozenset('api hr health state action candidate candidates employer recruiter auth session login logout me profile resume resumes download upload availability booking bookings interview interviews meetings meeting tokens notification notifications settings preferences vacancies vacancy imports import intake sources source search publish cancel confirm complete feedback transcript recording recordings media start stop status connection devices calendars calendar slots dates schedule join access links link invite invites telegram miniapp webhook hh outreach campaign campaigns tasks runs job jobs archive draft drafts account password code email verify exchange consent file files photo avatar document documents original cv lab summary notes question questions coach finish analysis retry reminders attachments message messages'.split())


def route_template(path):
    path = str(path).split('?', 1)[0].split('#', 1)[0]
    if not path.startswith('/api/hr/'):
        return '/unknown'
    return '/' + '/'.join(part if part in _ROUTE_WORDS else ':id' for part in path.split('/')[1:12])


def attributes(values):
    return [{'key': key, 'value': ({'boolValue': value} if isinstance(value, bool) else
        {'intValue': str(value)} if isinstance(value, int) else
        {'doubleValue': value} if isinstance(value, float) else {'stringValue': str(value)})}
        for key, value in values.items()]


def _deliver(kind, items):
    resource = {'attributes': attributes({'service.name': _SERVICE,
        'service.version': _RELEASE, 'deployment.environment': 'production'})}
    if kind == 'traces':
        payload = {'resourceSpans': [{'resource': resource,
            'scopeSpans': [{'scope': {'name': 'mashadasha.stdlib'}, 'spans': items}]}]}
    else:
        payload = {'resourceLogs': [{'resource': resource,
            'scopeLogs': [{'scope': {'name': 'mashadasha.stdlib'}, 'logRecords': items}]}]}
    request = urllib.request.Request(_ENDPOINT + '/v1/' + kind,
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    # Ignore ambient proxy settings: endpoint is the local collector.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
        response.read(1024)


def _worker():
    global _DROPPED
    while True:
        batches = {'traces': [], 'logs': []}
        kind, item = _QUEUE.get()
        batches[kind].append(item)
        deadline = time.monotonic() + 2
        while sum(map(len, batches.values())) < 32:
            try:
                kind, item = _QUEUE.get(timeout=max(0, deadline - time.monotonic()))
                batches[kind].append(item)
            except queue.Empty:
                break
        for kind, items in batches.items():
            if items:
                try:
                    _deliver(kind, items)
                except Exception:
                    # Bounded best effort. No retries/backpressure on product traffic.
                    _DROPPED += len(items)


def _emit(kind, item):
    global _STARTED, _DROPPED
    if not _ENDPOINT:
        return
    try:
        with _START_LOCK:
            if not _STARTED:
                threading.Thread(target=_worker, name='observability-export', daemon=True).start()
                _STARTED = True
        _QUEUE.put_nowait((kind, item))
    except Exception:
        _DROPPED += 1


def capture_exception(error, operation='request'):
    if not _ENDPOINT:
        return
    current = _CONTEXT.get()
    locations = '\n'.join(f'{Path(frame.filename).name}:{frame.lineno} in {frame.name}'
        for frame in traceback.extract_tb(error.__traceback__)[-24:])
    fields = {'exception.type': type(error).__name__, 'exception.stacktrace': locations,
              'operation': operation}
    if current:
        current['failed'] = True
        fields.update(current['attributes'])
    record = {'timeUnixNano': str(time.time_ns()), 'severityNumber': 17, 'severityText': 'ERROR',
              'body': {'stringValue': type(error).__name__}, 'attributes': attributes(fields)}
    if current:
        record.update(traceId=current['traceId'], spanId=current['spanId'])
    _emit('logs', record)


@contextmanager
def span(name, *, parent=None, kind=1, fields=None):
    existing = _CONTEXT.get()
    match = _TRACEPARENT.fullmatch(parent or '')
    trace_id = existing['traceId'] if existing else (match[1] if match and int(match[1], 16) and int(match[2], 16) else secrets.token_hex(16))
    parent_id = existing['spanId'] if existing else (match[2] if match and int(match[1], 16) and int(match[2], 16) else '')
    current = {'traceId': trace_id, 'spanId': secrets.token_hex(8), 'attributes': fields or {}, 'failed': False}
    token = _CONTEXT.set(current)
    started = time.time_ns()
    try:
        yield current
    except Exception as error:
        capture_exception(error, name)
        raise
    finally:
        item = {'traceId': trace_id, 'spanId': current['spanId'], 'parentSpanId': parent_id,
                'name': name, 'kind': kind, 'startTimeUnixNano': str(started), 'endTimeUnixNano': str(time.time_ns()),
                'attributes': attributes(current['attributes']), 'status': {'code': 2 if current['failed'] else 1}}
        _CONTEXT.reset(token)
        _emit('traces', item)


def operation(name):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if not _ENDPOINT:
                return function(*args, **kwargs)
            with span(name, kind=3):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def http_request(function):
    @functools.wraps(function)
    def wrapped(handler, *args, **kwargs):
        if not _ENDPOINT:
            return function(handler, *args, **kwargs)
        route = route_template(handler.path)
        with span(handler.command + ' ' + route, parent=handler.headers.get('traceparent'), kind=2,
                  fields={'http.request.method': handler.command, 'http.route': route}) as current:
            return function(handler, *args, **kwargs)
    return wrapped


def response_status(status):
    current = _CONTEXT.get()
    if current:
        current['attributes']['http.response.status_code'] = int(status)
        if int(status) >= 500:
            current['failed'] = True


def install_thread_hook():
    previous = threading.excepthook
    def hook(args):
        capture_exception(args.exc_value, 'thread.unhandled')
        previous(args)
    threading.excepthook = hook
