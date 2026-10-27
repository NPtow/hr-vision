"""Produce an inspectable patch; never write to or restart the existing worker.

python hh_browser/integration.py /path/to/hh-daily/worker.py --output /tmp/worker.rpa.py
diff -u /path/to/worker.py /tmp/worker.rpa.py
"""
import argparse
import ast
from pathlib import Path


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Worker source changed: required integration anchor is not unique')
    return source.replace(old, new, 1)


def integrate(source):
    if 'WorkerBridge' in source:
        raise ValueError('Already integrated; inspect the existing integration instead')
    source = replace_once(source, 'import alert\n', 'import alert\nfrom hh_browser import WorkerBridge\n')
    source = replace_once(source, 'ACCOUNT = os.environ.get("HH_ACCOUNT", "default")',
        'ACCOUNT = os.environ.get("HH_ACCOUNT", "default")\nRPA = WorkerBridge.from_env()')
    source = replace_once(source,
        '        page.goto(START_URL, wait_until="domcontentloaded"); time.sleep(2)',
        '        # Manual mode/pause survives restart; do not discard a restored draft.\n'
        '        if RPA is None or RPA.legacy_allowed():\n'
        '            page.goto(START_URL, wait_until="domcontentloaded"); time.sleep(2)')
    source = replace_once(source,
        '        last_check = time.time()\n        while True:\n            try:\n                job = JOBS.get(timeout=5)',
        '        last_check = time.time()\n'
        '        if RPA: RPA.sample_status(page)\n'
        '        rpa_last_sample = time.time()\n'
        '        while True:\n'
        '            if RPA:\n'
        '                if RPA.poll(page): continue\n'
        '                if time.time() - rpa_last_sample > 15:\n'
        '                    RPA.sample_status(page); rpa_last_sample = time.time()\n'
        '                if not RPA.legacy_allowed():\n'
        '                    time.sleep(0.1); continue\n'
        '            try:\n'
        '                job = JOBS.get(timeout=0.2 if RPA else 5)')
    source = replace_once(source,
        '                if time.time() - last_check > 900:',
        '                if time.time() - last_check > 900 and (RPA is None or RPA.begin_legacy()):')
    source = replace_once(source,
        '                    except Exception as e: S["error"] = str(e)[:200]\n                    last_check = time.time()',
        '                    except Exception as e: S["error"] = str(e)[:200]\n'
        '                    finally:\n'
        '                        if RPA: RPA.end_legacy()\n'
        '                    last_check = time.time()')
    source = replace_once(source,
        '            reload_extract()\n            try:\n                if job.get("debug"):',
        '            if RPA and not RPA.begin_legacy():\n'
        '                JOBS.put(job); continue\n'
        '            reload_extract()\n            try:\n                if job.get("debug"):')
    source = replace_once(source,
        '                S.update(job=None, progress="", error=str(e)[:300]); log(f"ошибка задачи: {e}")',
        '                S.update(job=None, progress="", error=str(e)[:300]); log(f"ошибка задачи: {e}")\n'
        '            finally:\n'
        '                if RPA: RPA.end_legacy()')
    source = replace_once(source,
        '    def do_GET(self):\n        u = urlparse(self.path)',
        '    def do_GET(self):\n'
        '        if RPA and RPA.handle_http(self): return\n'
        '        u = urlparse(self.path)')
    source = replace_once(source,
        '    def do_POST(self):\n        u = urlparse(self.path); q = parse_qs(u.query)',
        '    def do_POST(self):\n'
        '        if RPA and RPA.handle_http(self): return\n'
        '        u = urlparse(self.path); q = parse_qs(u.query)')
    ast.parse(source)
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve() or args.output.exists():
        parser.error('Use a new output file, never the original worker')
    args.output.write_text(integrate(args.source.read_text()), encoding='utf-8')
