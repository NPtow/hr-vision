"""Durable closure of cancelled rooms, including candidate-only early entry.

The status change and cleanup intent commit together. The provider is contacted
outside SQLite and under the same per-meeting lock as join/finish.
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
    ''')


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
                    room = json.loads(row[0])
                portal.media.finish(room)
                with portal.connect() as db:
                    db.execute('''UPDATE cp_call_cleanup SET closed_at=?
                      WHERE meeting_id=? AND room=? AND closed_at IS NULL''', (time.time(), mid, row[0]))
        except Exception as error:
            observability.capture_exception(error, "worker.call_cleanup")
            with portal.connect() as db:
                db.execute('''UPDATE cp_call_cleanup SET attempts=attempts+1,
                  next_attempt=?+MIN(300,2 << MIN(attempts,7))
                  WHERE meeting_id=? AND closed_at IS NULL''', (time.time(), mid))
            # Never log provider response bodies, participant names or tokens.
            log.warning('call_cleanup_retry meeting_id=%s error=%s', mid, type(error).__name__)


def start_worker(portal):
    def run():
        while True:
            try:
                process_pending(portal)
            except Exception as error:
                observability.capture_exception(error, "worker.call_cleanup")
                log.error('call_cleanup_worker_error error=%s', type(error).__name__)
            time.sleep(2)
    thread = threading.Thread(target=run, name='call-cleanup', daemon=True)
    thread.start()
    return thread
