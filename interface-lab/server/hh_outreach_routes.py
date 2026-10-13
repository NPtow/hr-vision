"""Operator and separately authenticated worker endpoints for browser outreach."""
import hmac
from urllib.parse import urlsplit

from domain import Problem
from hh_outreach import identifier, require

PREFIX = '/api/hr/outreach/'
WORKER_PREFIX = '/api/hr/outreach-worker/'
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}


class HHOutreachRoutes:
    def __init__(self, engine, *, worker_secret=''):
        self.engine = engine
        self.worker_secret = worker_secret

    @staticmethod
    def handles(path):
        clean = urlsplit(path).path
        return clean.startswith(PREFIX) or clean.startswith(WORKER_PREFIX)

    def authorize(self, manager_actor):
        with self.engine.db() as db:
            actor = manager_actor(db)
        require(actor == 'manager', 'Доступ только для команды HR Vision.', 403)
        return actor

    def authorize_worker(self, headers):
        authorization = next((value for key, value in headers.items() if key.lower() == 'authorization'), '')
        require(bool(self.worker_secret) and isinstance(authorization, str)
                and hmac.compare_digest(authorization, 'Bearer ' + self.worker_secret), 'worker_unauthorized', 403)

    def handle(self, method, raw_path, headers, body, manager_actor):
        parsed = urlsplit(raw_path)
        require(not parsed.query, 'query_not_supported')
        require(method in ('GET', 'POST'), 'method_not_allowed', 405)
        data = body if isinstance(body, dict) else {}
        if parsed.path.startswith(WORKER_PREFIX):
            self.authorize_worker(headers)
            require(method == 'POST', 'method_not_allowed', 405)
            action = parsed.path[len(WORKER_PREFIX):].strip('/')
            if action == 'claim':
                result = {'job': self.engine.claim(data.get('accountId'), data.get('workerId'))}
            elif action == 'start':
                result = self.engine.start(data.get('jobId'), data.get('claimToken'))
            elif action == 'result':
                result = self.engine.result(data.get('jobId'), data.get('claimToken'), data.get('outcome'),
                                            reason=data.get('reason'), evidence=data.get('evidence'))
            elif action == 'reconcile':
                result = self.engine.reconcile(data.get('jobId'), data.get('evidence'))
            else:
                raise Problem('outreach_route_not_found', 404)
            return result, dict(HEADERS)
        require(parsed.path.startswith(PREFIX), 'outreach_route_not_found', 404)
        self.authorize(manager_actor)
        # JSON + existing server owner auth is required. Never trust actor from body.
        require(next((v for k, v in headers.items() if k.lower() == 'sec-fetch-site'), '') != 'cross-site', 'cross_site_denied', 403)
        parts = parsed.path[len(PREFIX):].strip('/').split('/')
        if parts == ['campaigns'] and method == 'GET':
            result = {'campaigns': self.engine.campaigns(), 'sendingEnabled': bool(self.engine.sending_enabled)}
        elif parts == ['campaigns'] and method == 'POST':
            result = {'campaign': self.engine.create_campaign(data)}
        elif len(parts) == 2 and parts[0] == 'campaigns' and method == 'GET':
            result = self.engine.detail(identifier(parts[1]))
        elif len(parts) == 3 and parts[0] == 'campaigns' and method == 'POST':
            campaign = identifier(parts[1])
            action = parts[2]
            if action in ('pause', 'resume', 'cancel'):
                result = {'campaign': self.engine.control(campaign, action)}
            elif action == 'import':
                result = self.engine.import_recipients(campaign, data.get('recipients'), cursor=data.get('cursor'))
            elif action == 'queue':
                result = self.engine.queue(campaign, data.get('recipientIds'))
            else:
                raise Problem('outreach_route_not_found', 404)
        elif parts == ['accounts', 'unblock'] and method == 'POST':
            result = self.engine.unblock_account(data.get('accountId'))
        elif parts == ['jobs', 'retry-blocked'] and method == 'POST':
            result = self.engine.retry_blocked(data.get('jobId'))
        else:
            raise Problem('outreach_route_not_found', 404)
        return result, dict(HEADERS)
