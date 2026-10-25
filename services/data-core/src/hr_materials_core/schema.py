"""Typed data ownership only. Existing catalogues migrate explicitly, offline."""
import json
import sqlite3

from materials_repository import _require


SOURCES_V2 = """CREATE TABLE media_sources (
    id TEXT PRIMARY KEY, interview_id TEXT REFERENCES cp_interviews(id),
    meeting_id TEXT REFERENCES cp_meetings(id), intake_id TEXT REFERENCES em_intake_candidates(id),
    provider TEXT NOT NULL, connection_id TEXT NOT NULL, capture_identity TEXT NOT NULL,
    parent_source_id TEXT REFERENCES media_sources(id), source_ref TEXT NOT NULL,
    storage_policy TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1 CHECK(generation>0),
    state TEXT NOT NULL DEFAULT 'pending', metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    CHECK((interview_id IS NOT NULL)+(meeting_id IS NOT NULL)+(intake_id IS NOT NULL)=1),
    UNIQUE(provider,connection_id,capture_identity))"""


def is_v2(db):
    return any(r[2] == 'em_intake_candidates' and r[3] == 'intake_id'
               for r in db.execute('PRAGMA foreign_key_list(media_sources)'))


def ensure_sources_v2(db):
    _require(db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='em_intake_candidates'").fetchone(),
             'hr_intake_schema_required', 503)
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='media_sources'").fetchone():
        db.execute(SOURCES_V2)
    _require(is_v2(db), 'catalogue_migration_required', 503)


def migrate(connect, *, checkpoint=lambda stage: None):
    """Caller stops writers and takes a backup first. Never called at startup.

    SQLite's documented rebuild keeps child FK targets unchanged. DDL rollback
    restores the old catalogue on any failure, including between DROP/RENAME.
    """
    db = connect()
    try:
        _require(not db.in_transaction, 'migration_transaction_forbidden', 409)
        if is_v2(db):
            return False
        _require(db.execute("SELECT 1 FROM sqlite_schema WHERE name='em_intake_candidates' AND type='table'").fetchone(),
                 'hr_intake_schema_required', 503)
        _require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'existing_foreign_key_violation', 409)
        columns = [r[1] for r in db.execute('PRAGMA table_info(media_sources)')]
        expected = {'id','interview_id','meeting_id','provider','connection_id','capture_identity',
                    'parent_source_id','source_ref','storage_policy','generation','state','metadata','created_at','updated_at'}
        _require(set(columns) == expected, 'unknown_catalogue_schema', 409)
        db.execute('PRAGMA foreign_keys=OFF')
        _require(db.execute('PRAGMA foreign_keys').fetchone()[0] == 0, 'migration_fk_disable_failed')
        db.execute('BEGIN IMMEDIATE')
        quoted = ','.join('"' + c + '"' for c in columns)
        before = list(db.execute('SELECT ' + quoted + ' FROM media_sources ORDER BY id'))
        objects = list(db.execute("""SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL
            AND (type IN ('view','trigger') OR (type='index' AND tbl_name='media_sources'))"""))
        # All views are restored verbatim; an indirect dependent view must not
        # remain dangling while SQLite validates the table rename.
        for kind, name, _, _ in objects:
            db.execute('DROP ' + kind.upper() + ' "' + name.replace('"','""') + '"')
        db.execute(SOURCES_V2.replace('CREATE TABLE media_sources (', 'CREATE TABLE media_sources_new (', 1))
        db.execute('INSERT INTO media_sources_new (' + quoted + ') SELECT ' + quoted + ' FROM media_sources')
        checkpoint('copied')
        db.execute('DROP TABLE media_sources')
        checkpoint('dropped')
        db.execute('ALTER TABLE media_sources_new RENAME TO media_sources')
        checkpoint('renamed')
        for _, _, _, sql in objects:
            db.execute(sql)
        _require(before == list(db.execute('SELECT ' + quoted + ' FROM media_sources ORDER BY id')), 'migration_row_mismatch')
        _require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'migration_foreign_key_violation')
        _require(is_v2(db), 'migration_schema_mismatch')
        db.commit()
        return True
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA foreign_keys=ON')
        _require(db.execute('PRAGMA foreign_keys').fetchone()[0] == 1, 'migration_fk_restore_failed')
        db.close()


if __name__ == '__main__':
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument('database')
    parser.add_argument('--writers-stopped-and-backup-verified', action='store_true', required=True)
    args = parser.parse_args()
    path = Path(args.database).resolve()
    changed = migrate(lambda: sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=30))
    print(json.dumps({'migrated': changed, 'schema': 2}))
