"""Durable cancelled-room cleanup and read-only native Zoom end reconciliation.

The status change and cleanup intent commit together. The provider is contacted
outside SQLite and under the same per-meeting lock as join/finish. Native Zoom
end observations never send an end command, including after a browser closes.
"""
import observability
import json
import logging
import threading
import time


log = logging.getLogger(__name__)


def init(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS cp_call_cleanup (
        meeting_id TEXT PRIMARY KEY REFERENCES cp_meetings(id),
        room TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL DEFAULT 0, closed_at REAL);
      CREATE TRIGGER IF NOT EXISTS cp_cancelled_call_cleanup
      AFTER UPDATE OF status ON cp_meetings
      WHEN NEW.status IN ('cancelled','declined') AND NEW.room IS NOT NULL
           AND OLD.status NOT IN ('cancelled','declined')
      BEGIN
        INSERT OR IGNORE INTO cp_call_cleanup(meeting_id,room) VALUES (NEW.id,NEW.room);
      END;
      INSERT OR IGNORE INTO cp_call_cleanup(meeting_id,room)
        SELECT id,room FROM cp_meetings
        WHERE status IN ('cancelled','declined') AND room IS NOT NULL;
      CREATE TABLE IF NOT EXISTS cp_zoom_lifecycle_checks (
        meeting_id TEXT PRIMARY KEY, next_attempt REAL NOT NULL DEFAULT 0);
    ''')


def _recover_zoom_session(portal, mid, room_body):
    """Pin a missed first session without allowing a stale job to change owners."""
    room = json.loads(room_body)
    if room.get('provider') != 'zoom' or room.get('sessionPinned') is True:
        return room_body
    state = portal.calls.invoke(portal.calls.for_room(room).get_state, room)
    if state.get('status') not in ('started', 'ended'):
        return room_body  # Waiting/unknown never proves an instance to pin.
    session = state.get('providerSessionId')
    if not isinstance(session, str) or not session or any(c.isspace() for c in session):
        raise ValueError('cleanup session proof missing')
    pinned = json.dumps({**room, 'meetingSessionId': session, 'sessionPinned': True})
    with portal.lock, portal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('''SELECT c.room,m.status,m.room FROM cp_call_cleanup c
          JOIN cp_meetings m ON m.id=c.meeting_id
          WHERE c.meeting_id=? AND c.closed_at IS NULL''', (mid,)).fetchone()
        if (current is None or current[0] != room_body or current[2] != room_body
                or current[1] not in ('cancelled', 'declined')):
            raise ValueError('cleanup room identity changed')
        choice = db.execute('''SELECT provider,state,room FROM cp_call_provider_choices
          WHERE meeting_id=?''', (mid,)).fetchone()
        # The provider reservation and portal use different JSON serializers.
        # Compare the complete decoded binding, then guard the actual stored
        # bytes on update; comparing only a reusable Zoom number is insufficient.
        if (choice is None or choice[0] != 'zoom' or choice[1] != 'ready'
                or not choice[2] or json.loads(choice[2]) != room):
            raise ValueError('cleanup provider binding changed')
        changed = db.execute('''UPDATE cp_call_provider_choices SET room=?
          WHERE meeting_id=? AND provider='zoom' AND state='ready' AND room=?''',
          (pinned, mid, choice[2])).rowcount
        if changed != 1:
            raise ValueError('cleanup provider binding changed')
        db.execute('UPDATE cp_meetings SET room=? WHERE id=?', (pinned, mid))
        db.execute('UPDATE cp_call_cleanup SET room=? WHERE meeting_id=?', (pinned, mid))
    return pinned


def process_pending(portal, *, limit=5):
    """Retry persisted intent after restarts; never close an active/replaced room."""
    with portal.connect() as db:
        mids = [row[0] for row in db.execute('''
          SELECT meeting_id FROM cp_call_cleanup WHERE closed_at IS NULL AND next_attempt<=?
          ORDER BY next_attempt,meeting_id LIMIT ?''', (time.time(), limit))]
    for mid in mids:
        try:
            with portal.media_operation(mid):
                with portal.connect() as db:
                    row = db.execute('''
                      SELECT c.room,c.attempts,m.status,m.room FROM cp_call_cleanup c
                      JOIN cp_meetings m ON m.id=c.meeting_id
                      WHERE c.meeting_id=? AND c.closed_at IS NULL AND c.next_attempt<=?''',
                                     (mid, time.time())).fetchone()
                    if row is None:
                        continue
                    if row[2] not in ('cancelled', 'declined') or row[0] != row[3]:
                        # Keep the job for investigation; never infer permission
                        # to close a replacement room from an old cancellation.
                        raise ValueError('cleanup room identity changed')
                room_body = _recover_zoom_session(portal, mid, row[0])
                room = json.loads(room_body)
                portal.calls.finish(room)
                with portal.connect() as db:
                    db.execute('''UPDATE cp_call_cleanup SET closed_at=?
                      WHERE meeting_id=? AND room=? AND closed_at IS NULL''', (time.time(), mid, room_body))
        except Exception as error:
            observability.capture_exception(error, "worker.call_cleanup")
            with portal.connect() as db:
                db.execute('''UPDATE cp_call_cleanup SET attempts=attempts+1,
                  next_attempt=?+MIN(300,2 << MIN(attempts,7))
                  WHERE meeting_id=? AND closed_at IS NULL''', (time.time(), mid))
            # Never log provider response bodies, participant names or tokens.
            log.warning('call_cleanup_retry meeting_id=%s error=%s', mid, type(error).__name__)


def process_native_zoom_ends(portal, *, limit=5, interval_seconds=30):
    """Recover native ends after a closed browser; read provider state only.

    Persisted due times keep an ongoing/failed first call from starving later
    meetings. A worker restart does not reset the poll budget or create rooms.
    """
    if type(limit) is not int or not 1 <= limit <= 20 or not 5 <= interval_seconds <= 3600:
        raise ValueError('Invalid Zoom lifecycle poll bounds')
    tick = time.time()
    with portal.connect() as db:
        pending = "m.status='live'"
        if portal.materials_catalog is not None and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_sources'").fetchone():
            pending += """ OR (m.status='completed' AND NOT EXISTS (
              SELECT 1 FROM media_sources s WHERE s.provider='zoom' AND
                ((m.application_id IS NULL AND s.interview_id=m.id)
                 OR (m.application_id IS NOT NULL AND s.meeting_id=m.id))))"""
        rows = db.execute('''SELECT m.id,m.candidate_id,m.application_id FROM (
          SELECT id,candidate_id,NULL AS application_id,room,status FROM cp_interviews WHERE status IN ('live','completed')
          UNION ALL
          SELECT m.id,a.candidate_id,m.application_id,m.room,m.status FROM cp_meetings m
            JOIN cp_applications a ON a.id=m.application_id WHERE m.status IN ('live','completed')
        ) m LEFT JOIN cp_zoom_lifecycle_checks c ON c.meeting_id=m.id
        WHERE m.room IS NOT NULL AND json_extract(m.room,'$.provider')='zoom'
          AND COALESCE(c.next_attempt,0)<=? AND (''' + pending + ''')
        ORDER BY COALESCE(c.next_attempt,0),m.id LIMIT ?''', (tick, limit)).fetchall()
    results = []
    for mid, cid, application_id in rows:
        with portal.connect() as db:
            claimed = db.execute('''INSERT INTO cp_zoom_lifecycle_checks VALUES (?,?)
              ON CONFLICT(meeting_id) DO UPDATE SET next_attempt=excluded.next_attempt
              WHERE cp_zoom_lifecycle_checks.next_attempt<=?''',
                (mid, tick + interval_seconds, tick)).rowcount
        if not claimed:
            continue
        try:
            result = portal.reconcile_zoom_end(cid, application_id, mid)
            results.append({'meetingId': mid, 'completed': result['completed']})
        except Exception as error:
            observability.capture_exception(error, 'worker.zoom_native_end')
            log.warning('zoom_native_end_retry meeting_id=%s error=%s', mid, type(error).__name__)
            results.append({'meetingId': mid, 'completed': False, 'retry': True})
    return results


def start_worker(portal):
    def run():
        while True:
            try:
                process_pending(portal)
                process_native_zoom_ends(portal)
            except Exception as error:
                observability.capture_exception(error, "worker.call_cleanup")
                log.error('call_cleanup_worker_error error=%s', type(error).__name__)
            time.sleep(2)
    thread = threading.Thread(target=run, name='call-cleanup', daemon=True)
    thread.start()
    return thread
