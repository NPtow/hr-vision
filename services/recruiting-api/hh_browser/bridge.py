"""Authenticated local RPC and a queue drained only by hh-daily's owner thread.

The client never retries mutating requests. The worker stores its own pre-click
barrier in addition to the campaign engine's before_send transaction.
"""
from dataclasses import dataclass, field
import fcntl
import hmac
import json
import os
from pathlib import Path
import queue
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .adapter import BrowserError, HHBrowserAdapter, SelectorProfile, identifier
from .store import BrowserStore

MAX_BODY = 256 * 1024
MAX_RESPONSE = 20 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class BrowserBridgeClient:
    def __init__(self, base_url='http://127.0.0.1:8140', token='', *, timeout=95):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise BrowserError('bridge_requires_loopback_tunnel')
        self.base_url, self.token, self.timeout = base_url.rstrip('/'), token, timeout

    def _request(self, action, body=None):
        raw = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base_url + '/rpa/' + action, data=raw,
            method='GET' if body is None else 'POST',
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise BrowserError('bridge_response_too_large')
                result = json.loads(raw)
                return result
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read(4096))
                code = body.get('error', 'bridge_rejected')
            except (ValueError, UnicodeDecodeError):
                code = 'bridge_rejected'
            # Never include HTML, request URLs, message text or provider errors.
            if not isinstance(code, str) or not code.replace('_', '').isalnum() or len(code) > 100:
                code = 'bridge_rejected'
            raise BrowserError(code) from None
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            raise BrowserError('bridge_unavailable') from None

    def status(self):
        return self._request('status')

    def begin_discovery(self, source):
        return self._request('discover', {'source': source})

    def discovery_status(self, operation_id):
        return self._request('operations/' + identifier(operation_id))

    def discover(self, source, *, wait_timeout=120):
        operation = self.begin_discovery(source)
        deadline = time.monotonic() + wait_timeout
        while operation['status'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(0.25)
            operation = self.discovery_status(operation['operationId'])
        if operation['status'] == 'completed':
            return operation['result'] | {'operationId': operation['operationId']}
        return operation

    def prepare(self, target, message):
        return self._request('prepare', {'target': target, 'message': message})

    def send(self, prepared_id, attempt_id, *, before_send):
        # Caller must commit the campaign's send_started here. A false result or
        # exception prevents the HTTP send entirely. The callback is never retried.
        if before_send(attempt_id) is False:
            raise BrowserError('before_send_rejected')
        try:
            return self._request('send', {'preparedId': prepared_id, 'attemptId': attempt_id, 'campaignStarted': True})
        except BrowserError as error:
            return {'outcome': 'unknown', 'reason': error.code, 'attemptId': attempt_id}

    def observe(self, attempt_id, *, recheck=False):
        return self._request('observe', {'attemptId': attempt_id, 'recheck': recheck})

    def pause(self, reason='operator_pause'):
        return self._request('pause', {'reason': reason})

    def resume(self):
        return self._request('resume', {})

    def manual(self, enabled):
        return self._request('manual', {'enabled': enabled})

    def acknowledge(self, attempt_id):
        return self._request('acknowledge', {'attemptId': attempt_id})


@dataclass
class _Command:
    action: str
    body: dict
    ready: threading.Event = field(default_factory=threading.Event)
    result: object = None
    cancelled: bool = False
    started: bool = False
    operation_id: str = ''


class WorkerBridge:
    def __init__(self, data_dir, profile, token, *, account_id='', clock=time.time):
        if not isinstance(token, str) or len(token) < 32:
            raise BrowserError('invalid_bridge_token')
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.lockfile = open(self.root / 'owner.lock', 'a+')
        os.chmod(self.root / 'owner.lock', 0o600)
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lockfile.close()
            raise BrowserError('browser_owner_exists') from None
        self.store = BrowserStore(self.root / 'effects.sqlite3', clock=clock)
        self.profile, self.token, self.account_id, self.clock = profile, token, account_id, clock
        self.commands = queue.Queue(maxsize=8)
        self.prepared = None
        self.owner = None
        self.cached = {'configured': True, 'loggedIn': False, 'accountId': account_id,
                       'busy': False, 'captcha': False,
                       'error': 'not_checked' if profile.data.get('calibrated') is True else 'selectors_not_calibrated',
                       'selectorsCalibrated': profile.data.get('calibrated') is True}
        if profile.data.get('calibrated') is not True:
            self.cached['accountId'] = ''

    @classmethod
    def from_env(cls):
        if os.environ.get('HH_RPA_ENABLED') != '1':
            return None
        # Missing config deliberately fails worker startup instead of launching a
        # partially configured sender. No hard-coded/default credential.
        return cls(os.environ['HH_RPA_DATA_DIR'], SelectorProfile.from_file(os.environ['HH_RPA_SELECTORS']),
                   Path(os.environ['HH_RPA_TOKEN_FILE']).read_text().strip(),
                   account_id=os.environ.get('HH_RPA_ACCOUNT_ID', ''))

    def close(self):
        fcntl.flock(self.lockfile, fcntl.LOCK_UN)
        self.lockfile.close()

    def _expire_prepared(self):
        if self.prepared and self.prepared['expires'] <= self.clock():
            self.prepared = None

    def legacy_allowed(self):
        self._expire_prepared()
        ctl = self.store.control()
        return not (ctl['paused'] or ctl['manual'] or self.prepared or self.owner or not self.commands.empty())

    def begin_legacy(self):
        if not self.legacy_allowed():
            return False
        self.owner = 'legacy_parser'
        return True

    def end_legacy(self):
        self.owner = None

    def status(self):
        ctl = self.store.control()
        return self.cached | {'busy': bool(self.owner or self.prepared), 'owner': self.owner,
                              'paused': bool(ctl['paused']), 'pauseReason': ctl['reason'],
                              'manual': bool(ctl['manual']), 'queued': self.commands.qsize()}

    def request(self, method, path, body, authorization, *, timeout=90):
        if not hmac.compare_digest(str(authorization or ''), 'Bearer ' + self.token):
            return 401, {'error': 'bridge_unauthorized'}
        if path == '/rpa/status' and method == 'GET':
            # Cached owner-thread observation; does not navigate the page.
            return 200, self.status()
        if path.startswith('/rpa/operations/') and method == 'GET':
            try:
                return 200, self.store.operation(identifier(path.rsplit('/', 1)[-1]))
            except BrowserError as error:
                return 404, {'error': error.code}
        if method != 'POST' or path not in {'/rpa/' + name for name in ('discover', 'prepare', 'send', 'observe', 'pause', 'resume', 'manual', 'acknowledge')}:
            return 404, {'error': 'unknown_bridge_command'}
        if not isinstance(body, dict):
            return 400, {'error': 'invalid_body'}
        action = path.rsplit('/', 1)[-1]
        if action == 'pause':
            self.store.pause('operator_pause')
            # Persistent immediately; an in-flight browser action is not claimed
            # to be stopped. The effect journal decides its final outcome.
            return 200, self.status()
        command = _Command(action, body)
        if action == 'discover':
            command.operation_id = secrets.token_hex(16)
            self.store.operation_create(command.operation_id)
        try:
            self.commands.put_nowait(command)
        except queue.Full:
            if command.operation_id:
                self.store.operation_update(command.operation_id, 'failed', {'error': 'browser_queue_full'})
            return 409, {'error': 'browser_queue_full'}
        if command.operation_id:
            return 202, self.store.operation(command.operation_id)
        if not command.ready.wait(timeout):
            command.cancelled = True
            return 504, {'error': 'browser_command_timeout'}
        return command.result

    def handle_http(self, handler):
        """Return True if /rpa handled; use only from existing localhost handler."""
        path = urlsplit(handler.path).path
        if not path.startswith('/rpa/'):
            return False
        try:
            size = int(handler.headers.get('Content-Length', '0'))
            if not 0 <= size <= MAX_BODY:
                raise ValueError()
            raw = handler.rfile.read(size)
            body = json.loads(raw) if raw else {}
        except (ValueError, UnicodeDecodeError):
            handler._json(400, {'error': 'invalid_body'}); return True
        code, value = self.request(handler.command, path, body, handler.headers.get('Authorization'))
        handler._json(code, value)
        return True

    def _guard_control(self, *, observation=False):
        ctl = self.store.control()
        if ctl['manual']:
            raise BrowserError('manual_browser_owner')
        if ctl['paused'] and not observation:
            raise BrowserError('browser_paused')

    def sample_status(self, page):
        if self.profile.data.get('calibrated') is not True:
            # Connectivity is real; fixture selectors are not a live identity
            # probe. Do not query them against an operator's existing browser.
            self.cached.update(loggedIn=False, accountId='', captcha=False,
                               error='selectors_not_calibrated')
            return
        adapter = HHBrowserAdapter(page, self.profile, clock=self.clock)
        try:
            account = self.account_id or adapter.value(page, self.profile.data['identity']['accountId'])
            adapter.guard(account)
            self.cached.update(loggedIn=True, accountId=account, captcha=False, error='')
        except BrowserError as error:
            self.cached.update(loggedIn=False, captcha=error.code == 'captcha', error=error.code)
        except Exception:
            self.cached.update(loggedIn=False, error='page_unavailable')

    def poll(self, page):
        """Call on the Playwright owner thread at safe checkpoints only."""
        self._expire_prepared()
        try:
            command = self.commands.get_nowait()
        except queue.Empty:
            return False
        if command.cancelled:
            command.result = (409, {'error': 'command_cancelled'}); command.ready.set(); return True
        command.started, self.owner = True, command.action
        try:
            if command.operation_id:
                self.store.operation_update(command.operation_id, 'running')
            adapter = HHBrowserAdapter(page, self.profile, clock=self.clock,
                checkpoint=lambda: self._guard_control(observation=command.action == 'observe'))
            result = self._execute(adapter, command.action, command.body)
            command.result = (200, result)
        except BrowserError as error:
            if error.pause:
                self.store.pause(error.code)
            command.result = (409, {'error': error.code})
        except Exception:
            self.store.pause('browser_operation_failed')
            command.result = (502, {'error': 'browser_operation_failed'})
        finally:
            if command.operation_id:
                self.store.operation_update(command.operation_id,
                    'completed' if command.result[0] == 200 else 'failed', command.result[1])
            self.owner = None
            self.sample_status(page)
            command.ready.set()
        return True

    def _execute(self, adapter, action, body):
        if action == 'acknowledge':
            self.store.acknowledge(identifier(body.get('attemptId')))
            return {'acknowledged': True, 'retryAllowed': False}
        if action == 'manual':
            if type(body.get('enabled')) is not bool:
                raise BrowserError('invalid_manual_state')
            if body['enabled'] and self.prepared:
                raise BrowserError('prepared_send_holds_browser')
            self.store.manual(body['enabled'])
            return self.status()
        if action == 'resume':
            if self.store.control()['manual']:
                raise BrowserError('manual_browser_owner')
            account = self.account_id or adapter.value(adapter.page, self.profile.data['identity']['accountId'])
            adapter.guard(account)
            self.store.resume()
            return self.status()
        self._guard_control(observation=action == 'observe')
        if action == 'discover':
            if self.profile.data.get('calibrated') is not True:
                raise BrowserError('selectors_not_calibrated')
            if self.prepared:
                raise BrowserError('prepared_send_holds_browser')
            return adapter.discover(body.get('source', {}))
        if action == 'prepare':
            if self.prepared:
                raise BrowserError('prepared_send_holds_browser')
            payload = adapter.prepare(body.get('target', {}), body.get('message'))
            self.prepared = {'id': secrets.token_hex(16), 'expires': self.clock() + 90, 'payload': payload}
            return {'preparedId': self.prepared['id'], 'expiresAt': self.prepared['expires'],
                    'messageHash': payload['messageHash'], 'target': payload['target']}
        attempt_id = identifier(body.get('attemptId'))
        previous = self.store.get(attempt_id)
        if action == 'observe':
            if previous is None:
                return {'outcome': 'unknown', 'reason': 'attempt_not_found'}
            if previous['state'] == 'sent_observed':
                return previous['result']
            if previous['state'] == 'abandoned' and body.get('recheck') is not True:
                return {'outcome': 'unknown', 'reason': 'operator_acknowledged', 'acknowledged': True}
            if self.prepared:
                raise BrowserError('prepared_send_holds_browser')
            result = adapter.observe(previous['payload'])
            self.store.finish(attempt_id, result)
            return result
        if action != 'send':
            raise BrowserError('unknown_bridge_command')
        # Repeated send request cannot click again even when a client timed out.
        if previous is not None:
            if previous['prepared_id'] != body.get('preparedId'):
                raise BrowserError('attempt_binding_mismatch')
            return previous['result'] or {'outcome': 'unknown', 'reason': 'attempt_already_started'}
        prepared = self.prepared
        if not prepared or prepared['id'] != body.get('preparedId') or prepared['expires'] <= self.clock():
            raise BrowserError('prepared_send_expired')
        if body.get('campaignStarted') is not True:
            raise BrowserError('campaign_start_required')
        payload = prepared['payload']
        self.store.start(attempt_id, prepared['id'], payload)
        self.prepared = None
        try:
            self._guard_control()
            adapter.click_send(payload, checkpoint=self._guard_control)
            adapter.wait_for_ack(payload)
            result = adapter.observe(payload)
        except Exception:
            result = {'outcome': 'unknown', 'reason': 'send_outcome_uncertain'}
        self.store.finish(attempt_id, result)
        return result
