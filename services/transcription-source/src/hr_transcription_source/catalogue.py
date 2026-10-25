"""External opt-in watcher of authorized Core facts; never a bucket scanner."""
import math
import re

from .repository import Conflict
from .worker import Rejected, Retryable

IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{1,128}$')


class Catalogue:
    def __init__(self, runtime):
        self.runtime, self.settings = runtime, runtime.settings

    def sweep(self):
        settings, repo = self.settings, self.runtime.repository
        if not settings.auto_enabled:
            return {'enabled':False}
        caller = settings.auto_caller
        state = repo.catalogue(caller,settings.auto_activated_at)
        accepted = skipped = 0
        for _ in range(settings.auto_pages_per_tick):
            params = {'created_since':state['activation_floor'],'limit':settings.auto_page_size}
            if state['cursor'] is not None:
                params['cursor'] = state['cursor']
            page,_ = self.runtime._json(self.runtime.core,'GET','/v1/assets',settings.callers[caller].core_read_token,params=params)
            items, cursor = page.get('items'), page.get('next_cursor')
            if (not isinstance(items,list) or len(items)>settings.auto_page_size or
                    cursor is not None and (not isinstance(cursor,str) or not cursor or len(cursor)>4096 or cursor==state['cursor'])):
                raise Retryable('invalid_catalogue_page')
            for item in items:
                event = self.event(item,state['activation_floor'])
                if event is None:
                    skipped += 1
                    continue
                enrolled = repo.enrolled(caller,event['asset_id'])
                if enrolled:
                    if settings.select_primary and not repo.get(enrolled)['selection_requested']:
                        job=repo.select_enrolled(caller,event['asset_id'])
                        self.runtime.enqueue(job['id'])
                    skipped += 1
                    continue
                try:
                    # Repeat active owner/source authorization immediately before
                    # durable enrollment. Core checks again on receipt/selection.
                    self.runtime.authorize(caller,event)
                    job,created = repo.submit(caller,'auto:'+event['asset_id'],event,settings.config_identity,
                        select_primary=settings.select_primary,automatic=True)
                    self.runtime.enqueue(job['id'])
                    accepted += int(created)
                    skipped += int(not created)
                except Rejected as error:
                    if error.status in (401,403) or error.status>=500:
                        raise
                    # A revoked/stale item is absent next sweep; no paid job.
                    skipped += 1
                except Conflict:
                    skipped += 1
            # Commit cursor only after the complete page has durable jobs. A
            # crash mid-page replays IDs and their enrollments, not paid POSTs.
            repo.catalogue_cursor(caller,state['activation_floor'],state['cursor'],cursor)
            state['cursor'] = cursor
            if cursor is None:
                # Next tick restarts from the immutable floor, catching a late
                # pending->ready transition behind this sweep's keyset cursor.
                break
        return {'enabled':True,'accepted':accepted,'skipped':skipped}

    def event(self, item, floor):
        if not isinstance(item,dict):
            raise Retryable('invalid_catalogue_item')
        event = {key:item.get(key) for key in ('owner_kind','owner_id','binding_id','generation','asset_id')}
        stamp = item.get('created_at')
        if (event['owner_kind'] not in ('interview','meeting','intake') or
                any(not isinstance(event[key],str) or not IDENTIFIER.fullmatch(event[key]) for key in ('owner_id','binding_id','asset_id')) or
                type(event['generation']) is not int or event['generation']<=0 or
                type(stamp) not in (int,float) or not math.isfinite(stamp)):
            raise Retryable('invalid_catalogue_item')
        if (stamp < floor or item.get('kind') not in ('video','audio') or item.get('state')!='ready' or
                item.get('storage_policy')!='owned' or
                (item.get('provider'),item.get('connection_id')) not in self.settings.source_tokens):
            return None
        return event
